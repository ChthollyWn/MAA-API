"""``tests/agent/tickets`` 的共享 fixture（M69 规格 §6）。

- ``v3_factory``：临时 SQLite 库的异步会话工厂（``tests/fakes/v3_db.py`` 同口径），
  用例结束销毁同步引擎；异步用例写成「同步测试函数 + ``asyncio.run(scenario())``」。
- ``spec_registry``：三个代理定义的假注册表，形状与 ``AgentSpecRegistry.get`` 对齐
  （只带 ``kind`` 与 ``risk_ceiling``）；其他名字抛 ``AGENT_SPEC_NOT_FOUND``。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory

_SPECS: dict[str, tuple[str, str]] = {
    "daily_ops": ("worker", "consume"),
    "general": ("worker", "destructive"),
    "researcher": ("subagent", "none"),
}


@pytest.fixture
def v3_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """临时库的会话工厂；用例结束 dispose 同步引擎（不依赖事件循环）。"""
    engine: AsyncEngine
    engine, factory = asyncio.run(make_session_factory(tmp_path / "v3.db"))
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture
def spec_registry() -> SimpleNamespace:
    """三个代理定义的最小注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(name: str) -> SimpleNamespace:
        try:
            kind, risk_ceiling = _SPECS[name]
        except KeyError:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND,
                f"没有名为 {name} 的代理定义",
            ) from None
        return SimpleNamespace(kind=kind, risk_ceiling=risk_ceiling)

    return SimpleNamespace(get=get)
