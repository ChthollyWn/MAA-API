"""M84 T4：入队路径的消耗预留（spec §4.5.6、§6）。

队列绑定 :class:`SpendGuard` 后，入队与账本写在同一事务里：受限来源超额时
流水线与任务都不落库；幂等键命中返回旧行、不重复记账；未绑定守卫时与 v2
完全一致。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.spend import SpendGuard
from maa_api.db.models import Pipeline, SpendLedger, Task
from maa_api.domain.enums import PipelineSource
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import PipelineCreate
from maa_api.services.queue_service import QueueService
from maa_api.settings import Settings, SpendSettings
from tests.fakes.v3_db import make_session_factory

#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)


class _Limits:
    """可变的设置提供者：只覆盖消耗上限，其余取 ``Settings`` 默认值。"""

    def __init__(self, *, medicine: int = -1, stone: int = -1, expedite: int = -1) -> None:
        self.medicine = medicine
        self.stone = stone
        self.expedite = expedite

    def __call__(self) -> Settings:
        return Settings(
            spend=SpendSettings(
                daily_medicine_limit=self.medicine,
                daily_stone_limit=self.stone,
                daily_expedite_limit=self.expedite,
            )
        )


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_channel_defaults`` 走 ``load_settings()``，别读仓库真实 config.yaml。"""
    import maa_api.settings as settings_module

    monkeypatch.setattr(
        settings_module, "DEFAULT_CONFIG_PATH", tmp_path / "missing-config.yaml"
    )
    for env_name in settings_module.ENV_OVERRIDES.values():
        monkeypatch.delenv(env_name, raising=False)


def _payload(*, medicine: int = 2) -> PipelineCreate:
    return PipelineCreate(
        tasks=[{"name": "Fight", "stage": "1-7", "medicine": medicine}]
    )


async def _count(
    factory: async_sessionmaker[AsyncSession], model: type
) -> int:
    async with factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(model))).scalar_one()
        )


def test_agent_submit_over_cap_creates_nothing(
    tmp_path: Path, isolated_config: None
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "queue-cap.db")
        guard = SpendGuard(
            factory, settings_provider=_Limits(medicine=1), clock=lambda: NOW
        )
        queue = QueueService(factory)
        queue.bind_spend_guard(guard)
        try:
            with pytest.raises(AppError) as caught:
                await queue.submit(_payload(medicine=2), source=PipelineSource.AGENT)

            assert caught.value.code is ErrorCode.SPEND_CAP_EXCEEDED
            assert caught.value.details["kind"] == "medicine"
            assert caught.value.details["source"] == "agent"
            assert await _count(factory, Pipeline) == 0
            assert await _count(factory, Task) == 0
            assert await _count(factory, SpendLedger) == 0
            blocked = guard.blocked_today()
            assert len(blocked) == 1
            assert blocked[0]["source"] == "agent"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_idempotent_replay_adds_no_ledger_rows(
    tmp_path: Path, isolated_config: None
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "queue-idem.db")
        guard = SpendGuard(
            factory, settings_provider=_Limits(medicine=999), clock=lambda: NOW
        )
        queue = QueueService(factory)
        queue.bind_spend_guard(guard)
        try:
            first, reused = await queue.submit(
                _payload(medicine=2),
                source=PipelineSource.AGENT,
                idempotency_key="m84-queue-key",
            )
            assert reused is False
            assert await _count(factory, Pipeline) == 1
            assert await _count(factory, SpendLedger) == 1

            second, replayed = await queue.submit(
                _payload(medicine=2),
                source=PipelineSource.AGENT,
                idempotency_key="m84-queue-key",
            )
            assert replayed is True
            assert second.id == first.id
            assert await _count(factory, Pipeline) == 1
            assert await _count(factory, SpendLedger) == 1
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_queue_without_guard_behaves_as_v2(
    tmp_path: Path, isolated_config: None
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "queue-v2.db")
        queue = QueueService(factory)
        try:
            pipeline, reused = await queue.submit(
                _payload(medicine=2), source=PipelineSource.AGENT
            )
            assert reused is False
            assert str(pipeline.status) == "pending"
            assert await _count(factory, Pipeline) == 1
            assert await _count(factory, SpendLedger) == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_manual_submit_over_cap_is_allowed_but_counted(
    tmp_path: Path, isolated_config: None
) -> None:
    """裁决 R1：前端手动提交不受限，但仍写账本行。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "queue-manual.db")
        guard = SpendGuard(
            factory, settings_provider=_Limits(medicine=0), clock=lambda: NOW
        )
        queue = QueueService(factory)
        queue.bind_spend_guard(guard)
        try:
            pipeline, _ = await queue.submit(
                _payload(medicine=2), source=PipelineSource.MANUAL
            )
            assert pipeline.source == PipelineSource.MANUAL.value
            async with factory() as session:
                rows = list(
                    (await session.execute(select(SpendLedger))).scalars().all()
                )
            assert len(rows) == 1
            assert (rows[0].kind, rows[0].planned, rows[0].source) == (
                "medicine",
                2,
                PipelineSource.MANUAL.value,
            )
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
