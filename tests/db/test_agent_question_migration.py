"""M21 Task 1: ``agent_question`` 是纯新增表；downgrade 只删表、不动已有数据。

提问与 ``confirmation`` 无关（规格 §2.2），所以升级不需要回填：旧库里的「待答」概念
只属于确认链路，历史会话没有提问。两个索引各服务一条固定查询，这里连同外键的
``ondelete`` 一起在真实迁移产物上钉死。
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
REVISION = "0015_agent_question"
PREVIOUS = "0014_agent_knowledge"

EXPECTED_COLUMNS = {
    "id": ("VARCHAR(36)", False),
    "session_id": ("VARCHAR(36)", False),
    "tool_call_id": ("VARCHAR(64)", False),
    "question": ("TEXT", False),
    "options": ("JSON", False),
    "multiple": ("BOOLEAN", False),
    "status": ("VARCHAR(16)", False),
    "selection": ("JSON", True),
    "answered_by": ("VARCHAR(32)", True),
    "audit_id": ("INTEGER", True),
    "expires_at": ("DATETIME", False),
    "created_at": ("DATETIME", False),
    "answered_at": ("DATETIME", True),
}

EXPECTED_INDEXES = {
    "ix_agent_question_status_expires_at": ("status", "expires_at"),
    "ix_agent_question_session_status_created_at": (
        "session_id",
        "status",
        "created_at",
    ),
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


def test_question_migration_adds_the_table_with_indexes_and_cascades(tmp_path):
    db = tmp_path / "agent-question.db"
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

    assert "agent_question" in table_names(db)
    engine = create_engine(f"sqlite:///{db}")
    try:
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("agent_question")}
        assert set(columns) == set(EXPECTED_COLUMNS)
        for name, (type_name, nullable) in EXPECTED_COLUMNS.items():
            assert str(columns[name]["type"]).upper() == type_name, name
            assert columns[name]["nullable"] is nullable, name
        assert inspector.get_pk_constraint("agent_question")["constrained_columns"] == [
            "id"
        ]
        indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("agent_question")
        }
        assert indexes == EXPECTED_INDEXES
        assert inspector.get_unique_constraints("agent_question") == []
        ondelete = {
            fk["constrained_columns"][0]: (fk["referred_table"], fk["options"]["ondelete"])
            for fk in inspector.get_foreign_keys("agent_question")
        }
        assert ondelete == {
            "session_id": ("agent_session", "CASCADE"),
            "audit_id": ("agent_audit", "SET NULL"),
        }
    finally:
        engine.dispose()

    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "insert into agent_session"
            " (id, status, model, message_count, prompt_tokens, completion_tokens,"
            " created_at, updated_at)"
            " values ('session-1', 'active', 'test-model', 0, 0, 0,"
            " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )
        conn.execute(
            "insert into agent_question"
            " (id, session_id, tool_call_id, question, options, multiple, status,"
            " expires_at, created_at)"
            " values ('question-1', 'session-1', 'call-1', '走哪个渠道？',"
            " '[{\"label\": \"官服\"}, {\"label\": \"B 服\"}]', 0, 'pending',"
            " '2026-01-02 00:10:00', '2026-01-02 00:00:00')"
        )
        conn.commit()
        # options 是 JSON：原样读回整个数组（§7 整体赋值）。
        assert conn.execute(
            "select options, selection, answered_at from agent_question"
            " where id='question-1'"
        ).fetchone() == ('[{"label": "官服"}, {"label": "B 服"}]', None, None)

        # 外键生效：未知会话插不进去；删会话级联删掉它的提问。
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into agent_question"
                " (id, session_id, tool_call_id, question, options, multiple, status,"
                " expires_at, created_at)"
                " values ('question-2', 'no-such-session', 'call-2', 'q', '[]', 0,"
                " 'pending', '2026-01-02 00:10:00', '2026-01-02 00:00:00')"
            )
        conn.rollback()
        conn.execute("delete from agent_session where id='session-1'")
        conn.commit()
        assert conn.execute("select count(*) from agent_question").fetchone() == (0,)

        assert conn.execute(
            "select status from pipeline where id='legacy'"
        ).fetchone() == ("pending",)

    command.downgrade(cfg, PREVIOUS)
    assert "agent_question" not in table_names(db)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "select status from pipeline where id='legacy'"
        ).fetchone() == ("pending",)
        assert conn.execute("select version_num from alembic_version").fetchall() == [
            (PREVIOUS,)
        ]

    # 降级后再升级：表回来且是空的，说明没有假装回填历史提问。
    command.upgrade(cfg, REVISION)
    assert "agent_question" in table_names(db)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("select count(*) from agent_question").fetchone() == (0,)
