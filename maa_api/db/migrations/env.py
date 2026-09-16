"""Alembic 运行环境（docs/04 §8.2）。

要点与实测依据：

- **同步引擎**。迁移是一次性的串行操作，异步驱动只带来复杂度，所以统一用
  ``sqlite:///``（docs/04 §8.2）。应用侧的 ``sqlite+aiosqlite:///`` 不受影响。
- **``render_as_batch=True`` 是 SQLite 上最重要的一行**。SQLite 的 ``ALTER TABLE``
  只支持加列、改表名、改列名；改类型、加约束、删列都会被 batch 模式展开成
  「建临时表 → 拷数据 → 删旧表 → 改名」。忘了这行，第一次改列类型的迁移就会在
  生产库上失败。注意 batch 重建靠反射拿旧结构，会静默丢掉
  ``sqlite_autoincrement``，做重建的迁移必须显式给 ``copy_from``（M2-01 实测）。
- **``compare_type`` / ``compare_server_default``** 让 autogenerate 能发现列类型
  与默认值变化，但产物不可盲信：索引重命名、部分索引谓词它不保证检测得到，
  每个迁移文件都要人工过一遍（docs/04 §8.2，M2-01 review 清单）。
- **``auto_vacuum=INCREMENTAL`` 的落点是这里**，不是 0001 的 ``upgrade()`` 首行：
  SQLite 只在「库为空」时立即接受 ``auto_vacuum`` 变更，而迁移开始执行时
  ``alembic_version`` 已经建好，pragma 会**静默失效**（读回 0，无任何报错）。
  放在 ``context.configure()`` 之前、直接执行在 connection 上，是本机实测唯一
  能拿到 ``PRAGMA auto_vacuum == 2`` 的放置方式（M2-01，见
  ``tests/fixtures/db_probe_findings.md`` §2）。此外 pragma 之后必须紧跟一次
  ``conn.commit()``（M2-04 补），否则 autobegin 的事务会让 Alembic 把整次迁移
  判为「外部事务」而不提交，``alembic_version`` 行随之丢失 —— 详见
  :func:`run_migrations_online` 里的注释。
- **绝不调用 ``SQLModel.metadata.create_all()``**：两条建表路径混用会让
  ``alembic_version`` 与实际 schema 漂移（docs/04 §8.3）。这里只 import 模型，
  目的是把 13 张表注册进 ``SQLModel.metadata`` 供 autogenerate 比对。
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

# CLI（alembic 控制台脚本）的 sys.path[0] 是 .venv/bin，仓库根不一定在路径上；
# 这里按 env.py 的位置回推仓库根，保证 `import maa_api` 在任何入口下都成立。
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import maa_api.db.models  # noqa: E402,F401  必须 import：把全部表注册到 metadata
from maa_api.db.session import SYNC_URL  # noqa: E402

config = context.config
target_metadata = SQLModel.metadata

# 在线/离线共用；在线模式再追加 connection=conn。
COMMON_OPTS = {
    "target_metadata": target_metadata,
    "render_as_batch": True,          # SQLite 必须：ALTER 走复制重建
    "compare_type": True,             # 列类型变化进入 autogenerate 比对
    "compare_server_default": True,   # server_default 变化同上
}


def database_url() -> str:
    """优先取显式配置的 ``sqlalchemy.url``，缺省回退到应用侧同步 URL。

    ``maa_api/db/migrate.py`` 的 ``ensure_schema()`` 会在运行时
    ``set_main_option("sqlalchemy.url", SYNC_URL)``；测试也走同一条路径指向
    ``tmp_path`` 的临时库，绝不碰仓库里的 ``resource/maa_api.db``。
    """
    return config.get_main_option("sqlalchemy.url") or SYNC_URL


def run_migrations_offline() -> None:
    """``alembic upgrade head --sql``：不连库，只把 SQL 打到 stdout。"""
    context.configure(
        url=database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **COMMON_OPTS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            # ⚠️ 必须在建任何表之前、且写在 connection 上（M2-01 实测）。
            # 写进 0001 的 upgrade() 首行会静默失效：那时 alembic_version 已建好。
            conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")
            # ⚠️ 紧跟一次 commit，不能省。exec_driver_sql 会 autobegin 一个
            # SQLAlchemy 事务，而 Alembic 的 MigrationContext 只要发现连接上
            # 已有事务就把它当「外部事务」，于是 begin_transaction() 退化成
            # no-op、迁移结束也不提交：DDL 因 pysqlite 不把 DDL 包进事务而留下，
            # 但 alembic_version 的 INSERT 会随连接关闭一起回滚 —— 表现为
            # 「建完表但版本为空」，下次 upgrade 从头重放并撞上 table already exists。
            # 提交后连接回到无事务状态，Alembic 才会自己开事务并在结束时提交。
            conn.commit()
            context.configure(connection=conn, **COMMON_OPTS)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
