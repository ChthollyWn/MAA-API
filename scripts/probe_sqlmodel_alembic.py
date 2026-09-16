#!/usr/bin/env python3
"""M2-01 方案前置实测：SQLModel + Alembic 在本机 SQLite 上的 DDL 能力边界。

本脚本是**丢弃式探针**，不是生产代码，也不产出 ``maa_api/db/`` 下的任何东西。
它把 ``docs/04-数据模型与持久化.md`` 里几条「未经实测」的 DDL 假设问清楚，
结论供 M2-02（依赖与 session）/ M2-03（models）/ M2-04（迁移链）直接引用。

做五组实验：

1. **命名约定 + 建表 DDL**：在任何表定义之前设置 ``SQLModel.metadata.naming_convention``，
   定义一张带复合索引、部分唯一索引（``sqlite_where``）、两种外键级联
   （CASCADE / SET NULL）、JSON 列与 ``__table_args__={'sqlite_autoincrement': True}``
   的表，``create_all`` 到临时库后读 ``sqlite_master`` 的 DDL 原文逐项核对；
   另做两个对照：表定义之后才设置命名约定、以及去掉 ``sqlite_autoincrement``。
2. **``auto_vacuum=INCREMENTAL`` 的四种放置方式**：每种都在全新临时库里真跑一次
   ``alembic upgrade head``，升级后用**新连接**读 ``PRAGMA auto_vacuum`` 的整数值，
   并核对 ``alembic_version`` 表里是否有且仅有预期 revision 这一行（M2-14 补：只查
   「表存在」会放过「版本行被回滚」的坑）。
   (a) 0001 ``upgrade()`` 首行、(b) ``env.py`` 的 ``run_migrations_online()`` 里
   ``context.begin_transaction()`` 之前（pragma + ``conn.commit()``，M2-14 修正后）、
   (c) 0001 的 ``op.get_context().autocommit_block()``、
   (d) 对照：同 (b) 但不 ``conn.commit()``（M2-04 实测踩到的缺陷形态）。
   四种都拿不到 2 时继续试可行做法（pragma+VACUUM / 迁移前裸 sqlite3 连接）。
3. **``--autogenerate`` 保真度**：用 docs/04 §8.2 的 env.py 配置
   （``render_as_batch=True, compare_type=True, compare_server_default=True``）
   分别对「空库」与「已由 create_all 建好的库」跑 autogenerate，逐条比生成物与模型。
4. **batch + downgrade**：手写 0002 用 ``op.batch_alter_table(recreate="always")``
   加列 + 改列类型，``upgrade head`` 后 ``downgrade -1``，核对表结构回到上一版；
   并对比「纯反射」与 ``copy_from=<模型 Table>`` 两种 batch 重建对 ``AUTOINCREMENT``
   与命名外键的保留情况（这是本卡最重要的意外发现之一）。
5. **greenlet / 异步引擎**：``create_async_engine('sqlite+aiosqlite:///<临时库>')``
   真跑一次 ``connect + execute('select 1')``；本卡不安装 greenlet，失败原因原样记录，
   供 M2-02 决定依赖。

产物（默认写入 ``tests/fixtures/``）：

- ``db_probe_result.json``：机器可读的实测结果（含各验收布尔项）；
- ``db_probe_findings.md``：给 M2-02/M2-03/M2-04 引用的人读结论。

所有实验都在 ``tempfile.mkdtemp()`` 内完成，**不会**在仓库内生成 ``alembic.ini`` /
``migrations/`` / ``*.db``。``--help`` 由 argparse 在任何实验之前处理。

用法::

    .venv/bin/python scripts/probe_sqlmodel_alembic.py
    .venv/bin/python scripts/probe_sqlmodel_alembic.py --keep-temp
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = Path(__file__).resolve()
DEFAULT_OUT_JSON = REPO_ROOT / "tests" / "fixtures" / "db_probe_result.json"
DEFAULT_OUT_MD = REPO_ROOT / "tests" / "fixtures" / "db_probe_findings.md"

# docs/04 §8.1 的命名约定。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
PARTIAL_PREDICATE = "status = 'running'"
AUTO_VACUUM_PRAGMA = "PRAGMA auto_vacuum=INCREMENTAL"

from sqlalchemy import (  # noqa: E402
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlmodel import Field, SQLModel  # noqa: E402

# ⚠️ 必须在任何表定义之前设置 —— 这正是实验 1 要验证的写法。
SQLModel.metadata.naming_convention = NAMING_CONVENTION


def utcnow() -> datetime:
    """库内统一时间源（UTC naive），与 docs/04 §3.2 一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


class ProbeParent(SQLModel, table=True):
    """对照 docs/04 的表：UUID 主键 + 复合索引 + 部分唯一索引 + JSON 列。"""

    __tablename__ = "probe_parent"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=36)
    status: str = Field(default="pending", max_length=16)
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON, nullable=False, server_default=text("'{}'")),
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column("created_at", DateTime, nullable=False))

    __table_args__ = (
        # 无名 Index：名字由 naming_convention 的 ix 模板生成
        Index(None, "status", "created_at"),
        # 部分唯一索引：sqlite_where 只在 SQLite 上生效，名字按 docs/04 §6 手工给 uq_ 前缀
        Index("uq_probe_parent_running_status", "status", unique=True, sqlite_where=text(PARTIAL_PREDICATE)),
        # 无名 UniqueConstraint：名字由 uq 模板生成
        UniqueConstraint("status", "created_at"),
    )


class ProbeChild(SQLModel, table=True):
    """对照 docs/04 §3.1 的高频追加表：INTEGER PRIMARY KEY AUTOINCREMENT + 两种级联。"""

    __tablename__ = "probe_child"

    id: int | None = Field(default=None, primary_key=True)
    parent_id: str = Field(foreign_key="probe_parent.id", ondelete="CASCADE", max_length=36)
    auditor_id: str | None = Field(default=None, foreign_key="probe_parent.id", ondelete="SET NULL", max_length=36)
    label: str = Field(default="", max_length=16)

    __table_args__ = {"sqlite_autoincrement": True}


# --------------------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def alembic_version() -> str:
    import alembic

    return str(alembic.__version__)


