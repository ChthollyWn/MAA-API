"""M67：``0024_model_profiles`` 迁移验收。

测试纪律与 ``tests/db/test_skland_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰 ``resource/maa_api.db``。

两条断言（spec §6）：

1. 升级到 0024 后 ``model_profile`` / ``cost_ledger`` 存在、列集合与模型一致，
   ``cost_ledger`` 的 DDL 含 ``AUTOINCREMENT``；
2. 降级回 0023 后两张表消失，``agent_run`` 里的既有数据不变。
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0023_agent_runs"
HEAD_REVISION = "0024_model_profiles"

MODEL_PROFILE_COLUMNS = (
    "id",
    "name",
    "kind",
    "provider",
    "base_url",
    "api_key_encrypted",
    "model",
    "context_window",
    "max_output_tokens",
    "supports_vision",
    "supports_tools",
    "supports_json_schema",
    "dynamic_context_position",
    "price_input",
    "price_cached_input",
    "price_output",
    "currency",
    "embedding_dim",
    "local_model_path",
    "enabled",
    "created_at",
    "updated_at",
)
COST_LEDGER_COLUMNS = (
    "id",
    "run_id",
    "session_id",
    "agent_name",
    "role",
    "model_profile",
    "model",
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cost_micros",
    "currency",
    "created_at",
)
#: 降级前先写一行 agent_run；降级后逐列比对，证明本迁移没碰既有数据。
AGENT_RUN_SEED = {
    "id": "run-m66",
    "session_id": None,
    "parent_run_id": None,
    "root_run_id": "run-m66",
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


def test_upgrade_creates_profile_and_ledger_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "model_profiles_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        tables = _table_names(conn)
        assert {"model_profile", "cost_ledger"} <= tables

        profile = _columns(conn, "model_profile")
        assert tuple(profile) == MODEL_PROFILE_COLUMNS
        assert profile["id"]["pk"] == 1
        assert profile["name"]["notnull"] == 1
        assert profile["api_key_encrypted"]["notnull"] == 0
        assert profile["enabled"]["notnull"] == 1

        ledger = _columns(conn, "cost_ledger")
        assert tuple(ledger) == COST_LEDGER_COLUMNS
        assert ledger["id"]["pk"] == 1
        for name in ("run_id", "session_id"):
            assert ledger[name]["notnull"] == 0, name
        for name in ("agent_name", "role", "model_profile", "model", "currency"):
            assert ledger[name]["notnull"] == 1, name

        # AUTOINCREMENT 是 DDL 关键字，不是模型声明的推论：反射不到就只能看原文。
        assert "AUTOINCREMENT" in _ddl(conn, "cost_ledger")
        assert "UNIQUE (NAME)" in _ddl(conn, "model_profile")

        # 两条外键的目标与 ondelete 与规格一致（v3 docs/03 §4.2）。
        foreign_keys = {
            (row["from"], row["table"], (row["on_delete"] or "").upper())
            for row in conn.execute("pragma foreign_key_list('cost_ledger')")
        }
        assert foreign_keys == {
            ("run_id", "agent_run", "SET NULL"),
            ("session_id", "agent_session", "SET NULL"),
        }
    finally:
        conn.close()


def test_downgrade_drops_both_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "model_profiles_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_agent_run(conn)
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == PRE_REVISION
        tables = _table_names(conn)
        assert "model_profile" not in tables
        assert "cost_ledger" not in tables

        row = conn.execute(
            "select * from agent_run where id = 'run-m66'"
        ).fetchone()
        assert row is not None
        for name, value in AGENT_RUN_SEED.items():
            assert row[name] == value, name
    finally:
        conn.close()
