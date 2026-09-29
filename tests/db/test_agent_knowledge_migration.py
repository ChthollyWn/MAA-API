"""M18 Task 4: ``agent_knowledge`` is an additive table; downgrade only drops it.

migration ``0014`` 只新增一张表，不改写任何已有数据 —— 覆盖行与自定义条目都靠
「列为 NULL 就是没覆盖」在读取侧合并（规格 §4.1），所以升级不需要回填。
"""

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
REVISION = "0014_agent_knowledge"
PREVIOUS = "0013_agent_message_reasoning"

EXPECTED_COLUMNS = {
    "id": ("VARCHAR(48)", False),
    "kind": ("VARCHAR(16)", True),
    "title": ("VARCHAR(32)", True),
    "when_to_use": ("VARCHAR(120)", True),
    "body": ("TEXT", True),
    "enabled": ("BOOLEAN", False),
    "created_at": ("DATETIME", False),
    "updated_at": ("DATETIME", False),
}


def config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def table_names(db_path: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


def test_knowledge_migration_adds_the_table_without_touching_existing_rows(tmp_path):
    db = tmp_path / "agent-knowledge.db"
    cfg = config(db)
    command.upgrade(cfg, PREVIOUS)

    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "insert into pipeline"
            " (id, core_id, source, priority, status, task_count, notify_on_finish,"
            " created_at)"
            " values ('legacy', 'default', 'manual', 0, 'pending', 1, 1,"
            " '2026-01-01 00:00:00')"
        )
        conn.commit()

    command.upgrade(cfg, REVISION)

    assert "agent_knowledge" in table_names(db)
    engine = create_engine(f"sqlite:///{db}")
    try:
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("agent_knowledge")}
        assert set(columns) == set(EXPECTED_COLUMNS)
        for name, (type_name, nullable) in EXPECTED_COLUMNS.items():
            assert str(columns[name]["type"]).upper() == type_name, name
            assert columns[name]["nullable"] is nullable, name
        assert inspector.get_pk_constraint("agent_knowledge")["constrained_columns"] == [
            "id"
        ]
        # 表里最多几十行、每轮全量读，不建索引（docs/04 §6 不额外发明）
        assert inspector.get_indexes("agent_knowledge") == []
        assert inspector.get_unique_constraints("agent_knowledge") == []
        assert inspector.get_foreign_keys("agent_knowledge") == []
    finally:
        engine.dispose()

    with contextlib.closing(sqlite3.connect(db)) as conn:
        # 自定义条目：所有列都填
        conn.execute(
            "insert into agent_knowledge"
            " (id, kind, title, when_to_use, body, enabled, created_at, updated_at)"
            " values ('custom-entry', 'playbook', '自定义', '需要时', '正文', 1,"
            " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )
        # 覆盖行：只写与内置不同的字段，其余为 NULL
        conn.execute(
            "insert into agent_knowledge"
            " (id, kind, title, when_to_use, body, enabled, created_at, updated_at)"
            " values ('screen-home', null, null, null, null, 1,"
            " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )
        conn.commit()
        assert conn.execute(
            "select kind, title, when_to_use, body, enabled from agent_knowledge"
            " where id='screen-home'"
        ).fetchone() == (None, None, None, None, 1)
        assert conn.execute(
            "select body from agent_knowledge where id='custom-entry'"
        ).fetchone() == ("正文",)

        # enabled 非空、id 是主键
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into agent_knowledge"
                " (id, enabled, created_at, updated_at)"
                " values ('null-enabled', null, '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into agent_knowledge"
                " (id, enabled, created_at, updated_at)"
                " values ('screen-home', 1, '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
            )
        conn.rollback()

        assert conn.execute(
            "select status from pipeline where id='legacy'"
        ).fetchone() == ("pending",)

    command.downgrade(cfg, PREVIOUS)
    assert "agent_knowledge" not in table_names(db)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "select status from pipeline where id='legacy'"
        ).fetchone() == ("pending",)
        assert conn.execute("select version_num from alembic_version").fetchall() == [
            (PREVIOUS,)
        ]

    # 降级后再升级：表回来，且是空的（没有假装回填内置内容）
    command.upgrade(cfg, REVISION)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("select count(*) from agent_knowledge").fetchone() == (0,)
        assert conn.execute(
            "select status from pipeline where id='legacy'"
        ).fetchone() == ("pending",)
