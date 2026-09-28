"""0013: assistant 消息的思考内容列，既有行保持可读、可回滚。"""

import contextlib
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
REVISION = "0013_agent_message_reasoning"
PREVIOUS = "0012_push_subscription"


def config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _insert_legacy_message(db: Path) -> None:
    """在升级前写入一条历史消息：它没有列，升级后应可读且该列为 NULL。"""
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "insert into agent_session"
            " (id, title, status, model, base_url, message_count, prompt_tokens,"
            " completion_tokens, atomic_grant_id, atomic_grant_expires_at,"
            " created_at, updated_at, last_message_at)"
            " values ('legacy', 'old', 'active', 'test', null, 1, 0, 0, null, null,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00', null)"
        )
        conn.execute(
            "insert into agent_message"
            " (session_id, seq, role, content, created_at)"
            " values ('legacy', 0, 'assistant', '旧回复', '2026-01-01 00:00:00')"
        )
        conn.commit()


def test_reasoning_content_column_is_added_without_touching_existing_rows(tmp_path):
    db = tmp_path / "agent-reasoning.db"
    cfg = config(db)
    command.upgrade(cfg, PREVIOUS)
    _insert_legacy_message(db)

    command.upgrade(cfg, REVISION)

    engine = create_engine(f"sqlite:///{db}")
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("agent_message")}
    finally:
        engine.dispose()
    assert "reasoning_content" in columns

    with contextlib.closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "select content, reasoning_content from agent_message where session_id='legacy'"
        ).fetchall()
    assert rows == [("旧回复", None)], "既有消息必须保留，新列为 NULL"

    # 新写入的思考内容可以落库。
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "update agent_message set reasoning_content='先看日志' where session_id='legacy'"
        )
        conn.commit()
        assert conn.execute(
            "select reasoning_content from agent_message where session_id='legacy'"
        ).fetchone() == ("先看日志",)


def test_reasoning_content_migration_is_reversible(tmp_path):
    db = tmp_path / "agent-reasoning-downgrade.db"
    cfg = config(db)
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS)

    engine = create_engine(f"sqlite:///{db}")
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("agent_message")}
    finally:
        engine.dispose()
    assert "reasoning_content" not in columns
