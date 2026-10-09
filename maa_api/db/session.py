"""异步引擎与会话管理（docs/04 §2）。

两条可测性约定：

- 六个 PRAGMA 的挂接统一走 :func:`apply_pragmas`（挂在 ``connect`` 事件上），
  模块级 :data:`engine` 与测试都用 :func:`make_engine` 构造，测试可以拿
  ``tmp_path`` 的 URL 断言 PRAGMA 的真实取值。
- :data:`DB_PATH` 是**相对**路径，不在这里建目录、不在这里建表；目录创建与建表
  归 ``db/migrate.py`` 的 ``ensure_schema``（docs/04 §8.3）。

会话纪律：后台任务（PipelineRunner、LogHub 刷盘、清理任务）各自开新会话，不
跨任务共享；``expire_on_commit=False`` 让提交后仍能读取 ORM 对象字段做 WebSocket
广播，而不触发隐式 refresh 查询。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

DB_PATH = Path("resource") / "maa_api.db"
ASYNC_URL = f"sqlite+aiosqlite:///{DB_PATH}"
SYNC_URL = f"sqlite:///{DB_PATH}"          # Alembic 与维护脚本使用

# SQLite 只允许一个写者：DBAPI 层等 15 秒，引擎内部忙等同样 15 秒（docs/04 §2）。
CONNECT_ARGS = {
    "timeout": 15,              # 获取写锁的等待上限，秒
    "check_same_thread": False,
}

#: 写锁竞争的有限重试（docs/13 §8.32）。``busy_timeout=15000`` 下每次尝试最长
#: 等 15 秒；写锁通常在毫秒级释放，所以退避取毫秒级并加抖动，不在这里做大预算。
BUSY_RETRY_ATTEMPTS = 3
BUSY_RETRY_BASE_DELAY_SECONDS = 0.05


def apply_pragmas(dbapi_conn, _record=None) -> None:
    """在每条新连接上设置六个连接级 PRAGMA。

    必须挂在 ``connect`` 事件上而不是建库时执行一次：PRAGMA 是连接级的，
    连接池里每条新连接都要重新设置。``foreign_keys`` 尤其容易漏：SQLite
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


def is_sqlite_busy_error(exc: BaseException) -> bool:
    """判断是不是 SQLite 写锁竞争（``database is locked`` / ``table is locked``）。

    ``OperationalError`` 还覆盖语法错误、缺表等**不该重试**的失败，所以必须看
    DBAPI 原文而不是只看异常类型。
    """
    if not isinstance(exc, OperationalError):
        return False
    message = str(getattr(exc, "orig", exc)).lower()
    return "locked" in message or "busy" in message


def describe_database_error(exc: BaseException) -> str:
    """把 DB 失败渲染成可读原因：优先 DBAPI 原文，否则退回异常类名。

    ``sqlalchemy.exc.OperationalError`` 的 ``orig`` 是 ``sqlite3.OperationalError``，
    原文就是 ``database is locked``；只记类名的话，用户与日志都只剩一个
    ``OperationalError``（docs/13 §8.32）。
    """
    original = getattr(exc, "orig", None)
    detail = str(original).strip() if original is not None else ""
    if not detail:
        detail = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {detail}" if detail else name


async def run_with_busy_retry(
    session: AsyncSession,
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = BUSY_RETRY_ATTEMPTS,
    base_delay: float = BUSY_RETRY_BASE_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> T:
    """在独立 SAVEPOINT 里执行 ``operation``，对 SQLITE_BUSY 有限重试。

    失败只回退到 savepoint，外层事务（审计行等）不受影响，因此可以安全重试。
    每次重试前按 ``base_delay * 2**(n-1)`` 退避并加 0.5–1.5 倍抖动：多个写者
    同时重试时不会同步撞在一起。非 BUSY 的异常立即上抛：重试语法错误或约束
    冲突只会把同一个错误重复一遍。最终失败的处置留给调用方（如降级成
    ``SCREENSHOT_FAILED``），这里只负责多给几次机会。
    """
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(1, attempts + 1):
        try:
            async with session.begin_nested():
                return await operation()
        except OperationalError as exc:
            if attempt >= attempts or not is_sqlite_busy_error(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1)) * random.uniform(0.5, 1.5)
            logger.warning(
                "sqlite busy, retrying in %.3fs (attempt %d/%d): %s",
                delay,
                attempt,
                attempts,
                describe_database_error(exc),
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover - 循环必返回或上抛


engine = make_engine()

session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
