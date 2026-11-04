"""M68：``0025_trace_spans`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``tests/db/test_model_profiles_migration.py`` 一致：全程用 ``tmp_path``
的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

两条断言（spec §6）：

1. 升级到 0025 后 ``trace_span`` 存在，列类型与可空、两个显式索引、一个唯一约束、
   ``run_id`` 的 ``ON DELETE CASCADE`` 外键与 ``AUTOINCREMENT`` 关键字全部落地；
2. 降级回 0024 后表与两条索引消失，``agent_run`` 里的既有数据不变。
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0024_model_profiles"
HEAD_REVISION = "0025_trace_spans"

#: 列名 → (DDL 类型, 是否可空)。
EXPECTED_COLUMNS = {
    "id": ("INTEGER", False),
    "trace_id": ("VARCHAR(36)", False),
    "span_id": ("VARCHAR(16)", False),
    "parent_span_id": ("VARCHAR(16)", True),
    "run_id": ("VARCHAR(36)", True),
    "name": ("VARCHAR(64)", False),
    "kind": ("VARCHAR(16)", False),
    "status": ("VARCHAR(16)", False),
    "attributes": ("JSON", False),
    "started_at": ("DATETIME", False),
    "ended_at": ("DATETIME", True),
    "duration_ms": ("INTEGER", True),
}

EXPECTED_INDEXES = {
    "ix_trace_span_run_id": ("run_id",),
    "ix_trace_span_started_at": ("started_at",),
}
EXPECTED_UNIQUE = {
    "uq_trace_span_trace_id_span_id": ("trace_id", "span_id"),
}

#: 降级前先写一行 agent_run + 一条 span；降级后逐列比对，证明模板之外的数据没被碰。
AGENT_RUN_SEED = {
    "id": "run-m68",
    "session_id": None,
    "parent_run_id": None,
    "root_run_id": "run-m68",
    "agent_name": "legacy_chat",
    "kind": "legacy_turn",
    "status": "succeeded",
    "prompt_tokens": 12,
    "completion_tokens": 34,
    "cached_tokens": 5,
    "reasoning_tokens": 6,
    "cost_micros": 1234,
    "steps": 2,
    "boot_id": "boot-1",
    "started_at": "2026-11-01 08:00:00",
    "finished_at": "2026-11-01 08:00:05",
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
    """``sqlite_master`` 里的索引名（含 SQLite 为 UNIQUE 自动建的内部索引）。"""
    rows = conn.execute("select name from sqlite_master where type = 'index'").fetchall()
    return {row["name"] for row in rows}


def _ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("select sql from sqlite_master where name = ?", (name,)).fetchone()
    assert row is not None and row[0], f"sqlite_master 里没有 {name}"
    return row[0].upper()


def _seed_agent_run(conn: sqlite3.Connection) -> None:
    conn.execute(
        "insert into agent_run (id, session_id, parent_run_id, root_run_id, agent_name,"
        " kind, status, prompt_tokens, completion_tokens, cached_tokens,"
        " reasoning_tokens, cost_micros, steps, boot_id, started_at, finished_at)"
        " values (:id, :session_id, :parent_run_id, :root_run_id, :agent_name, :kind,"
        " :status, :prompt_tokens, :completion_tokens, :cached_tokens,"
        " :reasoning_tokens, :cost_micros, :steps, :boot_id, :started_at, :finished_at)",
        AGENT_RUN_SEED,
    )
    conn.commit()


def _seed_trace_span(conn: sqlite3.Connection) -> None:
    conn.execute(
        "insert into trace_span (trace_id, span_id, parent_span_id, run_id, name, kind,"
        " status, started_at, ended_at, duration_ms)"
        " values ('trace-1', 'span-1', null, 'run-m68', 'agent.run', 'other', 'ok',"
        " '2026-11-01 08:00:00', '2026-11-01 08:00:05', 5000)"
    )
    conn.commit()


def test_upgrade_creates_trace_span_with_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "trace_spans_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "trace_span" in _table_names(conn)

        columns = _columns(conn, "trace_span")
        assert tuple(columns) == tuple(EXPECTED_COLUMNS)
        for name, (type_name, nullable) in EXPECTED_COLUMNS.items():
            assert columns[name]["type"].upper() == type_name, name
            assert columns[name]["notnull"] == (0 if nullable else 1), name
        assert columns["id"]["pk"] == 1

        # AUTOINCREMENT 是 DDL 关键字，不是模型声明的推论：反射不到就只能看原文。
        table_ddl = _ddl(conn, "trace_span")
        assert "AUTOINCREMENT" in table_ddl
        assert "DEFAULT '{}'" in table_ddl
        assert "CONSTRAINT UQ_TRACE_SPAN_TRACE_ID_SPAN_ID UNIQUE" in table_ddl

        # 外键：run_id → agent_run，删运行级联删 span。
        foreign_keys = {
            (row["from"], row["table"], (row["on_delete"] or "").upper())
            for row in conn.execute("pragma foreign_key_list('trace_span')")
        }
        assert foreign_keys == {("run_id", "agent_run", "CASCADE")}

        # 写一行：attributes 不给值时由 server_default 落成 {}；删运行级联删 span。
        conn.execute("PRAGMA foreign_keys=ON")
        _seed_agent_run(conn)
        _seed_trace_span(conn)
        assert tuple(
            conn.execute(
                "select attributes, ended_at, duration_ms from trace_span"
            ).fetchone()
        ) == ("{}", "2026-11-01 08:00:05", 5000)
        conn.execute("delete from agent_run where id = 'run-m68'")
        conn.commit()
        assert tuple(
            conn.execute("select count(*) from trace_span").fetchone()
        ) == (0,)
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("trace_span")
        }
        assert indexes == EXPECTED_INDEXES
        # 唯一约束的名字就是 spec 里的 ``uq_trace_span_trace_id_span_id``。
        uniques = {
            row["name"]: tuple(row["column_names"])
            for row in inspector.get_unique_constraints("trace_span")
        }
        assert uniques == EXPECTED_UNIQUE
        # 三个索引名：两条显式索引 + 一条唯一约束。
        assert set(indexes) | set(uniques) == {
            "ix_trace_span_run_id",
            "ix_trace_span_started_at",
            "uq_trace_span_trace_id_span_id",
        }
    finally:
        engine.dispose()


def test_downgrade_drops_trace_span(tmp_path: Path) -> None:
    db_path = tmp_path / "trace_spans_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_agent_run(conn)
        _seed_trace_span(conn)
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == PRE_REVISION
        assert "trace_span" not in _table_names(conn)
        assert not (
            _index_names(conn)
            & {"ix_trace_span_run_id", "ix_trace_span_started_at"}
        )

        row = conn.execute("select * from agent_run where id = 'run-m68'").fetchone()
        assert row is not None
        for name, value in AGENT_RUN_SEED.items():
            assert row[name] == value, name
    finally:
        conn.close()
