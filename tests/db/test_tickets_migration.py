"""M69：``0026_tickets`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``tests/db/test_trace_spans_migration.py`` 一致：全程用 ``tmp_path`` 的
临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

三条断言：

1. 在 0025 上先写入 ``agent_session`` / ``agent_run`` / ``agent_message`` 各一行，
   升级到 0026 后五张新表的列名、类型、可空与 §4.1 一致；三处新列为 NULL；
   外键的 ``ondelete`` 逐个对上；``PRAGMA foreign_keys=ON`` 下删 ``ticket``
   级联删掉 ``mandate`` / ``ticket_report`` / ``thread_subscription``，并把
   ``agent_session.ticket_id`` / ``agent_run.ticket_id`` 置空；
2. 降级回 0025 后五张表与三列都不在、旧行完好、版本号回到 0025；再升级后
   五张表是空的（没有假装回填历史工单）；
3. 离线渲染 ``upgrade --sql`` 不连库也能生成五张表与三处 batch 重建的 SQL。
"""

import contextlib
import io
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0025_trace_spans"
HEAD_REVISION = "0026_tickets"

#: 表名 → 列名 → (DDL 类型, 是否可空)。顺序与 §4.1 一致。
EXPECTED_COLUMNS = {
    "ticket": {
        "id": ("VARCHAR(36)", False),
        "title": ("VARCHAR(64)", False),
        "kind": ("VARCHAR(32)", False),
        "status": ("VARCHAR(16)", False),
        "origin": ("VARCHAR(16)", False),
        "priority": ("INTEGER", False),
        "orchestrator_session_id": ("VARCHAR(36)", True),
        "worker_session_id": ("VARCHAR(36)", True),
        "summary": ("TEXT", True),
        "last_report_status": ("VARCHAR(16)", True),
        "human_action": ("JSON", True),
        "result": ("JSON", True),
        "created_at": ("DATETIME", False),
        "updated_at": ("DATETIME", False),
        "ended_at": ("DATETIME", True),
    },
    "mandate": {
        "id": ("VARCHAR(36)", False),
        "ticket_id": ("VARCHAR(36)", False),
        "scope": ("TEXT", False),
        "strength": ("VARCHAR(16)", False),
        "source": ("VARCHAR(16)", False),
        "sender": ("VARCHAR(128)", True),
        "rule_id": ("VARCHAR(48)", True),
        "risk_ceiling": ("VARCHAR(16)", False),
        "policy_version": ("VARCHAR(16)", True),
        "created_at": ("DATETIME", False),
    },
    "ticket_report": {
        "id": ("INTEGER", False),
        "ticket_id": ("VARCHAR(36)", False),
        "run_id": ("VARCHAR(36)", True),
        "status": ("VARCHAR(16)", False),
        "summary": ("TEXT", False),
        "human_action": ("JSON", True),
        "details": ("JSON", True),
        "forwarded": ("BOOLEAN", False),
        "created_at": ("DATETIME", False),
    },
    "thread_subscription": {
        "id": ("INTEGER", False),
        "ticket_id": ("VARCHAR(36)", False),
        "thread_key": ("VARCHAR(160)", False),
        "active": ("BOOLEAN", False),
        "created_at": ("DATETIME", False),
        "ended_at": ("DATETIME", True),
    },
    "agent_inbox": {
        "id": ("INTEGER", False),
        "session_id": ("VARCHAR(36)", False),
        "kind": ("VARCHAR(24)", False),
        "thread_key": ("VARCHAR(160)", True),
        "ref_id": ("VARCHAR(64)", True),
        "payload": ("JSON", False),
        "status": ("VARCHAR(16)", False),
        "created_at": ("DATETIME", False),
        "consumed_at": ("DATETIME", True),
        "consumed_run_id": ("VARCHAR(36)", True),
    },
}

