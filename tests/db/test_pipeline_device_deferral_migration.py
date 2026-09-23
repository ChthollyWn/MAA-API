"""M6-05 migration coverage: 0003 upgrades fresh and existing M2 databases."""

import contextlib
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
REVISION = "0003_pipeline_device_deferral"
PREVIOUS = "0002"


def config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def version(db_path: Path) -> str:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("select version_num from alembic_version").fetchone()[0]


def columns(db_path: Path) -> set[str]:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return {col["name"] for col in inspect(engine).get_columns("pipeline")}
    finally:
        engine.dispose()


def test_fresh_upgrade_adds_deferral_columns_and_repeat_is_safe(tmp_path):
    db = tmp_path / "fresh.db"
    cfg = config(db)

    command.upgrade(cfg, "head")
    assert version(db) == REVISION
    assert {"deferred_until", "defer_count"} <= columns(db)

    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "insert into pipeline"
            " (id, core_id, source, priority, status, task_count, notify_on_finish, created_at)"
            " values ('p1', 'default', 'scheduled', 2, 'pending', 1, 1, '2026-01-01 00:00:00')"
        )
        assert conn.execute(
            "select deferred_until, defer_count from pipeline where id='p1'"
        ).fetchone() == (None, 0)

    command.upgrade(cfg, "head")
    assert version(db) == REVISION


def test_existing_m2_rows_survive_upgrade_downgrade_and_reupgrade(tmp_path):
    db = tmp_path / "existing-m2.db"
    cfg = config(db)
    command.upgrade(cfg, PREVIOUS)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "insert into pipeline"
            " (id, core_id, source, priority, status, task_count, notify_on_finish, created_at)"
            " values ('existing', 'default', 'scheduled', 2, 'pending', 1, 1, '2026-01-01 00:00:00')"
        )
        conn.commit()

    command.upgrade(cfg, "head")
    assert version(db) == REVISION
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "select deferred_until, defer_count from pipeline where id='existing'"
        ).fetchone() == (None, 0)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, PREVIOUS)
    assert version(db) == PREVIOUS
    assert not ({"deferred_until", "defer_count"} & columns(db))
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("select id from pipeline where id='existing'").fetchone() == (
            "existing",
        )

    command.upgrade(cfg, "head")
    assert version(db) == REVISION
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "select deferred_until, defer_count from pipeline where id='existing'"
        ).fetchone() == (None, 0)
