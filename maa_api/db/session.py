"""异步引擎与会话管理（docs/04 §2）。

两条可测性约定：

- 六个 PRAGMA 的挂接统一走 :func:`apply_pragmas`（挂在 ``connect`` 事件上），
  模块级 :data:`engine` 与测试都用 :func:`make_engine` 构造，测试可以拿
  ``tmp_path`` 的 URL 断言 PRAGMA 的真实取值。
- :data:`DB_PATH` 是**相对**路径，不在这里建目录、不在这里建表；目录创建与建表
  归 ``db/migrate.py`` 的 ``ensure_schema``（docs/04 §8.3）。

会话纪律：后台任务（PipelineRunner、LogHub 刷盘、清理任务）各自开新会话，绝不
跨任务共享；``expire_on_commit=False`` 让提交后仍能读取 ORM 对象字段做 WebSocket
广播，而不触发隐式 refresh 查询。
"""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DB_PATH = Path("resource") / "maa_api.db"
ASYNC_URL = f"sqlite+aiosqlite:///{DB_PATH}"
SYNC_URL = f"sqlite:///{DB_PATH}"          # Alembic 与维护脚本使用

# SQLite 只允许一个写者：DBAPI 层等 15 秒，引擎内部忙等同样 15 秒（docs/04 §2）。
CONNECT_ARGS = {
    "timeout": 15,              # 获取写锁的等待上限，秒
    "check_same_thread": False,
}


def apply_pragmas(dbapi_conn, _record=None) -> None:
    """在每条新连接上设置六个连接级 PRAGMA。

    必须挂在 ``connect`` 事件上而不是建库时执行一次：PRAGMA 是连接级的，
    连接池里每条新连接都要重新设置。``foreign_keys`` 尤其容易漏 —— SQLite
    默认关闭外键约束，不显式打开则所有 ``ON DELETE CASCADE`` 都是装饰。
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA cache_size=-16000")     # 16 MB 页缓存，负数表示 KiB
    cur.close()


def make_engine(url: str = ASYNC_URL, **engine_kwargs) -> AsyncEngine:
    """构造挂了 :func:`apply_pragmas` 的异步引擎。

    ``engine_kwargs`` 原样透传给 :func:`create_async_engine`（测试用
    ``poolclass=NullPool`` 避免跨事件循环复用连接）。
    """
    engine_kwargs.setdefault("echo", False)
    engine_kwargs.setdefault("connect_args", dict(CONNECT_ARGS))
    engine = create_async_engine(url, **engine_kwargs)
    event.listen(engine.sync_engine, "connect", apply_pragmas)
    return engine


engine = make_engine()

session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