EXPECTED_INDEXES = {
    "ticket": {
        "ix_ticket_status_updated_at": ("status", "updated_at"),
        "ix_ticket_kind_created_at": ("kind", "created_at"),
    },
    "mandate": {},
    "ticket_report": {
        "ix_ticket_report_ticket_id_created_at": ("ticket_id", "created_at"),
    },
    "thread_subscription": {
        "ix_thread_subscription_thread_key_active": ("thread_key", "active"),
    },
    "agent_inbox": {
        "ix_agent_inbox_session_id_status_id": ("session_id", "status", "id"),
    },
}

EXPECTED_UNIQUE = {
    "mandate": {"uq_mandate_ticket_id": ("ticket_id",)},
    "thread_subscription": {
        "uq_thread_subscription_ticket_id_thread_key": ("ticket_id", "thread_key"),
    },
}

#: 表名 → {(列, 目标表, ondelete)}。
EXPECTED_FOREIGN_KEYS = {
    "ticket": {
        ("orchestrator_session_id", "agent_session", "SET NULL"),
        ("worker_session_id", "agent_session", "SET NULL"),
    },
    "mandate": {("ticket_id", "ticket", "CASCADE")},
    "ticket_report": {
        ("ticket_id", "ticket", "CASCADE"),
        ("run_id", "agent_run", "SET NULL"),
    },
    "thread_subscription": {("ticket_id", "ticket", "CASCADE")},
    "agent_inbox": {
        ("session_id", "agent_session", "CASCADE"),
        ("consumed_run_id", "agent_run", "SET NULL"),
    },
}

#: 0025 上先写一行 agent_session；升级后新列 ticket_id 必须是 NULL。
AGENT_SESSION_SEED = {
    "id": "session-m69",
    "title": "legacy",
    "status": "active",
    "model": "test-model",
    "message_count": 3,
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "kind": "worker",
    "created_at": "2026-11-01 08:00:00",
    "updated_at": "2026-11-01 08:00:10",
    "last_message_at": "2026-11-01 08:00:10",
}

#: 0025 上先写一行 agent_run；升级后新列 ticket_id 必须是 NULL。
AGENT_RUN_SEED = {
    "id": "run-m69",
    "session_id": "session-m69",
    "root_run_id": "run-m69",
    "agent_name": "legacy_worker",
    "kind": "worker_turn",
    "status": "succeeded",
    "prompt_tokens": 12,
    "completion_tokens": 34,
    "cached_tokens": 5,
    "reasoning_tokens": 6,
    "cost_micros": 1234,
    "steps": 2,
    "boot_id": "boot-m69",
    "started_at": "2026-11-01 08:00:01",
    "finished_at": "2026-11-01 08:00:05",
}

#: 0025 上先写一行 agent_message；升级后新列 inbox_id 必须是 NULL。
AGENT_MESSAGE_SEED = {
    "session_id": "session-m69",
    "seq": 0,
    "role": "user",
    "content": "hello",
    "run_id": "run-m69",
    "created_at": "2026-11-01 08:00:02",
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
        "insert into agent_run (id, session_id, root_run_id, agent_name, kind,"
        " status, prompt_tokens, completion_tokens, cached_tokens,"
        " reasoning_tokens, cost_micros, steps, boot_id, started_at, finished_at)"
        " values (:id, :session_id, :root_run_id, :agent_name, :kind, :status,"
        " :prompt_tokens, :completion_tokens, :cached_tokens, :reasoning_tokens,"
        " :cost_micros, :steps, :boot_id, :started_at, :finished_at)",
        AGENT_RUN_SEED,
    )
    conn.execute(
        "insert into agent_message (session_id, seq, role, content, run_id,"
        " created_at) values (:session_id, :seq, :role, :content, :run_id,"
        " :created_at)",
        AGENT_MESSAGE_SEED,
    )
    conn.commit()


