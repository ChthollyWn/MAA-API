"""仓库（Depot）服务契约（M27 规格 §7.3、§7.5）：整表替换、筛选、过期与识别记录。

全部离线：临时 SQLite（``make_engine`` + ``create_all``，``NullPool``），不打网、
不打真机。仓库识别「只看养成材料页签、龙门币与作战记录不在其中」是识别侧的约定，
这里只保证服务层按整表替换语义读写。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.repositories.account import AccountRecognitionRepository
from maa_api.db.session import make_engine
from maa_api.services.account.depot import DepotService
from maa_api.settings import Settings

import pytest

#: 依赖并行任务的落地物：仓储模块与五张表。还没落地时整模块 skip，落地后自动变绿
#: （见本里程碑的交接说明）；不把 ImportError 伪装成用例失败。
pytest.importorskip(
    "maa_api.db.repositories.account",
    reason="M27 的表与仓储（db/models.py、db/repositories/account.py）尚未落地",
)


#: 基时刻取「当前整点」而不是写死日期：``_is_stale`` 读的是真实时钟，写死日期一旦跨过
#: 「基准日 + 过期天数」就会把「两天前的记录」判成过期（2026-10-11 起这条用例真红过）。
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)


def _utc(*, days_ago: int = 0) -> datetime:
    return NOW - timedelta(days=days_ago)


def _iso(moment: datetime) -> str:
    """与 ``DepotService`` 出参同款的时间字面量（naive UTC + ``Z``）。"""
    return moment.isoformat() + "Z"


@pytest.fixture
def account_session_factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'account_depot.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _service(factory, *, stale_days: int = 3, min_items: int = 0) -> DepotService:
    """本文件里的用例大多写 2–3 件的小表，所以**默认关掉下限护栏**（``min_items=0``）；
    护栏本身由 ``test_min_items_guard_blocks_a_partial_recognition`` 显式打开覆盖。"""
    settings = Settings()
    settings.agent.depot_stale_days = stale_days
    settings.agent.depot_min_items = min_items
    return DepotService(factory, settings_provider=lambda: settings)


def test_replace_all_swaps_the_whole_table(account_session_factory):
    """整表替换：第二次识别里没有的物品必须消失，不是「合并」。"""
    service = _service(account_session_factory)

    async def scenario():
        written = await service.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                {"item_id": "30013", "name": "聚酸酯块", "count": 5, "category": "材料"},
            ],
            recognized_at=NOW,
        )
        assert written == 2
        first = await service.list_items()
        assert first["total"] == 2
        assert [item["item_id"] for item in first["items"]] == ["30012", "30013"]
        assert first["items"][0]["recognized_at"] == _iso(NOW)

        written = await service.replace_all(
            [{"item_id": "30013", "name": "聚酸酯块", "count": 9, "category": "材料"}],
            recognized_at=NOW,
        )
        assert written == 1
        second = await service.list_items()
        assert second["total"] == 1
        assert second["items"][0]["count"] == 9

        # 识别到空仓库也是合法的：整表清空。
        assert await service.replace_all([], recognized_at=NOW) == 0
        assert (await service.list_items())["total"] == 0

    asyncio.run(scenario())


def test_replace_all_normalizes_rows(account_session_factory):
    """缺 name 用 item_id 兜底、负数量归零、空 item_id 丢弃、重复取最后一条。"""
    service = _service(account_session_factory)

    async def scenario():
        written = await service.replace_all(
            [
                {"item_id": "30012", "count": 3},
                {"item_id": "30012", "count": 4, "character": "ignored"},
                {"item_id": "", "count": 5},
                {"item_id": "30013", "name": " ", "count": -2},
            ],
            recognized_at=NOW,
        )
        assert written == 2
        items = {item["item_id"]: item for item in (await service.list_items())["items"]}
        assert items["30012"]["name"] == "30012"
        assert items["30012"]["count"] == 4
        assert items["30013"]["count"] == 0

    asyncio.run(scenario())


def test_list_items_filters_by_category_and_name_with_pagination(
    account_session_factory,
):
    service = _service(account_session_factory)

    async def scenario():
        await service.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                {"item_id": "30013", "name": "聚酸酯块", "count": 5, "category": "材料"},
                {"item_id": "3231", "name": "技巧概要·卷1", "count": 40, "category": "技巧概要"},
            ],
            recognized_at=NOW,
        )
        assert (await service.list_items(category="材料"))["total"] == 2
        assert (await service.list_items(query="聚酸酯"))["total"] == 2
        assert (await service.list_items(category="材料", query="块"))["total"] == 1
        assert (await service.list_items(category="  "))["total"] == 3  # 空串忽略
        assert (await service.list_items(category="不存在"))["total"] == 0

        page = await service.list_items(limit=1, offset=1)
        assert page["total"] == 3
        assert page["limit"] == 1 and page["offset"] == 1
        assert len(page["items"]) == 1

        clamped = await service.list_items(limit=0, offset=-3)
        assert clamped["limit"] == 1 and clamped["offset"] == 0

    asyncio.run(scenario())


def test_summary_uses_the_depot_recognition_record_and_threshold(
    account_session_factory,
):
    """概要的识别时间取 ``account_recognition`` 的记录，不取物品行的最大值。"""
    service = _service(account_session_factory, stale_days=3)
    strict = _service(account_session_factory, stale_days=1)

    async def scenario():
        # 从未识别。
        fresh = await service.summary()
        assert fresh == {
            "total": 0,
            "recognized_at": None,
            "stale": True,
            "stale_days": 3,
        }

        await service.replace_all(
            [{"item_id": "30012", "name": "聚酸酯组", "count": 3}],
            recognized_at=_utc(days_ago=2),
        )
        summary = await service.summary()
        assert summary["total"] == 1
        assert summary["recognized_at"] == _iso(_utc(days_ago=2))
        assert summary["stale"] is False
        assert (await strict.summary())["stale"] is True

    asyncio.run(scenario())


def test_replace_all_writes_a_depot_recognition_record(account_session_factory):
    """识别记录写进 ``account_recognition``：概要的识别时间读的是它。

    ``pipeline_id`` / ``task_id`` 有外键（指向流水线与任务表），这里不传——真实调用方
    （capture 层）传的是库里确实存在的那两条 id。
    """
    service = _service(account_session_factory)

    async def scenario():
        await service.replace_all(
            [{"item_id": "30012", "name": "聚酸酯组", "count": 3}],
            recognized_at=NOW,
            client_type="Bilibili",
        )
        async with account_session_factory() as session:
            record = await AccountRecognitionRepository(session).last_at("depot")
        assert record == NOW

    asyncio.run(scenario())


def test_summary_counts_items_written_by_the_recognition_path(
    account_session_factory,
):
    """多次识别之间概要只反映当前表（整表替换），不累加历史条数。"""
    service = _service(account_session_factory)

    async def scenario():
        await service.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3},
                {"item_id": "30013", "name": "聚酸酯块", "count": 5},
            ],
            recognized_at=NOW,
        )
        assert (await service.summary())["total"] == 2
        await service.replace_all(
            [{"item_id": "30012", "name": "聚酸酯组", "count": 1}],
            recognized_at=NOW + timedelta(hours=1),
        )
        summary = await service.summary()
        assert summary["total"] == 1
        assert summary["recognized_at"] == _iso(NOW + timedelta(hours=1))

    asyncio.run(scenario())


def test_min_items_guard_blocks_a_partial_recognition(account_session_factory):
    """M44：条目数低于下限时整轮不写——真机实测过 26 件覆盖 77 行的后果。"""

    async def scenario():
        service = _service(account_session_factory, min_items=20)
        # 先写一份「完整」的 30 件。
        full = [{"item_id": f"30{i:03d}", "name": f"物品{i}", "count": i + 1} for i in range(30)]
        assert await service.replace_all(full) == 30

        # 半截识别（3 件）被护栏拦下：表还在，条数不变。
        partial = [{"item_id": "3003", "name": "赤金", "count": 1}]
        assert await service.replace_all(partial) == 0
        page = await service.list_items()
        assert page["total"] == 30
        assert {row["item_id"] for row in page["items"]} == {item["item_id"] for item in full}

        # 关掉护栏（0）时行为回到整表替换。
        relaxed = _service(account_session_factory, min_items=0)
        assert await relaxed.replace_all(partial) == 1
        page = await relaxed.list_items()
        assert page["total"] == 1

    asyncio.run(scenario())
