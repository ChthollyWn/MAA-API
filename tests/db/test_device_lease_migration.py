"""M71：``0027_device_lease`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``test_tickets_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，只用
Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰 ``resource/maa_api.db``。

两条断言：

1. 在 0026 上先写入 ``confirmation`` / ``agent_question`` / ``agent_audit`` 各一行，
   升级到 0027 后 ``device_lease`` 的列名、类型、可空与 §4.1 一致；三张旧表的新列
   在旧行上是 NULL；八个新外键的 ``ondelete`` 都是 SET NULL，batch 重建没有丢掉
   原有外键、索引与 ``AUTOINCREMENT``；第二行同 ``device_id`` 违反主键；
2. 降级回 0026 后新表与新列都不在、旧行逐列完好；再升级后新表回来且是空的。
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0026_tickets"
HEAD_REVISION = "0027_device_lease"

#: ``device_lease`` 列 → (DDL 类型, 是否可空)。顺序与 §4.1 一致。
EXPECTED_DEVICE_LEASE_COLUMNS = {
    "device_id": ("VARCHAR(32)", False),
    "lease_id": ("VARCHAR(36)", False),
    "ticket_id": ("VARCHAR(36)", True),
    "run_id": ("VARCHAR(36)", True),
    "holder_agent": ("VARCHAR(32)", False),
    "purpose": ("VARCHAR(120)", False),
    "explicit": ("BOOLEAN", False),
    "acquired_at": ("DATETIME", False),
    "expires_at": ("DATETIME", False),
    "heartbeat_at": ("DATETIME", False),
    "boot_id": ("VARCHAR(36)", False),
}

#: 三张旧表新增的列（追加在各自末尾，全部可空）。
EXPECTED_ORIGIN_COLUMNS = {
    "confirmation": ("origin_run_id", "origin_ticket_id", "origin_agent"),
    "agent_question": ("origin_run_id", "origin_ticket_id", "origin_agent"),
    "agent_audit": ("run_id", "ticket_id", "agent_name"),
}

#: 表名 → {(列, 目标表, ondelete)}；八个新外键，逐个 SET NULL。
EXPECTED_NEW_FOREIGN_KEYS = {
    "device_lease": {
        ("ticket_id", "ticket", "SET NULL"),
        ("run_id", "agent_run", "SET NULL"),
    },
    "confirmation": {
        ("origin_run_id", "agent_run", "SET NULL"),
        ("origin_ticket_id", "ticket", "SET NULL"),
    },
    "agent_question": {
        ("origin_run_id", "agent_run", "SET NULL"),
        ("origin_ticket_id", "ticket", "SET NULL"),
    },
    "agent_audit": {
        ("run_id", "agent_run", "SET NULL"),
        ("ticket_id", "ticket", "SET NULL"),
    },
}

#: batch 重建后原有外键必须原样保留（0026 时的完整集合）。
EXPECTED_FULL_FOREIGN_KEYS = {
    "confirmation": {("audit_id", "agent_audit", "SET NULL")},
    "agent_question": {
        ("session_id", "agent_session", "CASCADE"),
        ("audit_id", "agent_audit", "SET NULL"),
    },
    "agent_audit": {
        ("confirmation_id", "confirmation", "SET NULL"),
        ("authorized_by_id", "confirmation", "SET NULL"),
        ("session_id", "agent_session", "SET NULL"),
    },
}

#: 三张旧表重建后的索引（名字 → 列序）。
EXPECTED_INDEXES = {
    "confirmation": {"ix_confirmation_status_expires_at": ("status", "expires_at")},
    "agent_question": {
        "ix_agent_question_status_expires_at": ("status", "expires_at"),
        "ix_agent_question_session_status_created_at": (
            "session_id",
            "status",
            "created_at",
        ),
    },
    "agent_audit": {
        "ix_agent_audit_caller_created_at": ("caller", "created_at"),
        "ix_agent_audit_tool_name_created_at": ("tool_name", "created_at"),
        "ix_agent_audit_ticket_id_created_at": ("ticket_id", "created_at"),
    },
}

#: 0026 上先写一行 agent_session（agent_question 的会话外键）。
AGENT_SESSION_SEED = {
    "id": "session-m71",
    "title": "legacy",
    "status": "active",
    "model": "test-model",
    "message_count": 1,
    "prompt_tokens": 1,
    "completion_tokens": 2,
    "kind": "worker",
    "created_at": "2026-11-02 08:00:00",
    "updated_at": "2026-11-02 08:00:10",
    "last_message_at": "2026-11-02 08:00:10",
}

#: 0026 上先写一行 confirmation；升级后 origin_* 必须是 NULL。
CONFIRMATION_SEED = {
    "id": "confirmation-m71",
    "action": "click",
    "risk_level": "consume",
    "reason": "点一次开始",
    "payload": "{}",
    "status": "pending",
    "requested_by": "agent",
    "expires_at": "2026-11-02 08:10:00",
    "created_at": "2026-11-02 08:00:00",
}

#: 0026 上先写一行 agent_question；升级后 origin_* 必须是 NULL。
AGENT_QUESTION_SEED = {
    "id": "question-m71",
    "session_id": "session-m71",
    "tool_call_id": "call-m71",
    "question": "用哪个编队？",
    "options": "[]",
    "multiple": 0,
    "status": "pending",
    "expires_at": "2026-11-02 08:10:00",
    "created_at": "2026-11-02 08:00:00",
}

#: 0026 上先写一行 agent_audit；升级后 run_id / ticket_id / agent_name 必须是 NULL。
AGENT_AUDIT_SEED = {
    "caller": "rest",
    "caller_detail": "test",
    "tool_name": "get_status",
    "arguments": "{}",
    "status": "succeeded",
    "risk_level": "none",
    "forced": 0,
    "duration_ms": 5,
    "created_at": "2026-11-02 08:00:05",
}

#: 升级后插两行 device_lease 用的公共值；第二行换 lease_id、同 device_id。
DEVICE_LEASE_ROW = {
    "device_id": "default",
    "lease_id": "lease-m71",
    "holder_agent": "daily_ops",
    "purpose": "click",
    "explicit": 1,
    "acquired_at": "2026-11-02 09:00:00",
    "expires_at": "2026-11-02 09:10:00",
    "heartbeat_at": "2026-11-02 09:00:00",
    "boot_id": "boot-m71",
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


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    return {row["name"]: row for row in rows}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'index'").fetchall()
    return {row["name"] for row in rows}


def _ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("select sql from sqlite_master where name = ?", (name,)).fetchone()
    assert row is not None and row[0], f"sqlite_master 里没有 {name}"
    return row[0].upper()


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    return {
        (row["from"], row["table"], (row["on_delete"] or "").upper())
        for row in conn.execute(f"pragma foreign_key_list('{table}')")
    }


def _seed_legacy_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        "insert into agent_session (id, title, status, model, message_count,"
        " prompt_tokens, completion_tokens, kind, created_at, updated_at,"
        " last_message_at)"
        " values (:id, :title, :status, :model, :message_count, :prompt_tokens,"
        " :completion_tokens, :kind, :created_at, :updated_at, :last_message_at)",
        AGENT_SESSION_SEED,
    )
    conn.execute(
        "insert into confirmation (id, action, risk_level, reason, payload, status,"
        " requested_by, expires_at, created_at)"
        " values (:id, :action, :risk_level, :reason, :payload, :status,"
        " :requested_by, :expires_at, :created_at)",
        CONFIRMATION_SEED,
    )
    conn.execute(
        "insert into agent_question (id, session_id, tool_call_id, question,"
        " options, multiple, status, expires_at, created_at)"
        " values (:id, :session_id, :tool_call_id, :question, :options, :multiple,"
        " :status, :expires_at, :created_at)",
        AGENT_QUESTION_SEED,
    )
    conn.execute(
        "insert into agent_audit (caller, caller_detail, tool_name, arguments,"
        " status, risk_level, forced, duration_ms, created_at)"
        " values (:caller, :caller_detail, :tool_name, :arguments, :status,"
        " :risk_level, :forced, :duration_ms, :created_at)",
        AGENT_AUDIT_SEED,
    )
    conn.commit()


def _assert_origin_columns_are_null(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "select origin_run_id, origin_ticket_id, origin_agent from confirmation"
        " where id = :id",
        {"id": CONFIRMATION_SEED["id"]},
    ).fetchone()
    assert (row["origin_run_id"], row["origin_ticket_id"], row["origin_agent"]) == (
        None,
        None,
        None,
    )
    row = conn.execute(
        "select origin_run_id, origin_ticket_id, origin_agent from agent_question"
        " where id = :id",
        {"id": AGENT_QUESTION_SEED["id"]},
    ).fetchone()
    assert (row["origin_run_id"], row["origin_ticket_id"], row["origin_agent"]) == (
        None,
        None,
        None,
    )
    row = conn.execute(
        "select run_id, ticket_id, agent_name from agent_audit where caller = 'rest'"
    ).fetchone()
    assert (row["run_id"], row["ticket_id"], row["agent_name"]) == (None, None, None)


def test_device_lease_migration_adds_table_and_origin_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "device_lease_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_legacy_rows(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "device_lease" in _table_names(conn)

        # 新表的列名、类型、可空与 §4.1 逐一对应。
        columns = _columns(conn, "device_lease")
        assert tuple(columns) == tuple(EXPECTED_DEVICE_LEASE_COLUMNS)
        for name, (type_name, nullable) in EXPECTED_DEVICE_LEASE_COLUMNS.items():
            assert columns[name]["type"].upper() == type_name, name
            assert columns[name]["notnull"] == (0 if nullable else 1), name
        assert columns["device_id"]["pk"] == 1
        assert _foreign_keys(conn, "device_lease") == EXPECTED_NEW_FOREIGN_KEYS[
            "device_lease"
        ]

        # 三张旧表的新列在旧行上是 NULL。
        _assert_origin_columns_are_null(conn)
        for table, expected in EXPECTED_ORIGIN_COLUMNS.items():
            columns = _columns(conn, table)
            assert tuple(columns)[-3:] == expected, table
            for name in expected:
                assert columns[name]["notnull"] == 0, (table, name)

        # 八个新外键都是 SET NULL；batch 重建没有丢掉原有外键。
        for table, expected in EXPECTED_NEW_FOREIGN_KEYS.items():
            assert expected <= _foreign_keys(conn, table), table
        for table, expected in EXPECTED_FULL_FOREIGN_KEYS.items():
            assert expected <= _foreign_keys(conn, table), table

        # batch 重建保留了 agent_audit 的 AUTOINCREMENT 与三张表的原有索引。
        assert "AUTOINCREMENT" in _ddl(conn, "agent_audit")
        indices = _index_names(conn)
        for table, expected in EXPECTED_INDEXES.items():
            for name in expected:
                assert name in indices, (table, name)
        assert "UQ_DEVICE_LEASE_LEASE_ID" in _ddl(conn, "device_lease")

        # 第二行同 device_id 违反主键。
        conn.execute(
            "insert into device_lease (device_id, lease_id, holder_agent, purpose,"
            " explicit, acquired_at, expires_at, heartbeat_at, boot_id)"
            " values (:device_id, :lease_id, :holder_agent, :purpose, :explicit,"
            " :acquired_at, :expires_at, :heartbeat_at, :boot_id)",
            DEVICE_LEASE_ROW,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into device_lease (device_id, lease_id, holder_agent,"
                " purpose, explicit, acquired_at, expires_at, heartbeat_at, boot_id)"
                " values (:device_id, :lease_id, :holder_agent, :purpose, :explicit,"
                " :acquired_at, :expires_at, :heartbeat_at, :boot_id)",
                {**DEVICE_LEASE_ROW, "lease_id": "lease-m71-second"},
            )
        conn.rollback()
    finally:
        conn.close()


def test_device_lease_migration_downgrade_restores_0026(tmp_path: Path) -> None:
    db_path = tmp_path / "device_lease_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_legacy_rows(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    command.downgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PRE_REVISION
        tables = _table_names(conn)
        indices = _index_names(conn)
        assert "device_lease" not in tables
        assert "ix_agent_audit_ticket_id_created_at" not in indices
        for table, expected in EXPECTED_ORIGIN_COLUMNS.items():
            columns = _columns(conn, table)
            for name in expected:
                assert name not in columns, (table, name)
        for table, expected in EXPECTED_FULL_FOREIGN_KEYS.items():
            assert _foreign_keys(conn, table) == expected, table

        # 旧行逐列完好：batch 重建只是加/删列，不碰数据。
        row = conn.execute(
            "select * from confirmation where id = :id", {"id": CONFIRMATION_SEED["id"]}
        ).fetchone()
        for name, value in CONFIRMATION_SEED.items():
            assert row[name] == value, name
        row = conn.execute(
            "select * from agent_question where id = :id",
            {"id": AGENT_QUESTION_SEED["id"]},
        ).fetchone()
        for name, value in AGENT_QUESTION_SEED.items():
            assert row[name] == value, name
        row = conn.execute("select * from agent_audit").fetchone()
        for name, value in AGENT_AUDIT_SEED.items():
            assert row[name] == value, name
    finally:
        conn.close()

    # 再升级：新表回来且是空的，旧行新列仍为 NULL。
    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert conn.execute("select count(*) from device_lease").fetchone()[0] == 0
        _assert_origin_columns_are_null(conn)
    finally:
        conn.close()
