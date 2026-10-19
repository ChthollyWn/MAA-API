"""干员表服务的读侧契约（M27 规格 §7.3、§7.5）：列表筛选、概要、单条 404。

**M47 起账号干员数据只有一个来源**——森空岛练度同步（``apply_skland_sync``，写侧契约见
``test_account_box_skland.py``）：内核 ``OperBox`` 识别合并、手动编辑抽屉与识别差异三条
通道一起下线（docs/13 §8.70），所以本文件只钉读侧。

全部离线：临时 SQLite（``make_engine`` + ``create_all``，``NullPool`` 以便用例里多个
``asyncio.run`` 共用一个 engine），不打网、不打真机。时间用手工构造的 naive UTC 值
（库内统一 naive UTC，docs/04 §3.2）；过期判定读的是真实时钟，所以把服务层的
``_utcnow`` 冻结到 :data:`NOW`，否则测试会随真实日期推移而失效（2026-10-19 起
``recognized_at`` 与真实 now 的差值越过 14 天阈值，两条概要用例就是这样翻红的）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.repositories.account import AccountOperatorRepository
from maa_api.db.session import make_engine
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.box import OperatorBoxService
from maa_api.settings import Settings

#: 依赖并行任务的落地物：仓储模块与五张表。还没落地时整模块 skip，落地后自动变绿
#: （见本里程碑的交接说明）；不把 ImportError 伪装成用例失败。
pytest.importorskip(
    "maa_api.db.repositories.account",
    reason="M27 的表与仓储（db/models.py、db/repositories/account.py）尚未落地",
)


NOW = datetime(2026, 10, 10, 3, 0, 0)


def _utc(*, days_ago: int = 0) -> datetime:
    return NOW - timedelta(days=days_ago)


@pytest.fixture(autouse=True)
def _freeze_box_clock(monkeypatch):
    """种子时间都以固定 :data:`NOW` 为基准，过期判定用的真实时钟必须一起冻结。"""
    monkeypatch.setattr("maa_api.services.account.box._utcnow", lambda: NOW)


@pytest.fixture
def account_session_factory(tmp_path):
    """临时库 + ``NullPool`` 会话工厂（跨多个 ``asyncio.run`` 复用同一个 engine）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'account_box.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _service(factory, *, stale_days: int = 14) -> OperatorBoxService:
    """固定 ``agent.box_stale_days`` 的服务实例（不读真实 config.yaml）。"""
    settings = Settings()
    settings.agent.box_stale_days = stale_days
    return OperatorBoxService(factory, settings_provider=lambda: settings)


def _seed(factory, char_id: str, **fields) -> None:
    """直接经仓储写一行，形状与一轮森空岛同步落库后的行一致。"""
    defaults: dict[str, object] = dict(
        name="测试干员",
        rarity=6,
        profession="近卫",
        own=True,
        elite=2,
        level=90,
        potential=6,
        skill_level=7,
        masteries={"1": 3, "2": 0, "3": 0},
        modules={"X": 3},
        recognized_values={
            "own": True,
            "elite": 2,
            "level": 90,
            "potential": 6,
        },
        field_sources={
            "own": "skland",
            "elite": "skland",
            "level": "skland",
            "potential": "skland",
            "skill_level": "skland",
            "masteries": "skland",
            "modules": "skland",
        },
        recognized_at=_utc(),
        updated_at=_utc(),
    )
    defaults.update(fields)

    async def scenario():
        async with factory() as session:
            repo = AccountOperatorRepository(session)
            # ``new_row(char_id, **fields)`` 是仓储的建行入口（这里逐字段赋值，
            # 避开对关键字形参名的依赖）。
            row = repo.new_row(char_id)
            for field, value in defaults.items():
                setattr(row, field, value)
            await repo.upsert(row)
            await session.commit()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 列表筛选与概要
# ---------------------------------------------------------------------------