def test_tickets_migration_adds_tables_and_foreign_key_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "tickets_up.db"
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
        assert set(EXPECTED_COLUMNS) <= _table_names(conn)

        for table, expected in EXPECTED_COLUMNS.items():
            columns = _columns(conn, table)
            assert tuple(columns) == tuple(expected), table
            for name, (type_name, nullable) in expected.items():
                assert columns[name]["type"].upper() == type_name, (table, name)
                assert columns[name]["notnull"] == (0 if nullable else 1), (table, name)
            assert _foreign_keys(conn, table) == EXPECTED_FOREIGN_KEYS[table], table

        # 三张高频追加表的 AUTOINCREMENT 关键字与 agent_inbox.payload 的默认值。
        for table in ("ticket_report", "thread_subscription", "agent_inbox"):
            assert "AUTOINCREMENT" in _ddl(conn, table), table
        assert "DEFAULT '{}'" in _ddl(conn, "agent_inbox")

        # 三处 batch 加的新列在旧行上是 NULL。
        assert conn.execute(
            "select ticket_id from agent_session where id = 'session-m69'"
        ).fetchone()["ticket_id"] is None
        assert conn.execute(
            "select ticket_id from agent_run where id = 'run-m69'"
        ).fetchone()["ticket_id"] is None
        assert conn.execute(
            "select inbox_id from agent_message where session_id = 'session-m69'"
        ).fetchone()["inbox_id"] is None

        # 三处新列的外键 ondelete 也逐一钉死。
        assert _foreign_keys(conn, "agent_session") == {
            ("parent_session_id", "agent_session", "SET NULL"),
            ("atomic_grant_id", "confirmation", "SET NULL"),
            ("ticket_id", "ticket", "SET NULL"),
        }
        assert _foreign_keys(conn, "agent_run") == {
            ("session_id", "agent_session", "CASCADE"),
            ("parent_run_id", "agent_run", "SET NULL"),
            ("ticket_id", "ticket", "SET NULL"),
        }
        assert _foreign_keys(conn, "agent_message") == {
            ("session_id", "agent_session", "CASCADE"),
            ("audit_id", "agent_audit", "SET NULL"),
            ("run_id", "agent_run", "SET NULL"),
            ("inbox_id", "agent_inbox", "SET NULL"),
        }

        # batch 重建没有丢掉 agent_run 的部分唯一索引谓词。
        assert "WHERE STATUS IN ('RUNNING', 'WAITING_HUMAN')" in _ddl(
            conn, "uq_agent_run_active_session"
        )

        # 写工单、委托、汇报、订阅与收件箱各一行：payload 走 server_default。
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "insert into ticket (id, title, kind, status, origin, priority,"
            " orchestrator_session_id, worker_session_id, created_at, updated_at)"
            " values ('ticket-m69', '刷理智', 'daily_ops', 'open', 'user', 1,"
            " 'session-m69', 'session-m69', '2026-11-01 09:00:00',"
            " '2026-11-01 09:00:00')"
        )
        conn.execute(
            "insert into mandate (id, ticket_id, scope, strength, source,"
            " risk_ceiling, created_at)"
            " values ('mandate-m69', 'ticket-m69', '刷 1-7', 'explicit', 'web',"
            " 'consume', '2026-11-01 09:00:00')"
        )
        conn.execute(
            "insert into ticket_report (ticket_id, run_id, status, summary,"
            " forwarded, created_at)"
            " values ('ticket-m69', 'run-m69', 'ready', '刷完了', 0,"
            " '2026-11-01 09:10:00')"
        )
        conn.execute(
            "insert into thread_subscription (ticket_id, thread_key, active,"
            " created_at) values ('ticket-m69', 'ticket:ticket-m69', 1,"
            " '2026-11-01 09:00:00')"
        )
        conn.execute(
            "insert into agent_inbox (session_id, kind, status, created_at)"
            " values ('session-m69', 'report', 'pending', '2026-11-01 09:10:00')"
        )
        inbox_id = conn.execute("select id from agent_inbox").fetchone()["id"]
        assert conn.execute(
            "select payload from agent_inbox where id = ?", (inbox_id,)
        ).fetchone()["payload"] == "{}"
        conn.execute(
            "update agent_session set ticket_id = 'ticket-m69' where id = 'session-m69'"
        )
        conn.execute("update agent_run set ticket_id = 'ticket-m69'")
        conn.execute("update agent_message set inbox_id = ?", (inbox_id,))
        conn.commit()

        # 删工单：三张从属表级联删除，会话/运行的外键列置空，消息行仍在。
        conn.execute("delete from ticket where id = 'ticket-m69'")
        conn.commit()
        for table in ("mandate", "ticket_report", "thread_subscription"):
            assert conn.execute(
                f"select count(*) from {table}"
            ).fetchone()[0] == 0, table
        assert conn.execute(
            "select ticket_id from agent_session where id = 'session-m69'"
        ).fetchone()["ticket_id"] is None
        assert conn.execute(
            "select ticket_id from agent_run where id = 'run-m69'"
        ).fetchone()["ticket_id"] is None
        assert conn.execute("select count(*) from agent_message").fetchone()[0] == 1

        # 删收件箱：消息的 inbox_id 置空，消息本身保留。
        conn.execute("delete from agent_inbox where id = ?", (inbox_id,))
        conn.commit()
        assert conn.execute(
            "select inbox_id from agent_message where session_id = 'session-m69'"
        ).fetchone()["inbox_id"] is None
        assert conn.execute("select count(*) from agent_message").fetchone()[0] == 1
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        for table, expected in EXPECTED_INDEXES.items():
            indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes(table)
            }
            assert indexes == expected, table
        for table, expected in EXPECTED_UNIQUE.items():
            uniques = {
                row["name"]: tuple(row["column_names"])
                for row in inspector.get_unique_constraints(table)
            }
            assert uniques == expected, table
    finally:
        engine.dispose()


