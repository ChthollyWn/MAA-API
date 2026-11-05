"""M85：``0038_channels`` 迁移验收（spec §4.1、§5 T1、§6）。

测试纪律与 ``tests/db/test_spend_ledger_migration.py`` 一致：全程用 ``tmp_path``
的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖四件事：

1. 空库升级到 0038，三张表的列、可空性、索引、唯一约束与两条 ``SET NULL``
   外键逐项照 03 §11.7；``outbox.id`` 在 DDL 里真的是
   ``INTEGER PRIMARY KEY AUTOINCREMENT``；
2. 必填列齐了就能插入，可空列留空；``channel_binding`` 三元组与
   ``outbox.dedupe_key`` 的唯一约束真实生效；
3. 降级回到 0037 的形状：三张表与两条索引消失，再升级可重放且是空表；
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
PREVIOUS_REVISION = "0037_spend_ledger"
HEAD_REVISION = "0038_channels"
TIMESTAMP = "2026-11-05 08:00:00"

#: 列名 → (DDL 类型, 是否 NOT NULL)。顺序与 03 §11.7 一致。
EXPECTED_COLUMNS = {
    "channel_binding": {
        "id": ("VARCHAR(36)", True),
        "platform": ("VARCHAR(16)", True),
        "external_user_id": ("VARCHAR(128)", True),
        "external_chat_id": ("VARCHAR(128)", True),
        "display_name": ("VARCHAR(64)", False),
        "status": ("VARCHAR(16)", True),
        "bound_at": ("DATETIME", True),
        "revoked_at": ("DATETIME", False),
        "last_seen_at": ("DATETIME", False),
    },
    "channel_bind_code": {
        "code": ("VARCHAR(16)", True),
        "platform": ("VARCHAR(16)", False),
        "expires_at": ("DATETIME", True),
        "used_at": ("DATETIME", False),
        "created_at": ("DATETIME", True),
    },
    "outbox": {
        "id": ("INTEGER", True),
        "channel": ("VARCHAR(24)", True),
        "target": ("VARCHAR(255)", True),
        "kind": ("VARCHAR(24)", True),
        "payload": ("JSON", True),
        "dedupe_key": ("VARCHAR(160)", True),
        "status": ("VARCHAR(16)", True),
        "attempts": ("INTEGER", True),
        "next_attempt_at": ("DATETIME", True),
        "last_error": ("TEXT", False),
        "receipt": ("JSON", False),
        "ref_run_id": ("VARCHAR(36)", False),
        "ref_ticket_id": ("VARCHAR(36)", False),
        "created_at": ("DATETIME", True),
        "sent_at": ("DATETIME", False),
    },
}

#: 索引名 → (表, 列序, 是否唯一)。绑定表的三元组唯一索引走显式 Index；
#: ``outbox.dedupe_key`` 是表级 UNIQUE 约束，SQLite 自己建 autoindex，不在此列。
EXPECTED_INDEXES = {
    "uq_channel_binding_platform_external_user_id_external_chat_id": (
        "channel_binding",
        ("platform", "external_user_id", "external_chat_id"),
        True,
    ),
    "ix_outbox_status_next_attempt_at": (
        "outbox",
        ("status", "next_attempt_at"),
        False,
    ),
}

#: 本地列 → (目标表, 目标列, on_delete)。两条都只解除关联，不带走发件箱行。
EXPECTED_FOREIGN_KEYS = {
    "outbox": {
        "ref_run_id": ("agent_run", "id", "SET NULL"),
        "ref_ticket_id": ("ticket", "id", "SET NULL"),
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


def _insert_binding(
    conn: sqlite3.Connection,
    *,
    binding_id: str = "binding-1",
    platform: str = "telegram",
    external_user_id: str = "user-1",
    external_chat_id: str = "chat-1",
    status: str = "active",
) -> None:
    conn.execute(
        "insert into channel_binding (id, platform, external_user_id,"
        " external_chat_id, status, bound_at) values (?, ?, ?, ?, ?, ?)",
        (binding_id, platform, external_user_id, external_chat_id, status, TIMESTAMP),
    )


def _insert_bind_code(
    conn: sqlite3.Connection,
    *,
    code: str = "ABCD2345",
    platform: str | None = "feishu",
    used_at: str | None = None,
) -> None:
    conn.execute(
        "insert into channel_bind_code (code, platform, expires_at, used_at,"
        " created_at) values (?, ?, ?, ?, ?)",
        (code, platform, TIMESTAMP, used_at, TIMESTAMP),
    )


def _insert_outbox(
    conn: sqlite3.Connection,
    *,
    dedupe_key: str = "reply:run-1",
    ref_run_id: str | None = None,
    ref_ticket_id: str | None = None,
) -> None:
    conn.execute(
        "insert into outbox (channel, target, kind, payload, dedupe_key, status,"
        " attempts, next_attempt_at, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "im_telegram",
            "chat-1",
            "reply",
            '{"text":"hi"}',
            dedupe_key,
            "pending",
            0,
            TIMESTAMP,
            TIMESTAMP,
        ),
    )


def test_upgrade_creates_channel_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "channels_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert {"channel_binding", "channel_bind_code", "outbox"} <= _table_names(conn)

        for table, expected in EXPECTED_COLUMNS.items():
            assert _columns(conn, table) == expected, table
            # 索引逐条核对：名字、列序、唯一性，多建或少建都失败。
            actual_indexes = _indexes(conn, table)
            expected_indexes = {
                name: (columns, unique)
                for name, (index_table, columns, unique) in EXPECTED_INDEXES.items()
                if index_table == table
            }
            assert actual_indexes == expected_indexes, table

        assert _indexes(conn, "channel_bind_code") == {}
        outbox_sql = _table_sql(conn, "outbox")
        assert "AUTOINCREMENT" in outbox_sql
        assert "PRIMARY KEY" in outbox_sql
        # dedupe_key 是表级 UNIQUE 约束，名字进 DDL；SQLite 另建 autoindex。
        assert "CONSTRAINT UQ_OUTBOX_DEDUPE_KEY UNIQUE (DEDUPE_KEY)" in outbox_sql
        assert _foreign_keys(conn, "outbox") == EXPECTED_FOREIGN_KEYS["outbox"]
        for name in (
            "FK_OUTBOX_REF_RUN_ID_AGENT_RUN",
            "FK_OUTBOX_REF_TICKET_ID_TICKET",
        ):
            assert name in outbox_sql

        # 必填列齐了就能插；可空列留空。
        _insert_binding(conn)
        _insert_binding(
            conn,
            binding_id="binding-2",
            external_user_id="user-2",
            external_chat_id="chat-2",
            status="revoked",
        )
        _insert_bind_code(conn)
        _insert_bind_code(conn, code="WXYZ6789", platform=None, used_at=TIMESTAMP)
        _insert_outbox(conn)
        conn.commit()

        binding = conn.execute("select * from channel_binding order by id").fetchone()
        assert binding["display_name"] is None
        assert binding["revoked_at"] is None
        assert binding["last_seen_at"] is None
        assert binding["bound_at"] == TIMESTAMP
        code = conn.execute(
            "select * from channel_bind_code where platform is null"
        ).fetchone()
        assert code["used_at"] == TIMESTAMP
        outbox = conn.execute("select * from outbox").fetchone()
        assert outbox["status"] == "pending"
        assert outbox["attempts"] == 0
        assert outbox["payload"] == '{"text":"hi"}'
        assert outbox["last_error"] is None
        assert outbox["receipt"] is None
        assert outbox["ref_run_id"] is None
        assert outbox["ref_ticket_id"] is None
        assert outbox["sent_at"] is None
    finally:
        conn.close()


def test_unique_constraints_are_enforced(tmp_path: Path) -> None:
    """绑定三元组唯一、outbox.dedupe_key 唯一；不同三元组/键可以共存。"""
    db_path = tmp_path / "channels_unique.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _insert_binding(conn)
        _insert_outbox(conn)
        conn.commit()

        # 同一 (platform, external_user_id, external_chat_id) 再插一次被拒。
        with pytest.raises(sqlite3.IntegrityError, match="channel_binding"):
            _insert_binding(conn, binding_id="binding-dup")
        conn.rollback()

        # 同一 dedupe_key 再插一次被拒。
        with pytest.raises(sqlite3.IntegrityError, match="outbox"):
            _insert_outbox(conn, dedupe_key="reply:run-1")
        conn.rollback()

        # 换 chat（同用户）或换 dedupe_key 都能插。
        _insert_binding(conn, binding_id="binding-3", external_chat_id="chat-3")
        _insert_outbox(conn, dedupe_key="reply:run-2")
        conn.commit()
        assert conn.execute("select count(*) from channel_binding").fetchone()[0] == 2
        assert conn.execute("select count(*) from outbox").fetchone()[0] == 2
    finally:
        conn.close()


def test_downgrade_drops_tables_and_upgrade_replays(tmp_path: Path) -> None:
    db_path = tmp_path / "channels_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _insert_binding(conn)
        _insert_bind_code(conn)
        _insert_outbox(conn)
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert not {"channel_binding", "channel_bind_code", "outbox"} & _table_names(conn)
        # 0037 的表还在，降级只删本迁移的三张表。
        assert {"spend_ledger", "watchdog_alert", "goal", "ticket"} <= _table_names(conn)
        assert not {
            "uq_channel_binding_platform_external_user_id_external_chat_id",
            "ix_outbox_status_next_attempt_at",
        } & {
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
        # 纯新增：重放是空表，id 从 1 开始。
        _insert_outbox(conn)
        conn.commit()
        assert conn.execute("select id from outbox").fetchone()["id"] == 1
    finally:
        conn.close()


def test_offline_render(tmp_path: Path) -> None:
    db_path = tmp_path / "channels_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE channel_binding" in upgrade_text
    assert "CREATE TABLE channel_bind_code" in upgrade_text
    assert "CREATE TABLE outbox" in upgrade_text
    assert "AUTOINCREMENT" in upgrade_text
    assert "target VARCHAR(255) NOT NULL" in upgrade_text
    assert "uq_channel_binding_platform_external_user_id_external_chat_id" in (
        upgrade_text
    )
    assert "uq_outbox_dedupe_key" in upgrade_text
    assert "ix_outbox_status_next_attempt_at" in upgrade_text
    assert "ON DELETE SET NULL" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    assert "DROP INDEX ix_outbox_status_next_attempt_at" in downgrade_text
    assert "DROP TABLE outbox" in downgrade_text
    assert "DROP TABLE channel_bind_code" in downgrade_text
    assert "DROP TABLE channel_binding" in downgrade_text
    # 反序删表：outbox → channel_bind_code → channel_binding。
    assert (
        downgrade_text.index("DROP TABLE outbox")
        < downgrade_text.index("DROP TABLE channel_bind_code")
        < downgrade_text.index("DROP TABLE channel_binding")
    )
    assert not db_path.exists(), "离线模式不应创建数据库文件"
