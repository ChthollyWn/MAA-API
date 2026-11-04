"""M78 迁移 0031 验收：9 条 playbook 覆盖行搬进 ``agent_skill``，降级可还原。

测试纪律与 ``test_knowledge_index_migration.py`` 相同：只用 Alembic 的
``upgrade`` / ``downgrade`` 建库，临时库在 ``tmp_path``，绝不碰
``resource/maa_api.db``；迁移里只搬数据、不改知识库的合并逻辑。
"""

import contextlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0030_knowledge_index"

KNOWLEDGE_INSERT = (
    "insert into agent_knowledge (id, kind, title, when_to_use, body, enabled,"
    " created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?)"
)
TIMESTAMP = "2026-01-01 00:00:00"

#: 升级前造 4 行：两条要搬（一条改了标题、一条只停用），两条留在知识库。
KNOWLEDGE_ROWS = (
    (
        "playbook-recruit",
        None,
        "公开招募实操（改）",
        "用户要你自动公开招募时",
        "招募正文",
        1,
        TIMESTAMP,
        TIMESTAMP,
    ),
    ("task-preconditions", None, None, None, None, 0, TIMESTAMP, TIMESTAMP),
    (
        "screen-home",
        None,
        "首页布局（改）",
        "需要识别首页时",
        "首页正文",
        1,
        TIMESTAMP,
        TIMESTAMP,
    ),
    (
        "my-playbook",
        "playbook",
        "我的剧本",
        "用户自己的条目",
        "自建正文",
        1,
        TIMESTAMP,
        TIMESTAMP,
    ),
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "skills.db"


@pytest.fixture
def alembic_cfg(db_path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _table_names(db_path: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


def _seed_previous_revision(alembic_cfg: Config, db_path: Path) -> None:
    command.upgrade(alembic_cfg, PREVIOUS_REVISION)
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(KNOWLEDGE_INSERT, KNOWLEDGE_ROWS)
        conn.commit()


def test_upgrade_moves_playbook_overrides_into_skills(alembic_cfg, db_path):
    """升级只搬 9 个 id：description 来自 when_to_use，停用状态保留，其余条目不动。"""
    _seed_previous_revision(alembic_cfg, db_path)

    command.upgrade(alembic_cfg, "head")

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        skills = conn.execute(
            "select id, origin, status, title, description, enabled, tools, agents"
            " from agent_skill order by id"
        ).fetchall()
        knowledge = [
            row[0]
            for row in conn.execute("select id from agent_knowledge order by id")
        ]

    assert skills == [
        (
            "playbook-recruit",
            "override",
            "active",
            "公开招募实操（改）",
            "用户要你自动公开招募时",
            1,
            None,
            None,
        ),
        (
            "task-preconditions",
            "override",
            "active",
            None,
            None,
            0,
            None,
            None,
        ),
    ]
    assert knowledge == ["my-playbook", "screen-home"]


def test_downgrade_restores_overrides_and_drops_table(alembic_cfg, db_path):
    """降级把两行按覆盖语义写回知识库（kind 为 NULL），然后删掉 agent_skill。"""
    _seed_previous_revision(alembic_cfg, db_path)
    command.upgrade(alembic_cfg, "head")

    command.downgrade(alembic_cfg, PREVIOUS_REVISION)

    assert "agent_skill" not in _table_names(db_path)
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "select id, kind, title, when_to_use, body, enabled from agent_knowledge"
            " order by id"
        ).fetchall()

    assert rows == [
        ("my-playbook", "playbook", "我的剧本", "用户自己的条目", "自建正文", 1),
        (
            "playbook-recruit",
            None,
            "公开招募实操（改）",
            "用户要你自动公开招募时",
            "招募正文",
            1,
        ),
        ("screen-home", None, "首页布局（改）", "需要识别首页时", "首页正文", 1),
        ("task-preconditions", None, None, None, None, 0),
    ]


def test_upgrade_on_empty_database(alembic_cfg, db_path):
    """空库从 base 升到 head：agent_skill 建出且为空，知识库无需回填。"""
    command.upgrade(alembic_cfg, "head")

    assert "agent_skill" in _table_names(db_path)
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("select count(*) from agent_skill").fetchone()[0] == 0
