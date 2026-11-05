"""M81：``0034_triggers`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``tests/db/test_rewind_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖四件事：

1. 升级建出 ``agent_trigger`` / ``trigger_rule`` / ``trigger_observation``，列、可空、
   索引与 ``ondelete`` 都与 §4.1 一致；``ticket`` 多出可空的 ``source_trigger_id``，
   已有一行工单的数据逐列不变；
2. 循环外键真的生效：删事件级联删观察记录并把工单的来源置空；删工单把事件的
   ``ticket_id`` 置空；
3. 降级回到 0033 的形状：三张表与 ``ticket.source_trigger_id`` 消失，工单其它数据
   不丢；再升级可重放；
4. 离线 ``--sql`` 能渲染升级与降级的 batch 重建，且不建库文件。
"""

import contextlib
import io
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0033_rewind"
HEAD_REVISION = "0034_triggers"
TIMESTAMP = "2026-01-01 00:00:00"

#: 表名 → 列名 → (DDL 类型, 是否可空)。顺序与 §4.1 一致。
EXPECTED_COLUMNS = {
    "agent_trigger": {
        "id": ("INTEGER", True),
        "source": ("VARCHAR(24)", True),
        "kind": ("VARCHAR(48)", True),
        "thread_key": ("VARCHAR(160)", True),
        "sender": ("VARCHAR(128)", False),
        "payload": ("JSON", True),
        "dedupe_key": ("VARCHAR(160)", False),
        "admission": ("VARCHAR(16)", True),
        "rule_id": ("VARCHAR(48)", False),
        "eval_status": ("VARCHAR(16)", True),
        "eval_reason": ("TEXT", False),
        "eval_run_id": ("VARCHAR(36)", False),
        "ticket_id": ("VARCHAR(36)", False),
        "routed_to": ("VARCHAR(16)", False),
        "created_at": ("DATETIME", True),
        "processed_at": ("DATETIME", False),
    },
    "trigger_rule": {
        "id": ("VARCHAR(48)", True),
        "title": ("VARCHAR(64)", False),
        "source": ("VARCHAR(24)", False),
        "kind_pattern": ("VARCHAR(64)", False),
        "conditions": ("JSON", False),
        "mode": ("VARCHAR(16)", False),
        "worker": ("VARCHAR(32)", False),
        "policy": ("TEXT", False),
        "cooldown_seconds": ("INTEGER", False),
        "cron": ("VARCHAR(64)", False),
        "enabled": ("BOOLEAN", True),
        "origin": ("VARCHAR(16)", True),
        "created_at": ("DATETIME", True),
        "updated_at": ("DATETIME", True),
    },
    "trigger_observation": {
        "id": ("INTEGER", True),
        "trigger_id": ("INTEGER", True),
        "rule_id": ("VARCHAR(48)", True),
        "decision": ("JSON", True),
        "verdict": ("VARCHAR(16)", False),
        "reviewed_at": ("DATETIME", False),
        "created_at": ("DATETIME", True),
    },
}

#: 表名 → {索引名: (列序, 是否唯一)}。
EXPECTED_INDEXES = {
    "agent_trigger": {
        "uq_agent_trigger_dedupe_key": (("dedupe_key",), True),
        "ix_agent_trigger_thread_key_created_at": (
            ("thread_key", "created_at"),
            False,
        ),
        "ix_agent_trigger_created_at": (("created_at",), False),
    },
    "trigger_rule": {},
    "trigger_observation": {
        "ix_trigger_observation_rule_id_created_at": (
            ("rule_id", "created_at"),
            False,
        ),
    },
}

#: 表名 → {(列, 目标表, ondelete)}。
EXPECTED_FOREIGN_KEYS = {
    "agent_trigger": {
        ("eval_run_id", "agent_run", "SET NULL"),
        ("ticket_id", "ticket", "SET NULL"),
    },
    "trigger_rule": set(),
    "trigger_observation": {("trigger_id", "agent_trigger", "CASCADE")},
}

#: 升级前先写一行工单；升级/降级后逐列比对，证明 batch 重建没有碰数据。
TICKET_SEED = {
    "id": "ticket-m81",
    "title": "旧工单",
    "kind": "daily_ops",
    "status": "waiting",
    "origin": "user",
    "priority": 2,
    "summary": "升级前的说明",
    "created_at": "2026-01-01 00:00:00",
    "updated_at": "2026-01-01 00:01:00",
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


def _indexes(conn: sqlite3.Connection, table: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    result: dict[str, tuple[tuple[str, ...], bool]] = {}
    for row in conn.execute(f"pragma index_list({table})"):
        # 文本主键由 SQLite 自动建 sqlite_autoindex_*，不属于 §4.1 的索引清单。
        if row["name"].startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            info["name"] for info in conn.execute(f"pragma index_info({row['name']})")
        )
        result[row["name"]] = (columns, bool(row["unique"]))
    return result


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    return {
        (row["from"], row["table"], row["on_delete"])
        for row in conn.execute(f"pragma foreign_key_list({table})")
    }


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type='table'")
    }


def _seed_ticket(conn: sqlite3.Connection) -> None:
    columns = ", ".join(TICKET_SEED)
    placeholders = ", ".join("?" * len(TICKET_SEED))
    conn.execute(
        f"insert into ticket ({columns}) values ({placeholders})",
        tuple(TICKET_SEED.values()),
    )
    conn.commit()