def test_list_operators_filters_and_paginates(account_session_factory):
    _seed(account_session_factory, "char_a", name="阿米娅", rarity=5, profession="术师", elite=2)
    _seed(account_session_factory, "char_b", name="陈", rarity=6, profession="近卫", elite=1)
    _seed(account_session_factory, "char_c", name="能天使", rarity=6, profession="狙击", own=False, elite=0)
    service = _service(account_session_factory)

    async def scenario():
        page = await service.list_operators(limit=1, offset=0)
        assert page["total"] == 2  # 默认隐藏未拥有
        assert page["limit"] == 1 and page["offset"] == 0
        assert len(page["items"]) == 1
        assert set(page["items"][0]) == {
            "char_id",
            "name",
            "rarity",
            "profession",
            "own",
            "elite",
            "level",
            "potential",
            "skill_level",
            "masteries",
            "modules",
            # M43：卡片用的技能 id 与模组明细（「不知道」同样是 None）。
            "skill_ids",
            "module_details",
            # M42：信赖随六列一起进 wire。
            "favor_percent",
            "field_sources",
            "recognized_at",
            "updated_at",
        }

        assert (await service.list_operators(rarity_min=6))["total"] == 1
        assert (await service.list_operators(profession="狙击", owned_only=False))["total"] == 1
        assert (await service.list_operators(elite_min=2))["total"] == 1
        assert (await service.list_operators(query="能天", owned_only=False))["total"] == 1
        assert (await service.list_operators(query="  "))["total"] == 2  # 空串忽略

        second = await service.list_operators(limit=1, offset=1)
        assert second["total"] == 2 and len(second["items"]) == 1
        assert second["items"][0]["char_id"] != page["items"][0]["char_id"]

        # limit / offset 越界由服务层夹住，不把负数丢给 SQLite。
        clamped = await service.list_operators(limit=0, offset=-5)
        assert clamped["limit"] == 1 and clamped["offset"] == 0

    asyncio.run(scenario())


def test_summary_counts_owned_operators_and_elite_two(account_session_factory):
    _seed(account_session_factory, "char_a", elite=2)
    _seed(account_session_factory, "char_b", elite=1)
    _seed(account_session_factory, "char_c", own=False, elite=0)
    service = _service(account_session_factory)

    async def scenario():
        summary = await service.summary()
        assert summary["total"] == 2
        assert summary["elite_two"] == 1
        assert summary["unrecognized"] == 1
        assert summary["stale_days"] == 14
        assert summary["stale"] is False
        assert summary["recognized_at"] == "2026-10-10T03:00:00Z"
        # M47：差异条随识别 / 手动 / Agent 三条写通道下线，概要不再有 pending_diffs。
        assert "pending_diffs" not in summary

    asyncio.run(scenario())


def test_summary_uses_the_configured_stale_threshold(account_session_factory):
    _seed(account_session_factory, "char_a", recognized_at=_utc(days_ago=5))
    service = _service(account_session_factory, stale_days=14)
    strict = _service(account_session_factory, stale_days=3)

    async def scenario():
        assert (await service.summary())["stale"] is False
        assert (await strict.summary())["stale"] is True

    asyncio.run(scenario())


def test_summary_is_stale_when_nothing_was_ever_recognized(account_session_factory):
    service = _service(account_session_factory)

    async def scenario():
        summary = await service.summary()
        assert summary == {
            "total": 0,
            "elite_two": 0,
            "recognized_at": None,
            "stale": True,
            "stale_days": 14,
            "unrecognized": 0,
        }

    asyncio.run(scenario())


def test_get_operator_missing_row_is_404(account_session_factory):
    service = _service(account_session_factory)

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.get_operator("char_none")
        assert caught.value.code == ErrorCode.ACCOUNT_DATA_NOT_FOUND
        assert caught.value.http_status == 404
        assert caught.value.details == {"char_id": "char_none"}
        # M47：提示语指向唯一的写入通道（森空岛同步），不再让人去跑干员识别。
        assert "森空岛" in caught.value.message

    asyncio.run(scenario())