def test_tickets_migration_downgrade_restores_0025(tmp_path: Path) -> None:
    db_path = tmp_path / "tickets_down.db"
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
        for table in EXPECTED_COLUMNS:
            assert table not in tables, table
        for name in (
            "ix_ticket_status_updated_at",
            "ix_ticket_kind_created_at",
            "ix_ticket_report_ticket_id_created_at",
            "ix_thread_subscription_thread_key_active",
            "ix_agent_inbox_session_id_status_id",
        ):
            assert name not in indices, name

        assert "ticket_id" not in _columns(conn, "agent_session")
        assert "ticket_id" not in _columns(conn, "agent_run")
        assert "inbox_id" not in _columns(conn, "agent_message")

        # 旧行逐列完好：batch 重建只是加/删列，不碰数据。
        row = conn.execute(
            "select * from agent_session where id = 'session-m69'"
        ).fetchone()
        for name, value in AGENT_SESSION_SEED.items():
            assert row[name] == value, name
        row = conn.execute("select * from agent_run where id = 'run-m69'").fetchone()
        for name, value in AGENT_RUN_SEED.items():
            assert row[name] == value, name
        row = conn.execute("select * from agent_message").fetchone()
        for name, value in AGENT_MESSAGE_SEED.items():
            assert row[name] == value, name
    finally:
        conn.close()

    # 再升级：五张表回来且是空的，说明没有假装回填历史工单。
    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        for table in EXPECTED_COLUMNS:
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0, table
        assert conn.execute("select count(*) from agent_message").fetchone()[0] == 1
    finally:
        conn.close()


def test_tickets_migration_offline_renders_sql(tmp_path: Path) -> None:
    db_path = tmp_path / "tickets_offline.db"
    cfg = _config(db_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(cfg, HEAD_REVISION, sql=True)
    sql = buffer.getvalue()

    for table in EXPECTED_COLUMNS:
        assert f"CREATE TABLE {table}" in sql, table
    # 三张已有表的 batch 重建：离线模式没有连接可反射，全靠 copy_from。
    for table in ("agent_session", "agent_run", "agent_message"):
        assert f"_ALEMBIC_TMP_{table.upper()}" in sql.upper(), table
    assert "fk_agent_session_ticket_id_ticket" in sql
    assert "fk_agent_run_ticket_id_ticket" in sql
    assert "fk_agent_message_inbox_id_agent_inbox" in sql
    assert not db_path.exists(), "离线模式不应创建数据库文件"
