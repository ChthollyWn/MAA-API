"""M76 迁移 0030 验收：三表、FTS5 虚拟表与触发器、升级保留旧数据、降级可再升级。

测试纪律与 ``test_initial_migration.py`` 相同：只用 Alembic 的 ``upgrade`` /
``downgrade`` 建库，临时库在 ``tmp_path``，绝不碰 ``resource/maa_api.db``；迁移里
不做查询，所以离线渲染另由 ``test_initial_migration.py`` 的用例覆盖。
"""

import contextlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests.db.test_models import FTS_OBJECTS, FTS_SHADOW_TABLES

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0029_artifacts_plans"

PIPELINE_INSERT = (
    "insert into pipeline"
    " (id, core_id, source, priority, status, task_count, notify_on_finish, created_at)"
    " values ('pipeline-1', 'default', 'manual', 0, 'pending', 1, 1,"
    " '2026-01-01 00:00:00')"
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "knowledge_index.db"


@pytest.fixture
def alembic_cfg(db_path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _object_types(db_path: Path, names: set[str]) -> dict[str, str]:
    params = ", ".join("?" * len(names))
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return dict(
            conn.execute(
                f"select name, type from sqlite_master where name in ({params})",
                tuple(names),
            ).fetchall()
        )


def _table_names(db_path: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


def test_upgrade_adds_tables_fts_and_keeps_rows(alembic_cfg, db_path):
    """先升到 0029 造一条流水线；升到 0030 后三表、四个 FTS 对象都在，流水线还在。"""
    command.upgrade(alembic_cfg, PREVIOUS_REVISION)
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute(PIPELINE_INSERT)
        conn.commit()
    assert _object_types(db_path, set(FTS_OBJECTS)) == {}

    # 钉在 0030：M78 的 0031 是 head 之后，这里只验收 M76 自己的迁移。
    command.upgrade(alembic_cfg, "0030_knowledge_index")

    assert {"knowledge_source", "knowledge_document", "knowledge_chunk"} <= _table_names(
        db_path
    )
    assert _object_types(db_path, set(FTS_OBJECTS)) == FTS_OBJECTS
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("select id from pipeline").fetchall() == [("pipeline-1",)]
        assert conn.execute("select version_num from alembic_version").fetchall() == [
            ("0030_knowledge_index",)
        ]


def test_triggers_keep_fts_in_sync_through_cascade(alembic_cfg, db_path):
    """删除文档时级联删分块，``AFTER DELETE`` 触发器同步清掉全文索引。"""
    command.upgrade(alembic_cfg, "head")
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "insert into knowledge_source (id, kind, title, enabled, trusted, config,"
            " status, doc_count, chunk_count, created_at, updated_at)"
            " values ('builtin', 'builtin', '内置知识', 1, 1, '{}', 'idle', 1, 1,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "insert into knowledge_document (id, source_id, external_id, title,"
            " content_hash, meta, created_at, updated_at)"
            " values ('doc-1', 'builtin', 'entry-1', '掉落与刷图效率', 'hash', '{}',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "insert into knowledge_chunk (document_id, ord, heading, text, char_count,"
            " created_at)"
            " values ('doc-1', 0, '常驻最优图的判断', '以 1-7 刷固源岩为推荐做法', 14,"
            " '2026-01-01 00:00:00')"
        )
        conn.commit()

        def matching_rowids() -> list[int]:
            return [
                row[0]
                for row in conn.execute(
                    "select rowid from knowledge_chunk_fts"
                    " where knowledge_chunk_fts match '\"固源岩\"'"
                )
            ]

        chunk_id = conn.execute("select id from knowledge_chunk").fetchone()[0]
        assert matching_rowids() == [chunk_id]

        conn.execute("delete from knowledge_document where id = 'doc-1'")
        conn.commit()

        assert conn.execute("select count(*) from knowledge_chunk").fetchone()[0] == 0
        assert matching_rowids() == []
        # 外部内容表的一致性校验：触发器漏写会在这一步报 database disk image is malformed。
        conn.execute("INSERT INTO knowledge_chunk_fts(knowledge_chunk_fts) VALUES('integrity-check')")


def test_downgrade_removes_everything_and_reupgrade_works(alembic_cfg, db_path):
    """降到 0029 后三表与 FTS 对象（含影子表）都没了，再升级成功。"""
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, PREVIOUS_REVISION)

    tables = _table_names(db_path)
    assert not ({"knowledge_source", "knowledge_document", "knowledge_chunk"} & tables)
    assert _object_types(db_path, set(FTS_OBJECTS) | FTS_SHADOW_TABLES) == {}
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("select version_num from alembic_version").fetchall() == [
            (PREVIOUS_REVISION,)
        ]

    command.upgrade(alembic_cfg, "head")
    assert {"knowledge_source", "knowledge_document", "knowledge_chunk"} <= _table_names(
        db_path
    )
    assert _object_types(db_path, set(FTS_OBJECTS)) == FTS_OBJECTS
