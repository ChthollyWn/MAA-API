"""M86：``0041_nav_graph`` 迁移验收（spec §4.1、§5 T1、§6）。

测试纪律与其它迁移验收用例一致：全程用 ``tmp_path`` 的临时库，只用 Alembic 的
``upgrade`` / ``downgrade`` 建库与演进，绝不碰 ``resource/maa_api.db``。

覆盖四件事：

1. 空库升级到 0041：``nav_node`` / ``nav_edge`` 的列、可空性、唯一索引与两条
   ``CASCADE`` 外键逐项照 spec §4.1；两个 JSON 列的 server_default 落在 DDL 里；
2. 必填列齐了就能插；同 ``(from_node, to_node, action_hash)`` 的第二条边被唯一
   索引拒绝（``sqlite3.IntegrityError``）；
3. 降级回 0040 的形状：两表与唯一索引消失，0040 的表还在；再升级可重放且是空表；
4. 离线 ``--sql`` 能渲染升级与降级，且不建库文件、降级按反序删表。
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0040_question_expiry_historical"
HEAD_REVISION = "0041_nav_graph"
TIMESTAMP = "2026-11-09 08:00:00"

#: 列名 → (DDL 类型, 是否 NOT NULL)。顺序与 spec §4.1 一致。
EXPECTED_COLUMNS = {
    "nav_node": {
        "id": ("VARCHAR(48)", True),
        "title": ("VARCHAR(64)", True),
        "kind": ("VARCHAR(24)", True),
        "exemplars": ("JSON", True),
        "recognizer": ("JSON", True),
        "origin": ("VARCHAR(16)", True),
        "created_at": ("DATETIME", True),
        "updated_at": ("DATETIME", True),
    },
    "nav_edge": {
        "id": ("VARCHAR(36)", True),
        "from_node": ("VARCHAR(48)", True),
        "to_node": ("VARCHAR(48)", True),
        "action": ("JSON", True),
        "action_hash": ("VARCHAR(64)", True),
        "cost_ms": ("INTEGER", False),
        "success_count": ("INTEGER", True),
        "fail_count": ("INTEGER", True),
        "last_used_at": ("DATETIME", False),
        "origin": ("VARCHAR(16)", True),
        "enabled": ("BOOLEAN", True),
        "created_at": ("DATETIME", True),
        "updated_at": ("DATETIME", True),
    },
}

#: 索引名 → (表, 列序, 是否唯一)。
EXPECTED_INDEXES = {
    "uq_nav_edge_from_node_to_node_action_hash": (
        "nav_edge",
        ("from_node", "to_node", "action_hash"),
        True,
    ),
}

#: 本地列 → (目标表, 目标列, on_delete)。两条都随节点删除。
EXPECTED_FOREIGN_KEYS = {
    "nav_edge": {
        "from_node": ("nav_node", "id", "CASCADE"),
        "to_node": ("nav_node", "id", "CASCADE"),
    },
}


def _config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _revision(conn: sqlite3.Connection) -> str:
    return conn.execute("select version_num from alembic_version").fetchone()[
        "version_num"
    ]


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool]]:
    return {
        row["name"]: (row["type"], bool(row["notnull"]))
        for row in conn.execute(f"pragma table_info({table})")
    }


def _indexes(
    conn: sqlite3.Connection, table: str
) -> dict[str, tuple[tuple[str, ...], bool]]:
    result: dict[str, tuple[tuple[str, ...], bool]] = {}
    for row in conn.execute(f"pragma index_list({table})"):
        if row["name"].startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            info["name"] for info in conn.execute(f"pragma index_info({row['name']})")
        )
        result[row["name"]] = (columns, bool(row["unique"]))
    return result


def _foreign_keys(
    conn: sqlite3.Connection, table: str
) -> dict[str, tuple[str, str, str]]:
    return {
        row["from"]: (row["table"], row["to"], row["on_delete"])
        for row in conn.execute(f"pragma foreign_key_list({table})")
    }


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type='table'")
    }


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "select sql from sqlite_master where type='table' and name=?", (table,)
    ).fetchone()
    assert row is not None and row["sql"], f"sqlite_master 里没有 {table}"
    return row["sql"].upper()


def _insert_node(conn: sqlite3.Connection, node_id: str) -> None:
    conn.execute(
        "insert into nav_node (id, title, kind, origin, created_at, updated_at)"
        " values (?, ?, 'screen', 'seed', ?, ?)",
        (node_id, f"界面 {node_id}", TIMESTAMP, TIMESTAMP),
    )


def _insert_edge(
    conn: sqlite3.Connection,
    *,
    edge_id: str,
    from_node: str,
    to_node: str,
    action_hash: str = "hash-1",
) -> None:
    conn.execute(
        "insert into nav_edge (id, from_node, to_node, action, action_hash,"
        " success_count, fail_count, origin, enabled, created_at, updated_at)"
        " values (?, ?, ?, '{}', ?, 0, 0, 'seed', 1, ?, ?)",
        (edge_id, from_node, to_node, action_hash, TIMESTAMP, TIMESTAMP),
    )


def test_upgrade_creates_nav_tables_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "nav_graph_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert {"nav_node", "nav_edge"} <= _table_names(conn)

        for table, expected in EXPECTED_COLUMNS.items():
            assert _columns(conn, table) == expected, table

        # 索引逐条核对：名字、列序、唯一性，多建或少建都失败。
        assert _indexes(conn, "nav_node") == {}
        assert _indexes(conn, "nav_edge") == {
            name: (columns, unique)
            for name, (index_table, columns, unique) in EXPECTED_INDEXES.items()
            if index_table == "nav_edge"
        }

        # 两条外键都在 nav_edge 上，且 ondelete 是 CASCADE。
        assert _foreign_keys(conn, "nav_node") == {}
        assert _foreign_keys(conn, "nav_edge") == EXPECTED_FOREIGN_KEYS["nav_edge"]
        edge_sql = _table_sql(conn, "nav_edge")
        for name in (
            "FK_NAV_EDGE_FROM_NODE_NAV_NODE",
            "FK_NAV_EDGE_TO_NODE_NAV_NODE",
            "PK_NAV_EDGE",
        ):
            assert name in edge_sql
        assert "ON DELETE CASCADE" in edge_sql
        assert "PK_NAV_NODE" in _table_sql(conn, "nav_node")

        # 两个 JSON 列的 server_default 落在 DDL 里（spec §4.1）。
        node_sql = _table_sql(conn, "nav_node")
        assert "DEFAULT '[]'" in node_sql
        assert "DEFAULT '{}'" in node_sql

        # 必填列齐了就能插；可空列与带默认值的列留空。
        _insert_node(conn, "home")
        _insert_node(conn, "infrast")
        _insert_edge(conn, edge_id="edge-1", from_node="home", to_node="infrast")
        conn.commit()
        node = conn.execute("select * from nav_node where id='home'").fetchone()
        assert node["exemplars"] == "[]"
        assert node["recognizer"] == "{}"
        edge = conn.execute("select * from nav_edge where id='edge-1'").fetchone()
        assert edge["cost_ms"] is None
        assert edge["last_used_at"] is None
        assert edge["success_count"] == 0
        assert edge["fail_count"] == 0
        assert edge["enabled"] == 1
    finally:
        conn.close()


def test_nav_edge_unique_conflict(tmp_path: Path) -> None:
    """同 from/to/action_hash 只能有一条边；换 action_hash 可以共存。"""
    db_path = tmp_path / "nav_graph_unique.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _insert_node(conn, "home")
        _insert_node(conn, "infrast")
        _insert_edge(conn, edge_id="edge-1", from_node="home", to_node="infrast")
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="nav_edge"):
            _insert_edge(conn, edge_id="edge-dup", from_node="home", to_node="infrast")
        conn.rollback()

        # 换动作哈希（同一对界面可以有多个动作不同的跳转）能插。
        _insert_edge(
            conn,
            edge_id="edge-2",
            from_node="home",
            to_node="infrast",
            action_hash="hash-2",
        )
        conn.commit()
        assert conn.execute("select count(*) from nav_edge").fetchone()[0] == 2
    finally:
        conn.close()


def test_downgrade_drops_tables_and_reupgrades_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "nav_graph_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _insert_node(conn, "home")
        _insert_node(conn, "infrast")
        _insert_edge(conn, edge_id="edge-1", from_node="home", to_node="infrast")
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert not {"nav_node", "nav_edge"} & _table_names(conn)
        # 0040 的表还在，降级只删本迁移的两张表。
        assert {"agent_question", "agent_session", "outbox", "ticket"} <= _table_names(
            conn
        )
        assert not set(EXPECTED_INDEXES) & {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type='index'"
            )
        }
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        for table in EXPECTED_COLUMNS:
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0
        # 重放后可以重新写入（纯新增迁移，不依赖旧数据）。
        _insert_node(conn, "home")
        conn.commit()
    finally:
        conn.close()


def test_offline_sql_renders_both_directions(tmp_path: Path) -> None:
    db_path = tmp_path / "nav_graph_offline.db"
    cfg = _config(db_path)

    # 离线模式输出的是 DDL 原文（``op.create_table`` 渲染成 ``CREATE TABLE``）。
    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE nav_node" in upgrade_text
    assert "CREATE TABLE nav_edge" in upgrade_text
    assert "id VARCHAR(48) NOT NULL" in upgrade_text
    assert "uq_nav_edge_from_node_to_node_action_hash" in upgrade_text
    assert "ON DELETE CASCADE" in upgrade_text
    assert "DEFAULT '[]'" in upgrade_text
    assert "DEFAULT '{}'" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    assert "DROP INDEX uq_nav_edge_from_node_to_node_action_hash" in downgrade_text
    assert "DROP TABLE nav_edge" in downgrade_text
    assert "DROP TABLE nav_node" in downgrade_text
    # 反序删表：先 nav_edge（有外键引用 nav_node），再 nav_node。
    assert downgrade_text.index("DROP TABLE nav_edge") < downgrade_text.index(
        "DROP TABLE nav_node"
    )
    assert not db_path.exists(), "离线模式不应创建数据库文件"
