"""M13 Task 4: incident keys are nullable, unique and forward-only in practice."""

import contextlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
REVISION = "0011_agent_incident_key"
PREVIOUS = "0010_agent_audit_mcp_scopes"


def config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_incident_key_migration_preserves_old_sessions_and_unique_keys(tmp_path):
    db = tmp_path / "agent-incidents.db"
    cfg = config(db)
    command.upgrade(cfg, PREVIOUS)

    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "insert into agent_session"
            " (id, title, status, model, base_url, message_count, prompt_tokens,"
            " completion_tokens, atomic_grant_id, atomic_grant_expires_at,"
            " created_at, updated_at, last_message_at)"
            " values ('legacy', 'old', 'active', 'test', null, 0, 0, 0, null, null,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00', null)"
        )
        conn.commit()

    command.upgrade(cfg, REVISION)
    engine = create_engine(f"sqlite:///{db}")
    try:
        inspector = inspect(engine)
        assert "incident_key" in {
            column["name"] for column in inspector.get_columns("agent_session")
        }
        index = next(
            item for item in inspector.get_indexes("agent_session")
            if item["name"] == "uq_agent_session_incident_key"
        )
        assert bool(index["unique"]) is True
        assert index["column_names"] == ["incident_key"]
    finally:
        engine.dispose()

    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "select incident_key from agent_session where id='legacy'"
        ).fetchone() == (None,)
        conn.execute(
            "insert into agent_session"
            " (id, title, status, model, message_count, prompt_tokens, completion_tokens,"
            " created_at, updated_at)"
            " values ('legacy-2', 'old two', 'active', 'test', 0, 0, 0,"
            " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )
        conn.execute(
            "insert into agent_session"
            " (id, title, status, model, message_count, prompt_tokens, completion_tokens,"
            " created_at, updated_at, incident_key)"
            " values ('incident-1', 'auto', 'active', 'test', 0, 0, 0,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'pipeline:p1')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into agent_session"
                " (id, title, status, model, message_count, prompt_tokens, completion_tokens,"
                " created_at, updated_at, incident_key)"
                " values ('incident-2', 'auto', 'active', 'test', 0, 0, 0,"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'pipeline:p1')"
            )
        conn.rollback()

    command.downgrade(cfg, PREVIOUS)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert "incident_key" not in {
            column[1] for column in conn.execute("pragma table_info(agent_session)")
        }
        assert conn.execute(
            "select id, title from agent_session where id='legacy'"
        ).fetchone() == ("legacy", "old")
        assert conn.execute(
            "select id, title from agent_session where id='legacy-2'"
        ).fetchone() == ("legacy-2", "old two")
        assert conn.execute(
            "select id, title from agent_session where id='incident-1'"
        ).fetchone() == ("incident-1", "auto")

    command.upgrade(cfg, REVISION)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "select incident_key from agent_session where id='legacy'"
        ).fetchone() == (None,)
        assert conn.execute(
            "select incident_key from agent_session where id='legacy-2'"
        ).fetchone() == (None,)
