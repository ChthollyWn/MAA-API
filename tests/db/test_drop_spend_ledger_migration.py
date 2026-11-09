"""维护性补丁：``0039_drop_spend_ledger`` 迁移验收。

测试纪律与 ``tests/db/test_channels_migration.py`` 一致：全程用 ``tmp_path`` 的
临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖三件事：

1. 空库升级到 head：``spend_ledger`` 与它的索引都消失，其它业务表还在；
2. 降级回到 0038：表、9 列、可空性、索引与三条 ``SET NULL`` 外键按
   ``0037_spend_ledger.py`` 的 DDL 原样重建，可插入账本行；再升级可重放并再次删表；
3. 离线 ``--sql`` 能渲染升级与降级，且不建库文件。
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0038_channels"
HEAD_REVISION = "0039_drop_spend_ledger"
TIMESTAMP = "2026-11-09 10:00:00"

#: 列名 → (DDL 类型, 是否 NOT NULL)。顺序与 0037 的 DDL 一致。
EXPECTED_COLUMNS = {
    "id": ("INTEGER", True),
    "pipeline_id": ("VARCHAR(36)", False),
    "task_id": ("VARCHAR(36)", False),
    "kind": ("VARCHAR(16)", True),
    "planned": ("INTEGER", True),
    "actual": ("INTEGER", False),
    "source": ("VARCHAR(16)", True),
    "ticket_id": ("VARCHAR(36)", False),
    "created_at": ("DATETIME", True),
}

#: 索引名 → (列序, 是否唯一)。
EXPECTED_INDEXES = {
    "ix_spend_ledger_kind_created_at": (("kind", "created_at"), False),
}

#: 本地列 → (目标表, 目标列, on_delete)。三条都只解除关联，不带走账本行。
EXPECTED_FOREIGN_KEYS = {
    "pipeline_id": ("pipeline", "id", "SET NULL"),
    "task_id": ("task", "id", "SET NULL"),
    "ticket_id": ("ticket", "id", "SET NULL"),
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


def _index_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type='index'")
    }


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "select sql from sqlite_master where type='table' and name=?", (table,)
    ).fetchone()
    assert row is not None and row["sql"], f"sqlite_master 里没有 {table}"
    return row["sql"].upper()


def _insert_ledger(conn: sqlite3.Connection) -> None:
    conn.execute(
        "insert into spend_ledger"
        " (kind, planned, actual, source, created_at)"
        " values (?, ?, ?, ?, ?)",
        ("medicine", 2, None, "agent", TIMESTAMP),
    )


def test_upgrade_drops_table_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "drop_spend_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "spend_ledger" not in _table_names(conn)
        assert "ix_spend_ledger_kind_created_at" not in _index_names(conn)
        # 只删本表：其它业务表（含 0038 新建的三张）都还在。
        assert {
            "pipeline",
            "task",
            "ticket",
            "watchdog_alert",
            "channel_binding",
            "outbox",
        } <= _table_names(conn)
    finally:
        conn.close()


def test_downgrade_rebuilds_table_and_upgrade_replays(tmp_path: Path) -> None:
    db_path = tmp_path / "drop_spend_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        # 按 0037 的 DDL 原样重建：列、索引、外键、自增主键都一致。
        assert _columns(conn, "spend_ledger") == EXPECTED_COLUMNS
        assert _indexes(conn, "spend_ledger") == EXPECTED_INDEXES
        assert _foreign_keys(conn, "spend_ledger") == EXPECTED_FOREIGN_KEYS
        table_sql = _table_sql(conn, "spend_ledger")
        assert "AUTOINCREMENT" in table_sql
        assert "PRIMARY KEY" in table_sql
        for name in (
            "FK_SPEND_LEDGER_PIPELINE_ID_PIPELINE",
            "FK_SPEND_LEDGER_TASK_ID_TASK",
            "FK_SPEND_LEDGER_TICKET_ID_TICKET",
        ):
            assert name in table_sql
        # 降级后可以插入账本行（表结构真的可用）。
        _insert_ledger(conn)
        conn.commit()
        assert conn.execute("select count(*) from spend_ledger").fetchone()[0] == 1
    finally:
        conn.close()

    # 再升级：表又被删掉；重复升降级可重放。
    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "spend_ledger" not in _table_names(conn)
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert "spend_ledger" in _table_names(conn)
        assert conn.execute("select count(*) from spend_ledger").fetchone()[0] == 0
    finally:
        conn.close()


def test_offline_render(tmp_path: Path) -> None:
    db_path = tmp_path / "drop_spend_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "DROP INDEX ix_spend_ledger_kind_created_at" in upgrade_text
    assert "DROP TABLE spend_ledger" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    assert "CREATE TABLE spend_ledger" in downgrade_text
    assert "AUTOINCREMENT" in downgrade_text
    assert "kind VARCHAR(16) NOT NULL" in downgrade_text
    assert "planned INTEGER NOT NULL" in downgrade_text
    assert "ix_spend_ledger_kind_created_at" in downgrade_text
    assert "ON DELETE SET NULL" in downgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"
