"""M99：``0043_capability_insights`` 迁移验收（spec §4.1、§5 T2、§6）。

测试纪律与 ``tests/db/test_demonstration_migration.py`` 一致：全程用 ``tmp_path``
的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖两件事：

1. 空库升级到 0043：``eval_run`` / ``eval_case_result`` / ``run_feedback`` 三张表
   的列类型与可空性逐项照 spec §4.1；5 条业务索引、2 个唯一约束、2 条
   ``ON DELETE CASCADE`` 外键，以及两个 ``INTEGER PRIMARY KEY AUTOINCREMENT`` 表；
2. 降级回 0042 后三张表与它们的索引消失，0042 的表还在；再升级是空表。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0042_demonstration"
HEAD_REVISION = "0043_capability_insights"

#: 表名 → {列名: (DDL 类型, 是否 NOT NULL)}。列序与 spec §4.1 一致。
EXPECTED_COLUMNS = {
    "eval_run": {
        "id": ("VARCHAR(64)", True),
        "suite": ("VARCHAR(16)", True),
        "mode": ("VARCHAR(8)", True),
        "started_at": ("DATETIME", True),
        "finished_at": ("DATETIME", True),
        "duration_ms": ("INTEGER", True),
        "total": ("INTEGER", True),
        "passed": ("INTEGER", True),
        "failed": ("INTEGER", True),
        "error": ("INTEGER", True),
        "skipped": ("INTEGER", True),
        "pass_rate": ("FLOAT", True),
        "model_calls": ("INTEGER", True),
        "prompt_tokens": ("INTEGER", True),
        "completion_tokens": ("INTEGER", True),
        "cached_tokens": ("INTEGER", True),
        "reasoning_tokens": ("INTEGER", True),
        "cost_micros": ("INTEGER", True),
        "currency": ("VARCHAR(8)", False),
        "git": ("JSON", False),
        "model": ("JSON", False),
        "filters": ("JSON", False),
        "created_at": ("DATETIME", True),
    },
    "eval_case_result": {
        "id": ("INTEGER", True),
        "eval_run_id": ("VARCHAR(64)", True),
        "scenario": ("VARCHAR(96)", True),
        "title": ("VARCHAR(64)", True),
        "suite": ("VARCHAR(16)", True),
        "agent": ("VARCHAR(32)", True),
        "metrics": ("JSON", True),
        "tags": ("JSON", True),
        "status": ("VARCHAR(16)", True),
        "score": ("FLOAT", False),
        "attempt": ("INTEGER", True),
        "variant": ("VARCHAR(16)", True),
        "duration_ms": ("INTEGER", True),
        "model_calls": ("INTEGER", True),
        "prompt_tokens": ("INTEGER", True),
        "completion_tokens": ("INTEGER", True),
        "cached_tokens": ("INTEGER", True),
        "reasoning_tokens": ("INTEGER", True),
        "cost_micros": ("INTEGER", True),
        "currency": ("VARCHAR(8)", False),
        "graders": ("JSON", True),
        "error": ("TEXT", False),
        "created_at": ("DATETIME", True),
    },
    "run_feedback": {
        "id": ("INTEGER", True),
        "run_id": ("VARCHAR(36)", True),
        "rating": ("INTEGER", True),
        "reasons": ("JSON", True),
        "comment": ("VARCHAR(500)", False),
        "created_at": ("DATETIME", True),
        "updated_at": ("DATETIME", True),
    },
}

#: 表名 → {索引名: (列序, 是否唯一)}。
EXPECTED_INDEXES = {
    "eval_run": {
        "ix_eval_run_finished_at": (("finished_at",), False),
        "ix_eval_run_suite_finished_at": (("suite", "finished_at"), False),
    },
    "eval_case_result": {
        "ix_eval_case_result_eval_run_id": (("eval_run_id",), False),
        "ix_eval_case_result_scenario": (("scenario",), False),
    },
    "run_feedback": {
        "ix_run_feedback_created_at": (("created_at",), False),
    },
}

#: 表名 → [(本表列, 被引用表, 被引用列, ondelete)]。
EXPECTED_FOREIGN_KEYS = {
    "eval_case_result": [("eval_run_id", "eval_run", "id", "CASCADE")],
    "run_feedback": [("run_id", "agent_run", "id", "CASCADE")],
}

#: 两个高频追加表落下 ``AUTOINCREMENT``；``eval_run`` 是字符串主键，不在此列。
EXPECTED_AUTOINCREMENT_TABLES = ("eval_case_result", "run_feedback")


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
    """非 SQLite 自建的索引；自增主键的 autoindex 不算业务索引。"""
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
) -> list[tuple[str, str, str, str]]:
    return [
        (row["from"], row["table"], row["to"], row["on_delete"])
        for row in conn.execute(f"pragma foreign_key_list({table})")
    ]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type='table'")
    }


def _all_index_names(conn: sqlite3.Connection) -> set[str]:
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


def test_upgrade_creates_three_tables_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "capability_insights_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        names = _table_names(conn)
        for table in EXPECTED_COLUMNS:
            assert table in names, table

        # 逐列断言类型与可空性（spec §4.1 的模型逐列展开）。
        for table, expected in EXPECTED_COLUMNS.items():
            assert _columns(conn, table) == expected, table

        # 5 条业务索引，逐条核对名字、列序与唯一性；没有多建任何一条。
        for table, expected in EXPECTED_INDEXES.items():
            assert _indexes(conn, table) == expected, table

        # 唯一约束、主键与外键的名字进 DDL；SQLite 另建 autoindex。
        eval_run_sql = _table_sql(conn, "eval_run")
        assert "CONSTRAINT PK_EVAL_RUN PRIMARY KEY (ID)" in eval_run_sql
        assert "AUTOINCREMENT" not in eval_run_sql

        case_sql = _table_sql(conn, "eval_case_result")
        # 自增表的主键在 SQLite 上必须写成列内联形式（SQLAlchemy 会丢掉命名的
        # PrimaryKeyConstraint；与 trace_span / outbox 等既有自增表同一形态）。
        assert "ID INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT" in case_sql
        assert (
            "CONSTRAINT UQ_EVAL_CASE_RESULT_RUN_SCENARIO_ATTEMPT_VARIANT"
            " UNIQUE (EVAL_RUN_ID, SCENARIO, ATTEMPT, VARIANT)"
        ) in case_sql
        assert (
            "CONSTRAINT FK_EVAL_CASE_RESULT_EVAL_RUN_ID_EVAL_RUN"
            " FOREIGN KEY(EVAL_RUN_ID) REFERENCES EVAL_RUN (ID) ON DELETE CASCADE"
        ) in case_sql

        feedback_sql = _table_sql(conn, "run_feedback")
        assert "ID INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT" in feedback_sql
        assert "CONSTRAINT UQ_RUN_FEEDBACK_RUN_ID UNIQUE (RUN_ID)" in feedback_sql
        assert (
            "CONSTRAINT FK_RUN_FEEDBACK_RUN_ID_AGENT_RUN"
            " FOREIGN KEY(RUN_ID) REFERENCES AGENT_RUN (ID) ON DELETE CASCADE"
        ) in feedback_sql

        # 两个自增表（id 不复用的前提）与两条 CASCADE 外键。
        for table in EXPECTED_AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in _table_sql(conn, table), table
        for table, expected in EXPECTED_FOREIGN_KEYS.items():
            assert _foreign_keys(conn, table) == expected, table
    finally:
        conn.close()


def test_downgrade_drops_them(tmp_path: Path) -> None:
    db_path = tmp_path / "capability_insights_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)
    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        names = _table_names(conn)
        left_tables = set(EXPECTED_COLUMNS) & names
        assert not left_tables, sorted(left_tables)
        # 0042 的表照旧；降级只删本迁移的三张表与它们的 5 条索引。
        assert "demonstration" in names
        expected_indexes = {
            index for indexes in EXPECTED_INDEXES.values() for index in indexes
        }
        assert not (expected_indexes & _all_index_names(conn))
    finally:
        conn.close()

    # 降级后可重放，且三张表都是空表。
    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        for table in EXPECTED_COLUMNS:
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0
    finally:
        conn.close()