def test_upgrade_adds_tables_and_ticket_column(tmp_path: Path) -> None:
    db_path = tmp_path / "triggers_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        _seed_ticket(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert set(EXPECTED_COLUMNS) <= _table_names(conn)

        for table, expected in EXPECTED_COLUMNS.items():
            assert _columns(conn, table) == expected, table

        ticket_columns = _columns(conn, "ticket")
        assert ticket_columns["source_trigger_id"] == ("INTEGER", False)
        # 工单原有列一个不少，新列对旧行是 NULL。
        assert {
            "id",
            "title",
            "kind",
            "status",
            "origin",
            "priority",
            "summary",
            "created_at",
            "updated_at",
        } <= set(ticket_columns)

        for table, expected in EXPECTED_INDEXES.items():
            assert _indexes(conn, table) == expected, table
        assert set(_indexes(conn, "ticket")) == {
            "ix_ticket_status_updated_at",
            "ix_ticket_kind_created_at",
        }

        # 部分唯一索引真的带谓词：只有非 NULL 的 dedupe_key 互斥。
        predicate = conn.execute(
            "select sql from sqlite_master where type='index'"
            " and name='uq_agent_trigger_dedupe_key'"
        ).fetchone()["sql"]
        assert "WHERE" in predicate.upper()
        assert "dedupe_key IS NOT NULL" in predicate

        for table, expected in EXPECTED_FOREIGN_KEYS.items():
            assert _foreign_keys(conn, table) == expected, table
        assert (
            "source_trigger_id",
            "agent_trigger",
            "SET NULL",
        ) in _foreign_keys(conn, "ticket")

        row = conn.execute(
            "select title, kind, status, origin, priority, summary, created_at,"
            " updated_at, source_trigger_id from ticket where id='ticket-m81'"
        ).fetchone()
        assert tuple(row) == (
            "旧工单",
            "daily_ops",
            "waiting",
            "user",
            2,
            "升级前的说明",
            TIMESTAMP,
            "2026-01-01 00:01:00",
            None,
        )

        # 语义：循环外键两条边都生效。
        conn.execute("pragma foreign_keys = on")
        conn.execute(
            "insert into agent_trigger (id, source, kind, thread_key, admission,"
            " eval_status, created_at)"
            " values (1, 'pipeline', 'pipeline.failed', 'pipeline:1', 'accepted',"
            " 'pending', ?)",
            (TIMESTAMP,),
        )
        conn.execute(
            "insert into trigger_observation (trigger_id, rule_id, decision, created_at)"
            " values (1, 'diagnose-pipeline-failure', '{}', ?)",
            (TIMESTAMP,),
        )
        conn.execute(
            "update ticket set source_trigger_id = 1 where id='ticket-m81'"
        )
        conn.execute(
            "insert into agent_trigger (id, source, kind, thread_key, admission,"
            " eval_status, ticket_id, created_at)"
            " values (2, 'pipeline', 'pipeline.failed', 'pipeline:2', 'accepted',"
            " 'pending', 'ticket-m81', ?)",
            (TIMESTAMP,),
        )
        conn.commit()

        conn.execute("delete from agent_trigger where id = 1")
        conn.commit()
        assert conn.execute("select count(*) from trigger_observation").fetchone()[0] == 0
        assert conn.execute(
            "select source_trigger_id from ticket where id='ticket-m81'"
        ).fetchone()[0] is None

        conn.execute("delete from ticket where id='ticket-m81'")
        conn.commit()
        assert conn.execute(
            "select ticket_id from agent_trigger where id = 2"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_downgrade_restores_ticket(tmp_path: Path) -> None:
    db_path = tmp_path / "triggers_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        _seed_ticket(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        conn.execute("pragma foreign_keys = on")
        conn.execute(
            "insert into agent_trigger (id, source, kind, thread_key, admission,"
            " eval_status, created_at)"
            " values (7, 'pipeline', 'pipeline.failed', 'pipeline:7', 'accepted',"
            " 'pending', ?)",
            (TIMESTAMP,),
        )
        conn.execute(
            "insert into trigger_observation (trigger_id, rule_id, decision, created_at)"
            " values (7, 'sanity-full', '{}', ?)",
            (TIMESTAMP,),
        )
        conn.execute("update ticket set source_trigger_id = 7 where id='ticket-m81'")
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        names = _table_names(conn)
        assert not (set(EXPECTED_COLUMNS) & names)
        assert "source_trigger_id" not in _columns(conn, "ticket")
        row = conn.execute(
            "select title, kind, status, origin, priority, summary, created_at,"
            " updated_at from ticket where id='ticket-m81'"
        ).fetchone()
        assert tuple(row) == (
            "旧工单",
            "daily_ops",
            "waiting",
            "user",
            2,
            "升级前的说明",
            TIMESTAMP,
            "2026-01-01 00:01:00",
        )
    finally:
        conn.close()

    # 降到底再升级一次：可重放，三张表是空的（没有假装回填历史事件）。
    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert set(EXPECTED_COLUMNS) <= _table_names(conn)
        for table in EXPECTED_COLUMNS:
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0
        assert "source_trigger_id" in _columns(conn, "ticket")
    finally:
        conn.close()


def test_offline_render_keeps_copy_from(tmp_path: Path) -> None:
    """离线 ``--sql`` 没有连接可反射，``copy_from`` 必须让 batch 重建可渲染。"""
    db_path = tmp_path / "triggers_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE agent_trigger" in upgrade_text
    assert "uq_agent_trigger_dedupe_key" in upgrade_text
    assert "trigger_observation" in upgrade_text
    assert "fk_trigger_observation_trigger_id_agent_trigger" in upgrade_text
    assert "fk_ticket_source_trigger_id_agent_trigger" in upgrade_text
    assert "_alembic_tmp_ticket" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    # 降级的目标表没有 source_trigger_id：batch 用 copy_from 生成搬运 SQL，
    # INSERT 的列清单里也不该再有它。
    assert "source_trigger_id" not in downgrade_text
    assert "_alembic_tmp_ticket" in downgrade_text
    assert "DROP TABLE agent_trigger" in downgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"
