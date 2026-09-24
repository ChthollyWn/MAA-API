"""M2-04 初始迁移验收：0001 建出的 schema 与模型/§6 规格逐条一致，downgrade 可用。

测试纪律：

- 全程用 ``tmp_path`` 的临时库，经 ``Config.set_main_option("sqlalchemy.url", ...)``
  指过去；绝不触碰仓库里的 ``resource/maa_api.db``。
- 只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库，**不用** ``create_all``：本卡验的
  就是「生产建表路径」本身，两条路径混用会让 ``alembic_version`` 与 schema 漂移
  （docs/04 §8.3）。
- 目录期望值直接复用 ``tests/db/test_models.py`` 的 §6 规格常量，两边同一份清单，
  迁移文件与模型不可能各自漂移。
- 本仓没有 pytest-asyncio，全部用同步测试函数。
"""

import contextlib
import io
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  必须 import 才能注册全部表供比对
from tests.db.test_models import (
    AUTOINCREMENT_TABLES,
    BUSINESS_TABLES,
    EXPECTED_FK_CASCADE,
    EXPECTED_FK_SET_NULL,
    EXPECTED_INDEXES,
    EXPECTED_UNIQUE_CONSTRAINTS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "migration.db"


@pytest.fixture
def alembic_cfg(db_path) -> Config:
    """指向临时库的 Alembic 配置，与 M2-06 ensure_schema 走同一条路径。"""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def head_db(alembic_cfg, db_path) -> Path:
    command.upgrade(alembic_cfg, "head")
    return db_path


@pytest.fixture
def inspector(head_db):
    engine = create_engine(f"sqlite:///{head_db}")
    yield inspect(engine)
    engine.dispose()


@pytest.fixture
def raw(head_db):
    conn = sqlite3.connect(head_db)
    yield conn
    conn.close()


def ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("select sql from sqlite_master where name=?", (name,)).fetchone()
    assert row is not None and row[0], f"sqlite_master 里没有 {name}"
    return row[0].upper()


def table_names(db_path: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


# ---------------------------------------------------------------------------
# 建表：13 张表、列、索引
# ---------------------------------------------------------------------------
def test_upgrade_creates_all_business_tables(head_db):
    """13 张业务表全部建出，外加 Alembic 自己的版本表。"""
    names = table_names(head_db)
    assert BUSINESS_TABLES <= names, sorted(BUSINESS_TABLES - names)
    assert "alembic_version" in names
    assert set(SQLModel.metadata.tables) <= names


def test_columns_match_model_metadata(inspector):
    """每张表的列集合与模型 metadata 完全一致（不缺列、不多列）。"""
    for tname, table in SQLModel.metadata.tables.items():
        got = {col["name"] for col in inspector.get_columns(tname)}
        want = {col.name for col in table.columns}
        assert got == want, (tname, sorted(want - got), sorted(got - want))


def test_index_and_unique_constraint_inventory_matches_spec(inspector):
    """索引/唯一约束的名字、列序、唯一性都与 docs/04 §6 的规格一致。

    迁移文件是手写 SQL，autogenerate 又「不保证检测到索引重命名与部分索引」，
    所以这里做**全量**比对而不是子集断言；多一条少一条都算漂移。
    """
    expected = {
        name: (table, tuple(cols), True)
        for name, (table, cols) in EXPECTED_UNIQUE_CONSTRAINTS.items()
    }
    expected.update(EXPECTED_INDEXES)

    actual: dict[str, tuple[str, tuple[str, ...], bool]] = {}
    for tname in BUSINESS_TABLES:
        for ix in inspector.get_indexes(tname):
            actual[ix["name"]] = (
                tname,
                tuple(ix["column_names"]),
                bool(ix["unique"]),
            )
        for uq in inspector.get_unique_constraints(tname):
            actual[uq["name"]] = (tname, tuple(uq["column_names"]), True)

    assert actual == expected


def test_snippet_migration_creates_expected_columns_and_name_constraint(inspector):
    columns = {column["name"] for column in inspector.get_columns("api_snippet")}
    assert columns == {
        "id",
        "name",
        "method",
        "path",
        "path_params",
        "query",
        "headers",
        "body",
        "created_at",
        "updated_at",
    }
    unique = inspector.get_unique_constraints("api_snippet")
    assert unique == [{"name": "uq_api_snippet_name", "column_names": ["name"]}]


def test_partial_unique_index_keeps_predicate(inspector, raw):
    """`uq_update_record_running_target` 是带 sqlite_where 的部分唯一索引。

    两条断言：反射出的谓词原文；以及语义 —— 同 target 可以有很多历史行，但
    同时只能有一条 running。
    """
    index = {ix["name"]: ix for ix in inspector.get_indexes("update_record")}
    predicate = index["uq_update_record_running_target"]["dialect_options"].get(
        "sqlite_where"
    )
    # 反射回来的是 TextClause，取字符串原文比对
    assert str(predicate) == "status = 'running'"

    def insert(status: str) -> None:
        raw.execute(
            "insert into update_record"
            " (id, target, status, progress, triggered_by, created_at)"
            " values (?, 'game', ?, 0, 'api', '2026-01-01 00:00:00')",
            (uuid.uuid4().hex, status),
        )

    insert("success")
    insert("running")
    with pytest.raises(sqlite3.IntegrityError):
        insert("running")
    insert("failed")  # 非 running 的历史行不受限
    raw.commit()


# ---------------------------------------------------------------------------
# DDL 事实：AUTOINCREMENT / 外键级联 / server_default / CHECK
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_autoincrement_reaches_ddl(raw, table):
    """三张高频追加表的 DDL 里有 AUTOINCREMENT（缺了 id 会复用）。"""
    assert "AUTOINCREMENT" in ddl(raw, table)


def test_foreign_key_ondelete_matches_spec(inspector):
    """16 条外键的 ondelete 与 docs/04 §3.4 的映射一致。"""
    actual_cascade: set[tuple[str, str]] = set()
    actual_set_null: set[tuple[str, str]] = set()
    for tname in BUSINESS_TABLES:
        for fk in inspector.get_foreign_keys(tname):
            ondelete = (fk.get("options") or {}).get("ondelete")
            for col in fk["constrained_columns"]:
                if ondelete == "CASCADE":
                    actual_cascade.add((tname, col))
                elif ondelete == "SET NULL":
                    actual_set_null.add((tname, col))

    assert actual_cascade == EXPECTED_FK_CASCADE
    assert actual_set_null == EXPECTED_FK_SET_NULL


def test_task_foreign_key_cascade_reaches_ddl_and_pragma(raw):
    """级联不只是 metadata：DDL 与 PRAGMA foreign_key_list 都要看得到。"""
    assert "ON DELETE CASCADE" in ddl(raw, "task")
    fks = raw.execute("PRAGMA foreign_key_list('task')").fetchall()
    assert any(
        row[2] == "pipeline" and (row[6] or "").upper() == "CASCADE" for row in fks
    ), fks


def test_json_server_default_reaches_ddl(raw):
    """task.params 的 server_default 是 JSON 字面量 '{}'，且 NOT NULL。"""
    assert "DEFAULT '{}'" in ddl(raw, "task")


def test_check_constraint_reaches_ddl(raw):
    assert "CHECK" in ddl(raw, "resource_asset")
    assert "CONTENT IS NOT NULL OR PATH IS NOT NULL" in ddl(raw, "resource_asset")


def test_auto_vacuum_is_incremental(head_db):
    """M2-01 的落点：upgrade 完成后用新连接读 PRAGMA auto_vacuum == 2。

    值 2 = INCREMENTAL；写成 0 说明 pragma 又回到了「静默失效」的放置方式
    （例如被挪进 0001 的 upgrade() 首行）。
    """
    with contextlib.closing(sqlite3.connect(head_db)) as conn:
        assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 版本链与 downgrade
# ---------------------------------------------------------------------------
def test_head_revision_is_recorded_and_upgrade_is_idempotent(alembic_cfg, raw):
    """版本表被真正写入（不是「建完表但版本为空」），重复 upgrade 是空操作。

    head 从 ``ScriptDirectory`` 取而不是硬编码：每加一条迁移 head 都会后移
    （0002 数据迁移就是 M2-05 加的），硬编码序号会在每次加迁移时失效。
    """
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    assert raw.execute("select version_num from alembic_version").fetchall() == [(head,)]
    command.upgrade(alembic_cfg, "head")  # 不应抛 table already exists
    assert raw.execute("select version_num from alembic_version").fetchall() == [(head,)]


def test_downgrade_to_base_drops_every_table_then_upgrade_restores(alembic_cfg, head_db):
    """downgrade base 把 13 张表删干净，且能再次 upgrade（docs/12 §4）。"""
    command.downgrade(alembic_cfg, "base")
    left = table_names(head_db)
    assert not (BUSINESS_TABLES & left), sorted(BUSINESS_TABLES & left)
    with contextlib.closing(sqlite3.connect(head_db)) as conn:
        assert conn.execute("select version_num from alembic_version").fetchall() == []

    command.upgrade(alembic_cfg, "head")
    names = table_names(head_db)
    assert BUSINESS_TABLES <= names, sorted(BUSINESS_TABLES - names)


def test_offline_mode_renders_sql_without_creating_db(alembic_cfg, db_path):
    """env.py 的 run_migrations_offline 分支：`upgrade head --sql` 只打 SQL 不连库。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(alembic_cfg, "head", sql=True)
    sql = buffer.getvalue()

    assert "CREATE TABLE pipeline" in sql
    assert "AUTOINCREMENT" in sql
    assert "WHERE status = 'running'" in sql
    assert not db_path.exists(), "离线模式不应创建数据库文件"