def _sqlite_rows(db_path: Path, query: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _sqlite_master(db_path: Path) -> list[dict]:
    return _sqlite_rows(db_path, "select type, name, tbl_name, sql from sqlite_master order by type, name")


def _ddl_map(db_path: Path) -> dict[str, str]:
    return {str(row["name"]): str(row["sql"] or "") for row in _sqlite_master(db_path)}


def _read_auto_vacuum(db_path: Path) -> int:
    """用一条全新连接读库头里的 auto_vacuum（0=NONE / 1=FULL / 2=INCREMENTAL）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("PRAGMA auto_vacuum").fetchone()[0])
    finally:
        conn.close()


def _row_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])
    finally:
        conn.close()


# --------------------------------------------------------------------------------------
# 实验 1：命名约定 + 建表 DDL
# --------------------------------------------------------------------------------------


def _late_naming_convention_control() -> dict:
    """对照：把命名约定放在**表定义之后**才设置，看约束名是否跟随。"""
    md = MetaData()

    class _LateBase(SQLModel):
        metadata = md

    class LateParent(_LateBase, table=True):
        __tablename__ = "probe_late_parent"
        id: str = Field(primary_key=True, max_length=36)
        status: str = Field(max_length=16)

    class LateChild(_LateBase, table=True):
        __tablename__ = "probe_late_child"
        id: int | None = Field(default=None, primary_key=True)
        parent_id: str = Field(foreign_key="probe_late_parent.id", ondelete="CASCADE", max_length=36)
        status: str = Field(max_length=16)
        __table_args__ = (
            Index(None, "parent_id", "status"),
            UniqueConstraint("parent_id", "status"),
        )

    # ⚠️ 表已经定义完了才设置命名约定
    md.naming_convention = NAMING_CONVENTION

    engine = create_engine("sqlite://")
    try:
        md.create_all(engine)
        with engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.exec_driver_sql("select name, sql from sqlite_master order by name")]
    finally:
        engine.dispose()
    ddl = {str(r["name"]): str(r["sql"] or "") for r in rows}
    combined = "\n".join(ddl.values())
    expected = [
        "ix_probe_late_child_parent_id_status",
        "uq_probe_late_child_parent_id_status",
        "fk_probe_late_child_parent_id_probe_late_parent",
        "pk_probe_late_parent",
    ]
    missing = [name for name in expected if name not in combined]
    return {
        "applied": not missing,
        "expected_names": expected,
        "missing": missing,
        "ddl": ddl,
    }


def _autoincrement_without_flag_control() -> dict:
    """对照：同样的 int 主键，但不写 ``__table_args__={'sqlite_autoincrement': True}``。"""
    md = MetaData()

    class _NoFlagBase(SQLModel):
        metadata = md

    class NoFlagChild(_NoFlagBase, table=True):
        __tablename__ = "probe_no_autoinc"
        id: int | None = Field(default=None, primary_key=True)
        label: str = Field(max_length=16)

    engine = create_engine("sqlite://")
    try:
        md.create_all(engine)
        with engine.connect() as conn:
            sql = conn.exec_driver_sql(
                "select sql from sqlite_master where type='table' and name='probe_no_autoinc'"
            ).scalar()
    finally:
        engine.dispose()
    sql = str(sql or "")
    return {"emitted": "AUTOINCREMENT" in sql.upper(), "ddl": sql}


def probe_models_ddl(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "schema_probe.db"
    engine = create_engine(f"sqlite:///{db}")
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()

    ddl = _ddl_map(db)
    child_ddl = ddl.get("probe_child", "")
    parent_ddl = ddl.get("probe_parent", "")
    partial_ddl = ddl.get("uq_probe_parent_running_status", "")
    combined = "\n".join(ddl.values())

    expected_names = [
        "ix_probe_parent_status_created_at",  # ix 模板: ix_<table>_<col0>_..._<colN>
        "uq_probe_parent_status_created_at",  # uq 模板（无名 UniqueConstraint）
        "uq_probe_parent_running_status",  # 部分唯一索引，手工命名
        "fk_probe_child_parent_id_probe_parent",  # fk 模板
        "fk_probe_child_auditor_id_probe_parent",  # fk 模板（SET NULL 那条）
        "pk_probe_parent",  # pk 模板
    ]
    missing_names = [name for name in expected_names if name not in combined]

    return {
        "naming_convention": NAMING_CONVENTION,
        "naming_convention_applied": not missing_names,
        "naming_convention_missing_names": missing_names,
        "naming_convention_late_control": _late_naming_convention_control(),
        "autoincrement_emitted": "AUTOINCREMENT" in child_ddl.upper(),
        "autoincrement_writing": (
            "id: int | None = Field(default=None, primary_key=True) + "
            "__table_args__ = {'sqlite_autoincrement': True}"
        ),
        "autoincrement_without_flag_control": _autoincrement_without_flag_control(),
        "partial_index_emitted": "WHERE" in partial_ddl.upper() and PARTIAL_PREDICATE in partial_ddl,
        "partial_index_ddl": partial_ddl,
        "foreign_key_cascade_emitted": "ON DELETE CASCADE" in child_ddl and "ON DELETE SET NULL" in child_ddl,
        "json_server_default_emitted": "DEFAULT '{}'" in parent_ddl,
        "sqlite_sequence_present": any(row["name"] == "sqlite_sequence" for row in _sqlite_master(db)),
        "schema_ddl": ddl,
        "schema_db_is_temp": True,
    }


# --------------------------------------------------------------------------------------
# Alembic 临时脚手架
# --------------------------------------------------------------------------------------

SCRIPT_PY_MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''

# 与 docs/04 §8.2 一致的 env.py；@@PLACEMENT_BLOCK@@ 是 auto_vacuum 放置实验的注入点。
ENV_PY_TEMPLATE = '''\
"""由 scripts/probe_sqlmodel_alembic.py 生成的丢弃式 Alembic env.py（只在临时目录内）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

REPO_ROOT = Path(@@REPO_ROOT@@)
PROBE_SCRIPT = Path(@@PROBE_SCRIPT@@)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 进程内运行时，探针模块作为 __main__ 已经把表注册到 SQLModel.metadata；
# 独立进程（如 alembic CLI）下按文件路径补加载一次。
if "probe_child" not in SQLModel.metadata.tables:
    _spec = importlib.util.spec_from_file_location("_probe_ddl_models", PROBE_SCRIPT)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["_probe_ddl_models"] = _module
    _spec.loader.exec_module(_module)

target_metadata = SQLModel.metadata
config = context.config
DB_URL = config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(DB_URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
@@PLACEMENT_BLOCK@@
        context.configure(
            connection=conn,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

PLACEMENT_NONE = "        pass  # 本场景不在 env.py 注入额外语句"
# M2-14 修正：M2-01 原记录只写了 pragma 那一行、漏了 commit；M2-04 照抄后真实踩到
# 「表建好了但 alembic_version 行被回滚」。正确形态是 pragma 之后必须提交。
PLACEMENT_ENV_PRAGMA = (
    '        conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")\n'
    '        conn.commit()  # 必须：不 commit 则 alembic_version 行被回滚（M2-04 实测）'
)
# 对照场景：M2-01 原记录里的不完整写法（pragma 后不 commit），用来实测出具体现象。
PLACEMENT_ENV_PRAGMA_NO_COMMIT = '        conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")'
PLACEMENT_ENV_PRAGMA_VACUUM = (
    '        conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")\n'
    '        conn.exec_driver_sql("VACUUM")'
)

# 每个 auto_vacuum 场景都会 upgrade 到这个 revision，并核对 alembic_version 行（M2-14 补）。
EXPECTED_REVISION = "0001"

REVISION_TEMPLATE = '''"""{doc}"""

from alembic import op
import sqlalchemy as sa


revision = {rev!r}
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
{upgrade}


def downgrade() -> None:
{downgrade}
'''

MINIMAL_CREATE_TABLE = '''    op.create_table(
        "probe_min",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )'''


def _make_scaffold(root: Path, db_path: Path, placement_block: str, mako_sqlmodel_import: bool = False) -> Path:
    """在临时目录里生成最小 Alembic 工程，返回 alembic.ini 路径。"""
    migrations = root / "migrations"
    (migrations / "versions").mkdir(parents=True, exist_ok=True)
    mako_src = SCRIPT_PY_MAKO
    if mako_sqlmodel_import:
        # 修复候选：SQLModel 的 AutoString 会渲染成 sqlmodel.sql.sqltypes.AutoString，
        # 但 Autogenerate 不会自动补 import（见实验 3）。
        mako_src = mako_src.replace("import sqlalchemy as sa\n", "import sqlalchemy as sa\nimport sqlmodel\n", 1)
    (migrations / "script.py.mako").write_text(mako_src, encoding="utf-8")
    env_src = (
        ENV_PY_TEMPLATE.replace("@@REPO_ROOT@@", repr(str(REPO_ROOT)))
        .replace("@@PROBE_SCRIPT@@", repr(str(PROBE_SCRIPT)))
        .replace("@@PLACEMENT_BLOCK@@", placement_block)
    )
    (migrations / "env.py").write_text(env_src, encoding="utf-8")
    ini = root / "alembic.ini"
    ini.write_text(
        "[alembic]\n"
        f"script_location = {migrations}\n"
        f"sqlalchemy.url = sqlite:///{db_path}\n",
        encoding="utf-8",
    )
    return ini


def _write_revision(versions_dir: Path, filename: str, body: str) -> None:
    versions_dir.mkdir(parents=True, exist_ok=True)
    (versions_dir / filename).write_text(body, encoding="utf-8")


def _minimal_revision(pragma_mode: str | None) -> str:
    """exp2 用的最小 0001：只建一张两列的表。

    auto_vacuum 的取值取决于 pragma 执行时库内是否已有表，与表结构无关，
    因此这里刻意用最小表，避免把「pragma 放置」和「建表 DDL」两件事混在一起。
    """
    lines: list[str] = []
    if pragma_mode == "first_statement":
        lines.append('    op.execute("PRAGMA auto_vacuum=INCREMENTAL")')
    elif pragma_mode == "autocommit_block":
        lines.append("    with op.get_context().autocommit_block():")
        lines.append('        op.execute("PRAGMA auto_vacuum=INCREMENTAL")')
    lines.append(MINIMAL_CREATE_TABLE)
    return REVISION_TEMPLATE.format(
        doc="exp2 auto_vacuum placement probe",
        rev="0001",
        down=None,
        upgrade="\n".join(lines),
        downgrade='    op.drop_table("probe_min")',
    )


# --------------------------------------------------------------------------------------
# 实验 2：auto_vacuum 的三种放置方式
# --------------------------------------------------------------------------------------

AUTO_VACUUM_SCENARIOS = (
    # (名字, 0001 里的 pragma 模式, env.py 注入块)
    (
        "migration_first_statement",
        "first_statement",
        "        pass  # placement a: pragma 写在 0001 upgrade() 首行",
    ),
    (
        "env_py_before_begin_transaction",
        None,
        PLACEMENT_ENV_PRAGMA,
    ),
    (
        "migration_autocommit_block",
        "autocommit_block",
        "        pass  # placement c: pragma 写在 autocommit_block 里",
    ),
    (
        # M2-14 补的对照场景：M2-01 原记录漏了 commit，即这个形态。
        "env_py_before_begin_transaction_no_commit",
        None,
        PLACEMENT_ENV_PRAGMA_NO_COMMIT,
    ),
)

# 文档推荐的落点（M2-14 修正后带 commit），`auto_vacuum_placement` 优先取它。
RECOMMENDED_PLACEMENT = "env_py_before_begin_transaction"
NO_COMMIT_PLACEMENT = "env_py_before_begin_transaction_no_commit"

AUTO_VACUUM_FALLBACKS = (
    (
        "env_py_pragma_then_vacuum",
        None,
        PLACEMENT_ENV_PRAGMA_VACUUM,
        "none",
    ),
    (
        "raw_sqlite3_before_upgrade",
        None,
        PLACEMENT_NONE,
        "raw_sqlite3_pragma",
    ),
)


def _run_auto_vacuum_scenario(
    root: Path,
    name: str,
    pragma_mode: str | None,
    placement_block: str,
    pre_upgrade: str,
) -> tuple[int, dict]:
    from alembic import command
    from alembic.config import Config

    sroot = root / name
    db = sroot / "probe.db"
    ini = _make_scaffold(sroot, db, placement_block)
    _write_revision(sroot / "migrations" / "versions", "0001_probe.py", _minimal_revision(pragma_mode))

    note: dict[str, Any] = {"pragma_mode": pragma_mode, "placement_block": placement_block.strip(), "error": ""}
    if pre_upgrade == "raw_sqlite3_pragma":
        # 迁移之前，用裸 sqlite3 连接（autocommit）在空库上设置
        conn = sqlite3.connect(str(db), isolation_level=None)
        try:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        finally:
            conn.close()
        note["pre_upgrade"] = "raw sqlite3 连接在 alembic 之前执行 pragma（空库）"

    try:
        command.upgrade(Config(str(ini)), "head")
    except Exception as exc:  # noqa: BLE001 - 探针要把失败现象完整记下来
        note["error"] = f"{type(exc).__name__}: {exc}"
        note["traceback"] = traceback.format_exc()

    tables = sorted(row["name"] for row in _sqlite_master(db)) if db.exists() else []
    value = _read_auto_vacuum(db) if db.exists() else 0
    note["tables_after_upgrade"] = tables
    note["alembic_version_present"] = "alembic_version" in tables
    # M2-14 补：只查「表存在」挡不住版本行被回滚（M2-04 正是这样踩到坑的），
    # 必须用新连接核对 alembic_version 里确实有且仅有预期版本这一行。
    version_rows = (
        [str(row["version_num"]) for row in _sqlite_rows(db, "select version_num from alembic_version")]
        if note["alembic_version_present"]
        else []
    )
    note["alembic_version_rows"] = version_rows
    note["alembic_version_expected"] = EXPECTED_REVISION
    note["alembic_version_ok"] = version_rows == [EXPECTED_REVISION]
    note["pragma_value"] = value

    # 版本行丢失时，第二次 upgrade head 会从头重放并撞上 table already exists；
    # 版本行正确时它应当是无操作。两种结果都实测记录（M2-14 补）。
    if not note["error"]:
        try:
            command.upgrade(Config(str(ini)), "head")
            note["second_upgrade_ok"] = True
            note["second_upgrade_error"] = ""
            note["second_upgrade_pragma_value"] = _read_auto_vacuum(db)
        except Exception as exc:  # noqa: BLE001 - 失败现象就是这里的观测对象
            note["second_upgrade_ok"] = False
            note["second_upgrade_error"] = f"{type(exc).__name__}: {exc}"

    return value, note


def probe_auto_vacuum(root: Path) -> dict:
    placements: dict[str, int] = {}
    notes: dict[str, Any] = {}

    for name, pragma_mode, block in AUTO_VACUUM_SCENARIOS:
        value, note = _run_auto_vacuum_scenario(root, name, pragma_mode, block, "none")
        placements[name] = value
        notes[name] = note

    fallbacks_tried: list[str] = []
    if not any(value == 2 for value in placements.values()):
        # docs 的三种写法都拿不到 2 —— 必须试出真正可行的做法。
        for name, pragma_mode, block, pre_upgrade in AUTO_VACUUM_FALLBACKS:
            value, note = _run_auto_vacuum_scenario(root, name, pragma_mode, block, pre_upgrade)
            placements[name] = value
            notes[name] = note
            fallbacks_tried.append(name)

    effective_names = [name for name, value in placements.items() if value == 2]
    # 「生效」= pragma 拿到 2 **且** 版本行确实写进去了；只有前者会把 M2-04 的坑放过去。
    version_row_ok = {name: bool(notes.get(name, {}).get("alembic_version_ok")) for name in placements}
    usable_names = [name for name in effective_names if version_row_ok.get(name)]
    if version_row_ok.get(RECOMMENDED_PLACEMENT):
        chosen = RECOMMENDED_PLACEMENT
    elif usable_names:
        chosen = usable_names[0]
    elif effective_names:
        chosen = effective_names[0]
    else:
        chosen = ""
    return {
        "auto_vacuum_placements": placements,
        "auto_vacuum_placement": chosen,
        "auto_vacuum_effective": bool(effective_names),
        "auto_vacuum_version_row_ok": version_row_ok,
        "auto_vacuum_placement_usable": bool(usable_names),
        "auto_vacuum_version_rows": {
            name: notes.get(name, {}).get("alembic_version_rows") for name in placements
        },
        "auto_vacuum_notes": notes,
        "auto_vacuum_fallbacks_tried": fallbacks_tried,
        "auto_vacuum_ineffective_names": [name for name, value in placements.items() if value != 2],
    }


# --------------------------------------------------------------------------------------
# 实验 3：autogenerate 保真度
# --------------------------------------------------------------------------------------


def _run_autogenerate(root: Path, db_path: Path, tag: str, mako_sqlmodel_import: bool = False) -> dict:
    from alembic import command
    from alembic.config import Config

    sroot = root / tag
    ini = _make_scaffold(sroot, db_path, PLACEMENT_NONE, mako_sqlmodel_import=mako_sqlmodel_import)
    versions = sroot / "migrations" / "versions"
    out: dict[str, Any] = {"tag": tag, "ran": False, "error": "", "migration_text": "", "file": "", "ini": str(ini)}
    try:
        command.revision(Config(str(ini)), message=f"probe {tag}", autogenerate=True)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()
        return out
    files = sorted(path for path in versions.glob("*.py"))
    if not files:
        out["error"] = "autogenerate 未生成任何迁移文件"
        return out
    out["ran"] = True
    out["file"] = str(files[0].name)
    out["migration_text"] = files[0].read_text(encoding="utf-8")
    return out


def _apply_generated(run: dict, db_path: Path) -> dict:
    """把 autogenerate 产物真跑一次 upgrade head，验证产物自身能不能用。"""
    from alembic import command
    from alembic.config import Config

    out: dict[str, Any] = {"applied": False, "error": "", "table_ddl": {}}
    if not run.get("ran"):
        out["error"] = "autogenerate 没有产出可执行的迁移文件"
        return out
    try:
        command.upgrade(Config(str(run["ini"])), "head")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["applied"] = True
    out["table_ddl"] = _ddl_map(db_path)
    return out


def _summarize_generated(text: str) -> dict:
    # 只看 upgrade()，downgrade() 里的 drop_* 不是 autogenerate 的「漏报」。
    upgrade_body = text.split("def downgrade", 1)[0]

    def _names(pattern: str, source: str) -> list[str]:
        return re.findall(pattern, source)

    # op.f(...) / batch_op.f(...) 只是命名约定的包装，不是迁移操作本身。
    ops = [op for op in re.findall(r"(?:op|batch_op)\.([a-z_]+)\(", upgrade_body) if op != "f"]
    return {
        "ops": ops,
        "create_tables": _names(r"op\.create_table\(\s*['\"]([^'\"]+)['\"]", upgrade_body),
        "create_indexes": _names(
            r"(?:op|batch_op)\.create_index\(\s*(?:(?:op|batch_op)\.f\()?['\"]([^'\"]+)['\"]", upgrade_body
        ),
        "has_sqlite_where": "sqlite_where" in upgrade_body,
        "has_partial_predicate": PARTIAL_PREDICATE in upgrade_body,
        "has_sqlite_autoincrement": "sqlite_autoincrement" in upgrade_body,
        "has_json_server_default": 'server_default=sa.text("\'{}\'")' in upgrade_body,
        "has_ondelete_cascade": "CASCADE" in upgrade_body,
        "has_fk_name": "fk_probe_child_parent_id_probe_parent" in upgrade_body,
        "has_pk_name": "pk_probe_parent" in upgrade_body,
        "has_composite_index_name": "ix_probe_parent_status_created_at" in upgrade_body,
        "has_autostring": "sqlmodel.sql.sqltypes.AutoString" in upgrade_body,
        "has_sqlmodel_import": "import sqlmodel" in text,
    }


def probe_autogenerate(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    diffs: list[str] = []

    # (1) 空库：autogenerate 从零生成全部对象，用来看它「漏了什么」。
    empty_db = root / "empty.db"
    empty = _run_autogenerate(root, empty_db, "empty_db")
    empty_summary = _summarize_generated(empty["migration_text"]) if empty["ran"] else {}
    empty_apply = _apply_generated(empty, empty_db)

    # (1b) 空库 + 把 `import sqlmodel` 写进 script.py.mako：验证修复候选。
    fixed_db = root / "empty_fixed.db"
    fixed = _run_autogenerate(root, fixed_db, "empty_db_mako_fix", mako_sqlmodel_import=True)
    fixed_summary = _summarize_generated(fixed["migration_text"]) if fixed["ran"] else {}
    fixed_apply = _apply_generated(fixed, fixed_db)
    applied_ddl = fixed_apply.get("table_ddl", {}) or {}
    applied_child_ddl = str(applied_ddl.get("probe_child", ""))
    applied_partial_ddl = str(applied_ddl.get("uq_probe_parent_running_status", ""))

    # (2) 已由 create_all 建好的库：用来看它「多报了什么」（server_default / JSON / 命名噪声）。
    matched_db = root / "matched.db"
    engine = create_engine(f"sqlite:///{matched_db}")
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    matched = _run_autogenerate(root, matched_db, "matched_db")
    matched_summary = _summarize_generated(matched["migration_text"]) if matched["ran"] else {}
    matched_ops = [op for op in matched_summary.get("ops", []) if op != "pass"]

    detected_partial = bool(
        empty_summary.get("has_sqlite_where") and empty_summary.get("has_partial_predicate")
    )

    if not empty["ran"]:
        diffs.append(f"空库 autogenerate 失败：{empty['error']}")
    else:
        ops = empty_summary["ops"]
        diffs.append(f"空库 autogenerate 的 upgrade() 操作序列：{', '.join(ops) if ops else '（无）'}")
        diffs.append(
            "create_table 覆盖：" + (", ".join(empty_summary["create_tables"]) or "（无）")
            + "；create_index 覆盖：" + (", ".join(empty_summary["create_indexes"]) or "（无）")
        )
        if detected_partial:
            diffs.append(
                "部分唯一索引 uq_probe_parent_running_status：生成物**带** sqlite_where 谓词 —— "
                f"docs/04 §8.2 说 autogenerate 检测不到部分索引，本次在 Alembic {alembic_version()} 上"
                "**未能复现**该结论（但仍需人工过一遍，见下）"
            )
        elif "uq_probe_parent_running_status" in empty_summary["create_indexes"]:
            diffs.append(
                "部分唯一索引 uq_probe_parent_running_status：生成了索引名但**丢失 sqlite_where 谓词**，"
                "部分唯一索引退化为全量唯一索引 —— 必须人工补 "
                'sqlite_where=sa.text("status = \'running\'")'
            )
        else:
            diffs.append(
                "部分唯一索引 uq_probe_parent_running_status：**完全没有生成** —— 必须人工补 "
                "op.create_index(..., unique=True, sqlite_where=sa.text(\"status = 'running'\"))"
            )
        if empty_summary["has_sqlite_autoincrement"]:
            diffs.append("probe_child 的 create_table：保留了 sqlite_autoincrement=True，AUTOINCREMENT 不丢")
        else:
            diffs.append(
                "probe_child 的 create_table：**丢失 sqlite_autoincrement=True**，"
                "生成物建出的表没有 AUTOINCREMENT —— 高频追加表（log_entry/agent_message/agent_audit）必须人工补"
            )
        diffs.append(
            "JSON 列的 server_default："
            + ("生成物带 server_default=sa.text(\"'{}'\")" if empty_summary["has_json_server_default"] else "生成物**丢失** server_default")
        )
        diffs.append(
            "命名约定："
            + (
                "生成物沿用了约定的索引/约束名（含 ix_probe_parent_status_created_at / "
                "fk_probe_child_parent_id_probe_parent / pk_probe_parent）"
                if empty_summary["has_composite_index_name"] and empty_summary["has_fk_name"]
                else "生成物**没有**沿用约定名（见 migration_text）"
            )
        )
        diffs.append(
            "外键级联："
            + ("生成物带 ondelete='CASCADE'（SET NULL 同理需人工核对）" if empty_summary["has_ondelete_cascade"] else "生成物**丢失** ondelete 级联")
        )
        if empty_summary.get("has_autostring"):
            diffs.append(
                "**SQLModel 类型 import 缺失**：字符串列被渲染成 "
                "`sqlmodel.sql.sqltypes.AutoString(length=...)`，而生成文件只有 `import sqlalchemy as sa`，"
                f"没有 `import sqlmodel`（生成物含 import sqlmodel = {empty_summary.get('has_sqlmodel_import')}）"
            )
        if empty_apply["applied"]:
            diffs.append("把 autogenerate 产物直接 `upgrade head`：可以跑通")
        else:
            diffs.append(
                f"把 autogenerate 产物直接 `upgrade head`：**失败**，原始报错 `{empty_apply['error']}`"
                " —— 产物开箱即用是不可行的，必须补 sqlmodel import 或改写类型渲染"
            )
        if not fixed["ran"]:
            diffs.append(f"script.py.mako 补 `import sqlmodel` 后重新 autogenerate 失败：{fixed['error']}")
        elif fixed_apply["applied"]:
            diffs.append(
                "修复验证：把 `import sqlmodel` 写进 `script.py.mako` 后重新 autogenerate，产物可以直接 "
                "`upgrade head`，且落库 DDL 里 "
                f"AUTOINCREMENT = {'在' if 'AUTOINCREMENT' in applied_child_ddl.upper() else '丢了'}、"
                f"部分索引 WHERE 谓词 = {'在' if 'WHERE' in applied_partial_ddl.upper() and PARTIAL_PREDICATE in applied_partial_ddl else '丢了'}"
            )
        else:
            diffs.append(
                f"修复验证：补 `import sqlmodel` 后产物仍然跑不通：`{fixed_apply['error']}`"
            )

    if not matched["ran"]:
        diffs.append(f"已建库 autogenerate 失败：{matched['error']}")
    elif not matched_ops:
        diffs.append("已建库 autogenerate：生成物为空操作（无 server_default / JSON / 命名 / 类型噪声）")
    else:
        diffs.append(
            "已建库 autogenerate：模型与库本应一致，却产生了噪声操作 —— "
            + ", ".join(dict.fromkeys(matched_ops))
        )

    return {
        "autogenerate_ran": bool(empty["ran"]),
        "autogenerate_detected_partial_index": detected_partial,
        "autogenerate_diffs": diffs,
        "autogenerate_ops": empty_summary.get("ops", []),
        "autogenerate_matched_db_ops": matched_ops,
        "autogenerate_generated_migration_applies": bool(empty_apply["applied"]),
        "autogenerate_apply_error": str(empty_apply["error"]),
        "autogenerate_mako_fix_applies": bool(fixed_apply["applied"]),
        "autogenerate_mako_fix_error": str(fixed_apply["error"]),
        "autogenerate_applied_autoincrement": "AUTOINCREMENT" in applied_child_ddl.upper(),
        "autogenerate_applied_partial_where": bool(
            "WHERE" in applied_partial_ddl.upper() and PARTIAL_PREDICATE in applied_partial_ddl
        ),
        "autogenerate_notes": {
            "empty_db": {key: value for key, value in empty.items() if key != "migration_text"},
            "empty_db_mako_fix": {key: value for key, value in fixed.items() if key != "migration_text"},
            "matched_db": {key: value for key, value in matched.items() if key != "migration_text"},
            "empty_db_summary": empty_summary,
            "empty_db_mako_fix_summary": fixed_summary,
            "matched_db_summary": matched_summary,
            "empty_db_apply": {key: value for key, value in empty_apply.items() if key != "table_ddl"},
            "empty_db_mako_fix_apply": {key: value for key, value in fixed_apply.items() if key != "table_ddl"},
            "definition": (
                "autogenerate_detected_partial_index = 生成物里存在 sqlite_where 且带 "
                f"谓词 {PARTIAL_PREDICATE!r}"
            ),
        },
    }


# --------------------------------------------------------------------------------------
# 实验 4：batch + downgrade
# --------------------------------------------------------------------------------------


def _batch_revision_0001() -> str:
    upgrade = '''    op.create_table(
        "probe_parent",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_probe_parent"),
        sa.UniqueConstraint("status", "created_at", name="uq_probe_parent_status_created_at"),
    )
    op.create_index(
        "ix_probe_parent_status_created_at", "probe_parent", ["status", "created_at"], unique=False
    )
    op.create_index(
        "uq_probe_parent_running_status",
        "probe_parent",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "probe_child",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=False),
        sa.Column("auditor_id", sa.String(length=36), nullable=True),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["probe_parent.id"],
            name="fk_probe_child_parent_id_probe_parent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["auditor_id"],
            ["probe_parent.id"],
            name="fk_probe_child_auditor_id_probe_parent",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_probe_child"),
        sqlite_autoincrement=True,
    )
    op.execute(
        "INSERT INTO probe_parent (id, status, payload, created_at) "
        "VALUES ('p1', 'pending', '{}', '2026-01-01 00:00:00')"
    )
    op.execute("INSERT INTO probe_child (parent_id, label) VALUES ('p1', 'x')")'''
    downgrade = '''    op.drop_table("probe_child")
    op.drop_table("probe_parent")'''
    return REVISION_TEMPLATE.format(
        doc="exp4 batch probe 0001: 建表（含 AUTOINCREMENT / 命名外键 / 部分唯一索引）",
        rev="0001",
        down=None,
        upgrade=upgrade,
        downgrade=downgrade,
    )


def _batch_revision_0002(use_copy_from: bool) -> str:
    if use_copy_from:
        # 修复候选：两个方向都传 copy_from，且 copy_from 的表结构必须与「迁移前」一致。
        # upgrade 前 = 模型当前结构；downgrade 前 = 0002 之后的库结构（多一列 note）。
        upgrade = (
            "    from sqlmodel import SQLModel\n\n"
            '    copy_from = SQLModel.metadata.tables["probe_child"]\n'
            '    with op.batch_alter_table("probe_child", copy_from=copy_from, recreate="always") as batch_op:\n'
            '        batch_op.add_column(sa.Column("note", sa.String(length=32), nullable=True))\n'
            "        batch_op.alter_column(\n"
            '            "label", existing_type=sa.String(length=16), type_=sa.String(length=24), existing_nullable=False\n'
            "        )"
        )
        downgrade = (
            "    from sqlalchemy import Column, MetaData, String\n"
            "    from sqlmodel import SQLModel\n\n"
            '    copy_from = SQLModel.metadata.tables["probe_child"].to_metadata(MetaData())\n'
            '    copy_from.append_column(Column("note", String(length=32), nullable=True))\n'
            '    with op.batch_alter_table("probe_child", copy_from=copy_from, recreate="always") as batch_op:\n'
            "        batch_op.alter_column(\n"
            '            "label", existing_type=sa.String(length=24), type_=sa.String(length=16), existing_nullable=False\n'
            "        )\n"
            '        batch_op.drop_column("note")'
        )
    else:
        upgrade = (
            '    with op.batch_alter_table("probe_child", recreate="always") as batch_op:\n'
            '        batch_op.add_column(sa.Column("note", sa.String(length=32), nullable=True))\n'
            "        batch_op.alter_column(\n"
            '            "label", existing_type=sa.String(length=16), type_=sa.String(length=24), existing_nullable=False\n'
            "        )"
        )
        downgrade = (
            '    with op.batch_alter_table("probe_child", recreate="always") as batch_op:\n'
            "        batch_op.alter_column(\n"
            '            "label", existing_type=sa.String(length=24), type_=sa.String(length=16), existing_nullable=False\n'
            "        )\n"
            '        batch_op.drop_column("note")'
        )
    return REVISION_TEMPLATE.format(
        doc=f"exp4 batch probe 0002: batch_alter_table(recreate='always', copy_from={use_copy_from})",
        rev="0002",
        down="0001",
        upgrade=upgrade,
        downgrade=downgrade,
    )


def _table_snapshot(db_path: Path, table: str) -> dict:
    table_sql = ""
    for row in _sqlite_master(db_path):
        if row["name"] == table and row["type"] == "table":
            table_sql = str(row["sql"] or "")
    return {
        "columns": _sqlite_rows(db_path, f"PRAGMA table_info({table})"),
        "foreign_keys": _sqlite_rows(db_path, f"PRAGMA foreign_key_list({table})"),
        "indexes": [
            row
            for row in _sqlite_rows(
                db_path, "select name, sql from sqlite_master where type='index' and tbl_name=?", (table,)
            )
            if not str(row["name"]).startswith("sqlite_autoindex")
        ],
        "table_sql": " ".join(table_sql.split()),
        "has_autoincrement": "AUTOINCREMENT" in table_sql.upper(),
    }


def _structure(snapshot: dict) -> dict:
    """结构指纹：列（名/类型/非空/默认值/主键位）、外键、显式索引。"""
    return {
        "columns": [
            [col["name"], str(col["type"] or "").upper(), int(col["notnull"]), col["dflt_value"], int(col["pk"])]
            for col in snapshot["columns"]
        ],
        "foreign_keys": sorted(
            [fk["table"], fk["from"], fk["to"], fk["on_delete"]] for fk in snapshot["foreign_keys"]
        ),
        "indexes": sorted([idx["name"], " ".join(str(idx["sql"] or "").split())] for idx in snapshot["indexes"]),
    }


def _run_batch_scenario(root: Path, tag: str, use_copy_from: bool) -> dict:
    from alembic import command
    from alembic.config import Config

    sroot = root / tag
    db = sroot / "probe.db"
    ini = _make_scaffold(sroot, db, PLACEMENT_NONE)
    versions = sroot / "migrations" / "versions"
    _write_revision(versions, "0001_probe.py", _batch_revision_0001())
    _write_revision(versions, "0002_probe_batch.py", _batch_revision_0002(use_copy_from))

    cfg = Config(str(ini))
    out: dict[str, Any] = {
        "scenario": tag,
        "copy_from": use_copy_from,
        "upgrade_ok": False,
        "downgrade_ok": False,
        "change_observed": False,
        "error": "",
    }
    try:
        command.upgrade(cfg, "0001")
        before = _table_snapshot(db, "probe_child")
        rows_before = _row_count(db, "probe_child")

        command.upgrade(cfg, "head")
        after_up = _table_snapshot(db, "probe_child")
        rows_after_up = _row_count(db, "probe_child")

        command.downgrade(cfg, "-1")
        after_down = _table_snapshot(db, "probe_child")

        out["upgrade_ok"] = True
        out["columns_after_upgrade"] = [col["name"] for col in after_up["columns"]]
        out["label_type_after_upgrade"] = next(
            (str(col["type"]) for col in after_up["columns"] if col["name"] == "label"), ""
        )
        out["change_observed"] = "note" in out["columns_after_upgrade"] and out["label_type_after_upgrade"].upper() == "VARCHAR(24)"
        out["upgrade_preserved_autoincrement"] = bool(after_up["has_autoincrement"])
        out["upgrade_preserved_fk_names"] = (
            "fk_probe_child_parent_id_probe_parent" in after_up["table_sql"]
            and "fk_probe_child_auditor_id_probe_parent" in after_up["table_sql"]
        )
        out["upgrade_preserved_rows"] = rows_after_up == rows_before and rows_before == 1
        out["downgrade_columns"] = [col["name"] for col in after_down["columns"]]
        out["downgrade_preserved_autoincrement"] = bool(after_down["has_autoincrement"])
        out["structure_restored"] = _structure(after_down) == _structure(before)
        out["downgrade_ok"] = bool(out["structure_restored"])
        out["before_table_sql"] = before["table_sql"]
        out["upgrade_table_sql"] = after_up["table_sql"]
        out["downgrade_table_sql"] = after_down["table_sql"]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()
    return out


def probe_batch_downgrade(root: Path) -> dict:
    reflection = _run_batch_scenario(root, "reflection_batch", use_copy_from=False)
    copy_from = _run_batch_scenario(root, "copy_from_batch", use_copy_from=True)
    notes = {
        "reflection_batch": reflection,
        "copy_from_batch": copy_from,
        "definition": (
            "batch_downgrade_ok = 0002 upgrade head 出现结构变化，且 downgrade -1 后 "
            "列/外键/显式索引结构指纹与 0001 之后完全一致（表级 AUTOINCREMENT 选项单独记录，"
            "因为它会被 batch 重建影响）"
        ),
    }
    return {
        "batch_downgrade_ok": bool(reflection["downgrade_ok"]),
        "batch_upgrade_ok": bool(reflection["upgrade_ok"]),
        "batch_change_observed": bool(reflection["change_observed"]),
        "batch_upgrade_preserved_autoincrement": bool(reflection.get("upgrade_preserved_autoincrement")),
        "batch_upgrade_preserved_fk_names": bool(reflection.get("upgrade_preserved_fk_names")),
        "batch_upgrade_preserved_rows": bool(reflection.get("upgrade_preserved_rows")),
        "batch_downgrade_preserved_autoincrement": bool(reflection.get("downgrade_preserved_autoincrement")),
        "batch_copy_from_upgrade_ok": bool(copy_from.get("upgrade_ok")),
        "batch_copy_from_upgrade_preserved_autoincrement": bool(copy_from.get("upgrade_preserved_autoincrement")),
        "batch_copy_from_downgrade_ok": bool(copy_from.get("downgrade_ok")),
        "batch_copy_from_downgrade_preserved_autoincrement": bool(copy_from.get("downgrade_preserved_autoincrement")),
        "batch_copy_from_error": str(copy_from.get("error") or ""),
        "batch_notes": notes,
    }


# --------------------------------------------------------------------------------------
# 实验 5：greenlet / 异步引擎
# --------------------------------------------------------------------------------------


def probe_greenlet(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "async_probe.db"

    greenlet_available = False
    greenlet_import_error = ""
    try:
        import greenlet  # noqa: F401

        greenlet_available = True
    except Exception as exc:  # noqa: BLE001
        greenlet_import_error = f"{type(exc).__name__}: {exc}"

    async def _connect_and_select() -> int:
        from sqlalchemy import text as sa_text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        try:
            async with engine.connect() as conn:
                result = await conn.execute(sa_text("select 1"))
                return int(result.scalar_one())
        finally:
            await engine.dispose()

    async_engine_usable = False
    async_engine_error = ""
    async_engine_error_type = ""
    value: Any = None
    try:
        value = asyncio.run(_connect_and_select())
        async_engine_usable = value == 1
    except Exception as exc:  # noqa: BLE001
        async_engine_error = str(exc)
        async_engine_error_type = f"{type(exc).__module__}.{type(exc).__name__}"

    return {
        "greenlet_available": greenlet_available,
        "greenlet_import_error": greenlet_import_error,
        "async_engine_usable": async_engine_usable,
        "async_engine_error": async_engine_error,
        "async_engine_error_type": async_engine_error_type,
        "async_engine_select1_value": value,
        "greenlet_notes": (
            "本卡不安装 greenlet（依赖变更归 M2-02）；AIOSQLITE_URL 的异步引擎在缺 greenlet 时"
            "的原始报错见 async_engine_error。"
        ),
    }


# --------------------------------------------------------------------------------------
# 结果汇总
# --------------------------------------------------------------------------------------

EXP1_DEFAULTS = {
    "naming_convention": NAMING_CONVENTION,
    "naming_convention_applied": False,
    "naming_convention_missing_names": [],
    "naming_convention_late_control": {},
    "autoincrement_emitted": False,
    "autoincrement_writing": "",
    "autoincrement_without_flag_control": {},
    "partial_index_emitted": False,
    "partial_index_ddl": "",
    "foreign_key_cascade_emitted": False,
    "json_server_default_emitted": False,
    "sqlite_sequence_present": False,
    "schema_ddl": {},
}

EXP2_DEFAULTS = {
    "auto_vacuum_placements": {name: 0 for name, _, _ in AUTO_VACUUM_SCENARIOS},
    "auto_vacuum_placement": "",
    "auto_vacuum_effective": False,
    "auto_vacuum_version_row_ok": {name: False for name, _, _ in AUTO_VACUUM_SCENARIOS},
    "auto_vacuum_placement_usable": False,
    "auto_vacuum_version_rows": {name: [] for name, _, _ in AUTO_VACUUM_SCENARIOS},
    "auto_vacuum_notes": {},
    "auto_vacuum_fallbacks_tried": [],
    "auto_vacuum_ineffective_names": [],
}

EXP3_DEFAULTS = {
    "autogenerate_ran": False,
    "autogenerate_detected_partial_index": False,
    "autogenerate_diffs": [],
    "autogenerate_ops": [],
    "autogenerate_matched_db_ops": [],
    "autogenerate_generated_migration_applies": False,
    "autogenerate_apply_error": "",
    "autogenerate_mako_fix_applies": False,
    "autogenerate_mako_fix_error": "",
    "autogenerate_applied_autoincrement": False,
    "autogenerate_applied_partial_where": False,
    "autogenerate_notes": {},
}

EXP4_DEFAULTS = {
    "batch_downgrade_ok": False,
    "batch_upgrade_ok": False,
    "batch_change_observed": False,
    "batch_upgrade_preserved_autoincrement": False,
    "batch_upgrade_preserved_fk_names": False,
    "batch_upgrade_preserved_rows": False,
    "batch_downgrade_preserved_autoincrement": False,
    "batch_copy_from_upgrade_ok": False,
    "batch_copy_from_upgrade_preserved_autoincrement": False,
    "batch_copy_from_downgrade_ok": False,
    "batch_copy_from_downgrade_preserved_autoincrement": False,
    "batch_copy_from_error": "",
    "batch_notes": {},
}

EXP5_DEFAULTS = {
    "greenlet_available": False,
    "greenlet_import_error": "",
    "async_engine_usable": False,
    "async_engine_error": "",
    "async_engine_error_type": "",
    "async_engine_select1_value": None,
    "greenlet_notes": "",
}


def _versions() -> dict[str, str]:
    import alembic
    import sqlalchemy
    import sqlmodel

    return {
        "alembic_version": str(alembic.__version__),
        "sqlmodel_version": str(sqlmodel.__version__),
        "sqlalchemy_version": str(sqlalchemy.__version__),
        "sqlite_version": str(sqlite3.sqlite_version),
    }


def run_all_probes(workdir: Path) -> dict:
    result: dict[str, Any] = {
        "probe": "M2-01",
        "probe_title": "SQLModel + Alembic 在本机 SQLite 上的 DDL 能力边界",
        "generated_at": _now_iso(),
        "repo_root": str(REPO_ROOT),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "temp_workdir": str(workdir),
        "probe_command": ".venv/bin/python scripts/probe_sqlmodel_alembic.py",
        "partial_index_predicate": PARTIAL_PREDICATE,
    }
    result.update(_versions())

    errors: list[str] = []
    experiments = (
        ("exp1_schema", probe_models_ddl, EXP1_DEFAULTS, workdir / "exp1_schema"),
        ("exp2_auto_vacuum", probe_auto_vacuum, EXP2_DEFAULTS, workdir / "exp2_auto_vacuum"),
        ("exp3_autogenerate", probe_autogenerate, EXP3_DEFAULTS, workdir / "exp3_autogenerate"),
        ("exp4_batch", probe_batch_downgrade, EXP4_DEFAULTS, workdir / "exp4_batch"),
        ("exp5_greenlet", probe_greenlet, EXP5_DEFAULTS, workdir / "exp5_greenlet"),
    )
    for name, func, defaults, target in experiments:
        try:
            result.update(func(target))
        except Exception as exc:  # noqa: BLE001 - 单个实验失败不掩盖其它实验的观测
            errors.append(f"{name}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            result.update(defaults)

    versions_ok = all(
        isinstance(result.get(key), str) and result[key].strip()
        for key in ("alembic_version", "sqlmodel_version", "sqlalchemy_version", "sqlite_version")
    )
    av_notes = result.get("auto_vacuum_notes", {}) or {}
    no_commit_note = av_notes.get(NO_COMMIT_PLACEMENT, {}) or {}
    required_checks = {
        "naming_convention_applied": bool(result.get("naming_convention_applied")),
        "autoincrement_emitted": bool(result.get("autoincrement_emitted")),
        "partial_index_emitted": bool(result.get("partial_index_emitted")),
        "foreign_key_cascade_emitted": bool(result.get("foreign_key_cascade_emitted")),
        "json_server_default_emitted": bool(result.get("json_server_default_emitted")),
        "auto_vacuum_effective": bool(result.get("auto_vacuum_effective")),
        # M2-14 补的两条门禁：推荐放置必须真的写下 alembic_version 行；不 commit 的
        # 对照必须被本探针识别出来（否则文档里那条坑就等于没有门禁挡着）。
        "auto_vacuum_version_row_ok": bool(result.get("auto_vacuum_placement_usable")),
        "auto_vacuum_no_commit_control_reproduced": no_commit_note.get("alembic_version_ok") is False
        and no_commit_note.get("second_upgrade_ok") is False,
        "autogenerate_ran": bool(result.get("autogenerate_ran")),
        "autogenerate_apply_recorded": bool(result.get("autogenerate_generated_migration_applies"))
        or bool(str(result.get("autogenerate_apply_error") or "").strip()),
        "batch_upgrade_ok": bool(result.get("batch_upgrade_ok")),
        "batch_change_observed": bool(result.get("batch_change_observed")),
        "batch_downgrade_ok": bool(result.get("batch_downgrade_ok")),
        "async_probe_recorded": bool(result.get("greenlet_available"))
        or bool(str(result.get("async_engine_error") or "").strip()),
        "versions_recorded": versions_ok,
    }
    result["required_checks"] = required_checks
    result["errors"] = errors
    result["probe_ok"] = all(required_checks.values())
    return result


# --------------------------------------------------------------------------------------
# 人读结论
# --------------------------------------------------------------------------------------


def build_findings_md(result: dict) -> str:
    av = result.get("auto_vacuum_placements", {})
    av_notes = result.get("auto_vacuum_notes", {}) or {}
    av_version_ok = result.get("auto_vacuum_version_row_ok", {}) or {}

    def _version_rows_text(name: str) -> str:
        """把某场景升级后 alembic_version 表的实测内容渲染成表格单元（M2-14 补）。"""
        if not av_notes.get(name):
            return "（未采集到）"
        rows = av_notes[name].get("alembic_version_rows") or []
        if not rows:
            return "**空（0 行）**"
        return ", ".join(f"`{row}`" for row in rows)

    def _oneline(value: Any, limit: int = 160) -> str:
        """把可能带换行的报错压成单行，避免撑破 Markdown 的列表项。"""
        collapsed = " ".join(str(value or "").split())
        return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"

    def _verdict(name: str, value: int) -> str:
        if value == 2 and av_version_ok.get(name):
            return "✅ 生效"
        if value == 2:
            return "⚠️ **不可用**：pragma 拿到 2，但 `alembic_version` 行被回滚"
        return "❌ 未生效（0 = NONE，静默失效）"

    av_rows = "\n".join(
        f"| `{name}` | {value} | {_version_rows_text(name)} | {_verdict(name, value)} |"
        for name, value in av.items()
    )
    chosen = result.get("auto_vacuum_placement", "")
    chosen_note = av_notes.get(chosen, {}) or {}
    no_commit_note = av_notes.get(NO_COMMIT_PLACEMENT, {}) or {}
    late = result.get("naming_convention_late_control", {}) or {}
    no_flag = result.get("autoincrement_without_flag_control", {}) or {}
    diffs = result.get("autogenerate_diffs", []) or []
    diff_lines = "\n".join(f"{index}. {line}" for index, line in enumerate(diffs, start=1)) or "（未采集到）"
    batch = result.get("batch_notes", {}).get("reflection_batch", {}) or {}
    batch_copy = result.get("batch_notes", {}).get("copy_from_batch", {}) or {}

    def _yn(value: Any) -> str:
        return "是" if value else "否"

    if result.get("greenlet_available"):
        greenlet_section = (
            f"- `greenlet_available = True`（{result.get('greenlet_version', '已安装')}），"
            f"异步引擎可用性 = {_yn(result.get('async_engine_usable'))}，"
            f"`select 1` 返回值 = {result.get('async_engine_select1_value')}。\n"
        )
        impact = "- 本机当前**已能**创建异步引擎，M2-02 仍需把 greenlet 写进显式依赖（aiosqlite/SQLAlchemy async 的硬要求），不要依赖间接传递。\n"
    else:
        greenlet_section = (
            "- `greenlet_available = False`：`.venv` 里没有 greenlet。\n"
            f"- 原始报错（`async_engine_error` 原样记录）：`{result.get('async_engine_error')}`\n"
            f"- 异常类型：`{result.get('async_engine_error_type')}`；`async_engine_usable = "
            f"{_yn(result.get('async_engine_usable'))}`。\n"
        )
        impact = (
            "- **影响面**：只要 greenlet 缺失，`sqlite+aiosqlite:///...` 的 `create_async_engine` / "
            "`connect` / `execute` 全部不可用 —— 也就是 docs/04 §2 的 `maa_api/db/session.py`"
            "（异步引擎 + `async_sessionmaker`）以及将来所有异步仓储/路由都跑不起来；"
            "Alembic 走的同步 `sqlite:///` 引擎不受影响。\n"
            "- **处置（归 M2-02，本卡不装）**：把 `greenlet` 显式加入 `pyproject.toml` 依赖"
            "（SQLAlchemy 只把它放在 `sqlalchemy[asyncio]` extra 里，本项目是按 `sqlalchemy` 裸装的，"
            "所以它没有被带进来），然后 `poetry lock` / `pip install greenlet`。\n"
            "- 测试侧不需要新增 pytest 插件：用同步测试函数 + `asyncio.run(...)` 即可（本探针就是这么跑异步引擎的）。\n"
        )

    md = f"""# M2-01 实测记录：SQLModel + Alembic 在本机 SQLite 上的 DDL 能力边界

- 生成时间：{result.get('generated_at')}
- 环境：Python {result.get('python_version')} / SQLAlchemy {result.get('sqlalchemy_version')} /
  SQLModel {result.get('sqlmodel_version')} / Alembic {result.get('alembic_version')} /
  SQLite {result.get('sqlite_version')}（{result.get('platform')}）
- 复现命令：`{result.get('probe_command')}`（`--help` 不做实验；所有实验都在 `tempfile.mkdtemp()`
  内完成，仓库里不会留下 `alembic.ini` / `migrations/` / `*.db`）

> 本文件全部结论来自实测；与 docs/04 的写法冲突处以本文件为准，docs 的推断在下面逐条标注。
>
> **M2-14 修正（2026-09-16）**：§2 与 §6.5 原先记录的 `auto_vacuum`「唯一生效放置」只写了
> `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")`、漏了紧跟的 `conn.commit()`。
> 该缺陷由 **M2-04** 实测发现（照抄后 `alembic_version` 行为空、第二次 `upgrade head` 报
> `table already exists`，见 `.refactor/DEFECTS.md` 的 M2-01 条目）；M2-14 已按实测补全
> 说明，并给探针补上「`alembic_version` 行 == 预期版本」断言与「不 commit」对照场景，
> 使这类错误能被探针本身挡住。

## 结论速览

| 问题 | 实测结论 |
|---|---|
| 命名约定 | 表定义**之前**设置才生效（`naming_convention_applied = {_yn(result.get('naming_convention_applied'))}`）；表定义之后才设置时，约定名{'跟随' if late.get('applied') else '**不跟随**'} |
| `AUTOINCREMENT` | {'DDL 里确实落下了关键字' if result.get('autoincrement_emitted') else '**没有**落下关键字'}；写法：`id: int \\| None = Field(default=None, primary_key=True)` + `__table_args__ = {{'sqlite_autoincrement': True}}` |
| 部分唯一索引 | `sqlite_where` 的 `WHERE` 子句{'进入了 DDL' if result.get('partial_index_emitted') else '**没有**进入 DDL'} |
| `auto_vacuum` | 生效的放置方式：`{chosen or '（四种都没拿到 2）'}`，且 pragma 之后**必须 `conn.commit()`**（漏了会让版本行被回滚，见第 2 节） |
| autogenerate | 部分索引谓词{'被保留' if result.get('autogenerate_detected_partial_index') else '**被丢失**'}；产物直接 `upgrade` {'可跑通' if result.get('autogenerate_generated_migration_applies') else '**跑不通**（`import sqlmodel` 缺失，见第 3 节）'} |
| batch downgrade | `batch_downgrade_ok = {_yn(result.get('batch_downgrade_ok'))}`；注意 batch 重建会丢 `AUTOINCREMENT`（见第 4 节） |
| greenlet | {'已安装' if result.get('greenlet_available') else '**缺失**'}（`async_engine_usable = {_yn(result.get('async_engine_usable'))}`） |

## 1. AUTOINCREMENT 的正确写法

实测 DDL（`probe_child`）：

```sql
{str(result.get('schema_ddl', {}).get('probe_child', '（未采集到）')).strip()}
```

- 让 DDL 落下 `AUTOINCREMENT` 的写法是：`id: int | None = Field(default=None, primary_key=True)`
  **加上** `__table_args__ = {{'sqlite_autoincrement': True}}`。只写 `primary_key=True`
  时 DDL 是 `id INTEGER NOT NULL PRIMARY KEY`，**没有** `AUTOINCREMENT`
  （对照实验 `autoincrement_without_flag_control.emitted = {_yn(no_flag.get('emitted'))}`，
  对照 DDL：`{str(no_flag.get('ddl', '')).strip()}`）。
- `create_all` 之后库里会多一张内部表 `sqlite_sequence`（实测：`sqlite_sequence_present = {_yn(result.get('sqlite_sequence_present'))}`），
  这正是 SQLite 用来保证 id 永不回退的序列表，可以作为「AUTOINCREMENT 真的生效」的旁证。
- 结论：docs/04 §3.1 的硬约束成立，M2-03 给 `log_entry` / `agent_message` / `agent_audit`
  三张表都必须写 `__table_args__ = {{'sqlite_autoincrement': True}}`；只写自增主键是不够的。

## 2. `auto_vacuum`：哪种放置生效，哪种静默失效

四种放置方式各自在**全新临时库**里真跑 `alembic upgrade head`，升级完成后用**新连接**读
`PRAGMA auto_vacuum`（0 = NONE，1 = FULL，2 = INCREMENTAL），并核对 `alembic_version`
表里是否有且仅有 `{EXPECTED_REVISION}` 这一行 —— 只查「表存在」挡不住版本行被回滚
（M2-14 补，起因见下）：

| 放置方式 | `PRAGMA auto_vacuum` 实测值 | `alembic_version` 实测行 | 结论 |
|---|---|---|---|
{av_rows}

- 生效写法：`{chosen or '（无）'}`。
  {f"对应的代码形态是在 `env.py` 的 `run_migrations_online()` 里、`context.begin_transaction()` **之前**，于 connection 上执行 `conn.exec_driver_sql(\"PRAGMA auto_vacuum=INCREMENTAL\")`；此时库还是空的（`alembic_version` 尚未创建），pragma 被写进库头立即生效。" if chosen == RECOMMENDED_PLACEMENT else ""}
- **`PRAGMA` 之后必须紧跟一次 `conn.commit()`（M2-04 实测发现，M2-14 补入本文件）**。
  正确形态一共两行，缺第二行就会出上面那条坑：

  ```python
  conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")
  conn.commit()  # ⚠️ 不能省：见下一条
  ```

  本场景（`{chosen}`）实测 `PRAGMA auto_vacuum = {av.get(chosen, '（无）')}`、
  `alembic_version` 行 = {_version_rows_text(chosen) if chosen else '（无）'}；
  紧接着的第二次 `upgrade head` 实测为{'无操作、无报错（幂等）' if chosen_note.get('second_upgrade_ok') else '**报错**：`' + _oneline(chosen_note.get('second_upgrade_error')) + '`'}。
- **不 `commit()` 的具体现象（对照场景 `{NO_COMMIT_PLACEMENT}`，M2-14 实测复现 M2-04 的发现）**：
  只执行 pragma、不提交时，实测 `PRAGMA auto_vacuum = {av.get(NO_COMMIT_PLACEMENT, '（无）')}`、
  `alembic_version` 行 = {_version_rows_text(NO_COMMIT_PLACEMENT)}。原因是
  `exec_driver_sql()` 会 autobegin 一个 SQLAlchemy 事务；Alembic 的 `MigrationContext`
  只要发现连接上已有事务就把它当「外部事务」，于是 `begin_transaction()` 退化成 no-op、
  迁移结束也不提交：DDL 因为 pysqlite 不把 DDL 包进事务而留在库里（**表建好了**），
  但 `alembic_version` 的 INSERT 随连接关闭一起回滚（**版本行没了**）。
  第二次 `upgrade head` 实测{'无报错' if no_commit_note.get('second_upgrade_ok') else '：`' + _oneline(no_commit_note.get('second_upgrade_error')) + '`'}
  —— 版本行为空使 Alembic 从头重放 0001，撞上 `table ... already exists`。
  2026-09-16 的 M2-14 探针跑出这条对照，正是 M2-04 写 `env.py` 初版时踩到的现象。
- **docs/04 §2 原本的写法（0001 `upgrade()` 首行）实测无效**：值仍是 0，而且没有任何报错 ——
  典型的静默失效。原因是 Alembic 在跑第一个迁移之前已经建好了 `alembic_version` 表，
  库不再是空库，SQLite 只在「库为空」时立即接受 `auto_vacuum` 变更，否则要整库 `VACUUM` 才生效。
- `op.get_context().autocommit_block()` 同样无效（值 0）：它只解决「事务里不能改」的问题，
  解决不了「库非空」，`alembic_version` 此时已经存在。
- 四种都没拿到 2 时才需要兜底实验；本次兜底{f"已触发：{', '.join(result.get('auto_vacuum_fallbacks_tried') or [])}" if result.get('auto_vacuum_fallbacks_tried') else "未触发"}，预留的兜底候选是「pragma + `VACUUM`」与「迁移前用裸 `sqlite3` 连接在空库上设置」。
- **给 M2-04 的落点**：把 `PRAGMA auto_vacuum=INCREMENTAL` 从初始迁移挪到
  `maa_api/db/migrations/env.py` 的 `run_migrations_online()` 开头（`context.configure` 之前），
  **紧跟一次 `conn.commit()`**，并留一条注释说明「写在迁移里是静默无效的、不 commit 会丢版本行」；
  `connect` 事件里同样不能写（docs/04 §2 的判断正确，只是落点选错了）。
  M2-04 的 `env.py` 现状即按此实现（pragma → `conn.commit()` → `context.configure`），
  M2-14 的探针实测其成品（`{chosen}`）版本行为 {_version_rows_text(chosen) if chosen else '（无）'}。

## 3. autogenerate 漏了什么、需要人工补什么

用 docs/04 §8.2 的配置（`render_as_batch=True, compare_type=True, compare_server_default=True`）
对同一份模型跑了三次 `alembic revision --autogenerate`：空库（看漏报）、
空库 + 修好的 `script.py.mako`（看修复候选）、`create_all` 已建好的库（看误报）。
每次都把产物真跑一次 `upgrade head`。

`autogenerate_ran = {_yn(result.get('autogenerate_ran'))}`；
`autogenerate_detected_partial_index = {_yn(result.get('autogenerate_detected_partial_index'))}`
（定义：生成物里既有 `sqlite_where` 又有谓词 `{result.get('partial_index_predicate')}`）。

逐条 diff：

{diff_lines}

- 空库那次生成的操作序列：`{', '.join(result.get('autogenerate_ops') or []) or '（无）'}`
- 已建库那次的噪声操作：`{', '.join(result.get('autogenerate_matched_db_ops') or []) or '（无，说明没有误报）'}`
- **最重要的一条：`autogenerate_ran` 不等于「产物能用」。** 实测
  `autogenerate_generated_migration_applies = {_yn(result.get('autogenerate_generated_migration_applies'))}`，
  原始报错 `{result.get('autogenerate_apply_error') or '（无）'}`：SQLModel 的 `Field(max_length=...)`
  在元数据里是 `sqlmodel.sql.sqltypes.AutoString`，autogenerate 按 `模块.类` 渲染，
  但**不会自动补 `import sqlmodel`**，于是生成文件一执行就 `NameError`。
  修复候选已验证：把 `import sqlmodel` 写进 `script.py.mako` 后重新生成，
  `autogenerate_mako_fix_applies = {_yn(result.get('autogenerate_mako_fix_applies'))}`
  （若失败，报错：`{result.get('autogenerate_mako_fix_error') or '（无）'}`），
  且落库 DDL 里 `AUTOINCREMENT = {_yn(result.get('autogenerate_applied_autoincrement'))}`、
  部分索引 `WHERE` 谓词 = {_yn(result.get('autogenerate_applied_partial_where'))}。
- docs/04 §8.2 说「autogenerate 检测不到 `sqlite_where` 的部分索引」：本次在
  Alembic {result.get('alembic_version')} + SQLModel {result.get('sqlmodel_version')} 上**未能复现**
  （谓词被完整生成，连 `sqlite_autoincrement=True`、`server_default`、`op.f()` 约定名都保住了）。
  「索引重命名检测不到」这一条本卡没有单独设对照，仍按 docs 的人工 review 纪律执行。
- 结论：**生成物仍然必须人工过一遍**，至少检查
  ①`import sqlmodel` / `AutoString` 渲染（本次唯一实测会炸的项）、
  ②部分唯一索引的 `sqlite_where` 谓词、③`sqlite_autoincrement=True`、
  ④JSON 列的 `server_default`、⑤外键 `ondelete` 与约定名。
  M2-04 应把 `import sqlmodel` 直接写进 `script.py.mako`，并把上面五项列进 review 清单。

## 4. batch + downgrade

第二个迁移用 `op.batch_alter_table("probe_child", recreate="always")` 同时加一列
（`note VARCHAR(32) NULL`）并把 `label` 从 `VARCHAR(16)` 改成 `VARCHAR(24)`，
流程是 `upgrade 0001` → 快照 → `upgrade head` → `downgrade -1` → 快照比对。

- `batch_upgrade_ok = {_yn(result.get('batch_upgrade_ok'))}`，
  `batch_change_observed = {_yn(result.get('batch_change_observed'))}`：
  upgrade 后列集合 = `{batch.get('columns_after_upgrade')}`，`label` 类型 = `{batch.get('label_type_after_upgrade')}`。
- `batch_downgrade_ok = {_yn(result.get('batch_downgrade_ok'))}`（列/外键/显式索引结构指纹与上一版一致）；
  downgrade 后列集合 = `{batch.get('downgrade_columns')}`。
- 数据不丢：`batch_upgrade_preserved_rows = {_yn(result.get('batch_upgrade_preserved_rows'))}`（重建前后都是 1 行）。
- 命名外键在重建后仍在：`batch_upgrade_preserved_fk_names = {_yn(result.get('batch_upgrade_preserved_fk_names'))}`
  —— 这正是 docs/04 §8.1 要求「约束先命名」的原因，匿名约束在 batch 重建时会丢名字。
- **本卡最重要的意外发现：纯反射的 batch 重建会静默丢掉 `AUTOINCREMENT`。**
  实测 `batch_upgrade_preserved_autoincrement = {_yn(result.get('batch_upgrade_preserved_autoincrement'))}`，
  `batch_downgrade_preserved_autoincrement = {_yn(result.get('batch_downgrade_preserved_autoincrement'))}`；
  upgrade 前 DDL：`{batch.get('before_table_sql')}`；
  batch 重建后 DDL：`{batch.get('upgrade_table_sql')}`；
  downgrade 后 DDL：`{batch.get('downgrade_table_sql')}`。
  原因：`batch_alter_table` 重建时靠 SQLAlchemy 反射拿旧表结构，而 SQLite 反射**不还原**
  `sqlite_autoincrement` 表选项，于是 `id INTEGER PRIMARY KEY AUTOINCREMENT` 变成
  `id INTEGER PRIMARY KEY`，id 回退保护就此消失。
- 兜底做法实测（给 `batch_alter_table` 传 `copy_from`，不让它反射）：
  upgrade 侧传模型自己的 `SQLModel.metadata.tables["probe_child"]`，
  `batch_copy_from_upgrade_ok = {_yn(result.get('batch_copy_from_upgrade_ok'))}`、
  `batch_copy_from_upgrade_preserved_autoincrement = {_yn(result.get('batch_copy_from_upgrade_preserved_autoincrement'))}`；
  downgrade 侧必须传「0002 之后的库结构」（模型表 `to_metadata()` 再补上 `note` 列），
  `batch_copy_from_downgrade_ok = {_yn(result.get('batch_copy_from_downgrade_ok'))}`、
  `batch_copy_from_downgrade_preserved_autoincrement = {_yn(result.get('batch_copy_from_downgrade_preserved_autoincrement'))}`{('（copy_from 变体报错：`' + str(result.get('batch_copy_from_error')) + '`）') if result.get('batch_copy_from_error') else '。'}
  结论：**`copy_from` 的表结构必须与「该方向的迁移前结构」一致，只补一个方向不够**；
  涉及 `log_entry` / `agent_message` / `agent_audit` 这类带 `AUTOINCREMENT` 的表时，
  upgrade 与 downgrade 都要显式给 `copy_from`，并在迁移后用 `sqlite_master` 复查 DDL。
- 给 M2-04 的纪律：凡是要 rebuild 表的迁移（改列类型 / 加删约束 / 删列），
  ①反射式重建会静默丢 `AUTOINCREMENT`，所以 `copy_from` 必须补；
  ②`copy_from` 的表结构要与该方向的迁移前结构一致（升级用模型 Table，降级用迁移后的结构）；
  ③迁移落库后检查 `sqlite_master` 里那张表的 DDL 是否还有 `AUTOINCREMENT`。
  纯加列的迁移（`ALTER TABLE ADD COLUMN`）不重建表，不受影响。

## 5. greenlet 缺失的原始报错与影响面

{greenlet_section}{impact}
## 6. 给 M2-03 / M2-04 的具体写法建议

1. **models.py 顶部顺序**：先 `SQLModel.metadata.naming_convention = {{...}}`，再定义任何
   `table=True` 的模型。实测表定义之后才设置时，无名 `Index` / `UniqueConstraint` / 外键
   {'仍会跟随' if late.get('applied') else '**不会**跟随'}约定名（对照缺失项：`{late.get('missing')}`）。
2. **三张高频追加表**写 `__table_args__ = {{'sqlite_autoincrement': True}}`，
   否则 DDL 里没有 `AUTOINCREMENT`，日志表按天清理后会复用已删除的 id，WebSocket 断线续传游标失效。
3. **部分唯一索引**（`uq_update_record_running_target`、`uq_probe_parent_running_status` 这类）
   建议照 docs/04 §6 手写 `Index(name, col, unique=True, sqlite_where=sa.text(...))`：
   本次实测 autogenerate {'会保留' if result.get('autogenerate_detected_partial_index') else '**会丢掉**'}谓词，
   但「生成物能不能直接跑」还取决于下一条，手写最稳。
4. **`script.py.mako` 必须补 `import sqlmodel`**：否则所有 `Field(max_length=...)` 字符串列会被
   渲染成 `sqlmodel.sql.sqltypes.AutoString(...)`，生成文件一执行就
   `NameError: name 'sqlmodel' is not defined`（本次实测产物
   `upgrade head` 失败：`{result.get('autogenerate_apply_error') or '（未复现）'}`）。
   补上之后 `autogenerate_mako_fix_applies = {_yn(result.get('autogenerate_mako_fix_applies'))}`。
   备选方案是在 env.py 里用 `render_item` 把 `AutoString` 渲染成 `sa.String(length=...)`。
5. **env.py**：`render_as_batch=True, compare_type=True, compare_server_default=True` 照抄，
   并在 `run_migrations_online()` 里 `context.begin_transaction()` 之前加
   `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")`，**紧跟一次 `conn.commit()`**
   （M2-14 修正：M2-01 原记录漏了这一行，M2-04 照抄后 `alembic_version` 行为空、第二次
   `upgrade head` 报 `table already exists`；实测见第 2 节）；
   不要写进初始迁移的 `upgrade()`（实测静默无效）。
6. **迁移后的断言不能只看表**：每次 `upgrade head` 之后都要核对 `alembic_version` 有且仅有
   预期 revision 这一行（M2-14 补，探针的 `auto_vacuum_version_row_ok` /
   `auto_vacuum_no_commit_control_reproduced` 两条门禁即为此设）。表建好而版本行为空时，
   下一次 `upgrade head` 会从头重放并撞 `table already exists`。
7. **迁移 review 清单**：`import sqlmodel`、`sqlite_where` 谓词、`sqlite_autoincrement=True`、
   JSON `server_default`、`ondelete` 级联、约定名；涉及 rebuild 的迁移额外检查 `copy_from`
   与迁移后的 `AUTOINCREMENT`/`foreign_keys` 是否还在。
8. **异步层（M2-02）**：greenlet 必须显式加依赖；在装上之前的 async 代码无法运行。
"""
    return md


# --------------------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_sqlmodel_alembic.py",
        description=(
            "M2-01 方案前置实测：SQLModel + Alembic 在本机 SQLite 上的 DDL 能力边界。"
            "所有实验都在临时目录内完成，不会改动仓库。"
        ),
        epilog="产物默认写入 tests/fixtures/db_probe_result.json 与 tests/fixtures/db_probe_findings.md。",
    )
    parser.add_argument(
        "--out-json",
        default=str(DEFAULT_OUT_JSON),
        help="实测结果 JSON 路径（默认 tests/fixtures/db_probe_result.json）",
    )
    parser.add_argument(
        "--out-md",
        default=str(DEFAULT_OUT_MD),
        help="人读结论 Markdown 路径（默认 tests/fixtures/db_probe_findings.md）",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留临时实验目录（默认结束后删除）",
    )
    # --help 在下面任何实验之前由 argparse 处理并 exit 0。
    args = parser.parse_args(argv)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    if not out_json.is_absolute():
        out_json = REPO_ROOT / out_json
    if not out_md.is_absolute():
        out_md = REPO_ROOT / out_md

    workdir = Path(tempfile.mkdtemp(prefix="maa_api_db_probe_"))
    print(f"[probe] 临时实验目录：{workdir}", flush=True)
    try:
        result = run_all_probes(workdir)
    finally:
        if args.keep_temp:
            print(f"[probe] --keep-temp：保留 {workdir}", flush=True)
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(build_findings_md(result), encoding="utf-8")
    print(f"[probe] wrote {out_json}", flush=True)
    print(f"[probe] wrote {out_md}", flush=True)

    if result.get("errors"):
        for item in result["errors"]:
            print(f"[probe] ERROR {item}", file=sys.stderr, flush=True)

    failed = [name for name, ok in result.get("required_checks", {}).items() if not ok]
    if failed or not result.get("probe_ok"):
        print(f"[probe] FAILED required checks: {failed}", file=sys.stderr, flush=True)
        return 1
    print("PROBE OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
