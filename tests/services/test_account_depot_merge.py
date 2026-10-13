"""M45 W3a 的两路合并规则（规格 §4.5）＋ 2026-10-13 关停后的读侧口径。

内核 `Depot` 那一趟（95 个材料 + 5 个硬编码基础物品）与 M45 自识别的那一趟按 id 合并：
**并集**、同一 id 以内核为准（它给得出数量）、自识别独有的 id 写 ``NULL`` ＝ 未识别、
每行的来源进 ``sources``、整轮的过程事实进识别记录的 ``detail``。

**这组能力已于 2026-10-13 按用户要求关停并下线**（`docs/13` §8.68，M46 的 OCR 复用
实验 53% 未达止损线）：``account.depot_scan_enabled`` 默认 false，采集层不再走合并
（见 ``test_account_capture.py::test_disabled_self_scan_keeps_the_m44_write_path``），
``GET /api/account/depot`` 也不再返回来源表、未识别清单与覆盖统计，只给有数量的行。

所以本文件分两段：**纯函数**（合并规则本身，代码保留作备档，将来若重开还得靠它）与
**写库 / 读侧形状**（关停后的口径：``NULL`` 行留在库里、读侧一律不出现）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.models import AccountDepotItem
from maa_api.db.repositories.account import AccountRecognitionRepository
from maa_api.db.session import make_engine
from maa_api.services.account.depot import (
    DEPOT_SOURCE_KERNEL,
    DEPOT_SOURCE_SELF_SCAN,
    DepotService,
    enumerate_depot_ids,
    merge_depot_sources,
    unrecognized_depot_items,
)
from maa_api.settings import Settings

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)


class _Meta:
    """``capture.ItemMeta`` 的最小替身（只需要 name / category 两个字段）。"""

    def __init__(self, name: str | None = None, category: str | None = None) -> None:
        self.name = name
        self.category = category


#: 一份小目录：一件材料、一件凭据（内核白名单外）、一件按机制不进仓库的信物。
CATALOG = {
    "3003": _Meta("赤金", "MATERIAL"),
    "7003": _Meta("寻访凭证", "NORMAL"),
    "4004": _Meta("高级凭证", "NORMAL"),
    "char_002_amiya_token": _Meta("阿米娅的信物", "MEMENTO"),
}

KERNEL = {"3003": 3092, "4001": 4722000}
SCANNED = {"7003": 0.9025, "4004": 0.8999, "3003": 0.8712}


# --------------------------------------------------------------------------- #
# 合并规则（纯函数）
# --------------------------------------------------------------------------- #
def test_merge_is_a_union_with_kernel_authoritative_on_counts() -> None:
    """并集；同一 id 两边都有 ⇒ 数量取内核（自识别 W3a 还没读数量）。"""
    merged = merge_depot_sources(KERNEL, SCANNED, catalog=CATALOG, depot_ids=("3003", "4001", "7003", "4004"))

    rows = {row["item_id"]: row for row in merged.rows}
    assert set(rows) == {"3003", "4001", "7003", "4004"}
    assert rows["3003"]["count"] == 3092, "两边都有：数量以内核为准"
    assert rows["3003"]["source"] == DEPOT_SOURCE_KERNEL
    assert rows["4001"]["count"] == 4722000
    assert rows["4001"]["source"] == DEPOT_SOURCE_KERNEL
    assert rows["7003"]["count"] is None, "只有自识别有 ⇒ 身份入库、数量未识别"
    assert rows["7003"]["source"] == DEPOT_SOURCE_SELF_SCAN
    assert rows["7003"]["name"] == "寻访凭证"
    assert rows["7003"]["category"] == "NORMAL"


def test_merge_never_reports_a_missing_item_as_zero() -> None:
    """没识别到的 id 不进表、也不写 0：它们进 ``unrecognized``。"""
    merged = merge_depot_sources(
        {"3003": 3092}, {"7003": 0.9}, catalog=CATALOG, depot_ids=("3003", "4004", "7003")
    )

    rows = {row["item_id"] for row in merged.rows}
    assert "4004" not in rows
    assert "4004" in merged.unrecognized
    assert merged.unrecognized == ("4004",)


def test_merge_treats_a_kernel_zero_as_a_real_zero() -> None:
    """内核给了 0 就写 0（整表替换的既有语义）—— 0 是数量事实，``None`` 才是不知道。"""
    merged = merge_depot_sources({"3003": 0}, {}, catalog=CATALOG, depot_ids=("3003",))

    assert merged.rows[0]["count"] == 0
    assert merged.sources == {"3003": DEPOT_SOURCE_KERNEL}
    assert merged.unrecognized == ()


def test_merge_without_a_self_scan_is_the_kernel_only_path() -> None:
    """自识别未启用时：结果就是内核那一趟，一行不多、一行不少（M27 行为不变）。"""
    merged = merge_depot_sources(KERNEL, None, catalog=CATALOG, depot_ids=("3003", "4001", "7003"))

    assert {row["item_id"] for row in merged.rows} == {"3003", "4001"}
    assert set(merged.sources.values()) == {DEPOT_SOURCE_KERNEL}
    assert merged.unrecognized == ("7003",)


def test_merge_ignores_non_depot_categories_when_listing_unrecognized() -> None:
    """``MEMENTO`` / ``NONE`` 按机制不进仓库，不算「未识别」候选。"""
    ids = enumerate_depot_ids(CATALOG)

    assert ids == ("3003", "4004", "7003")
    assert "char_002_amiya_token" not in ids
    # 原始 dict 形状（item_index.json 的读法）同样认。
    assert enumerate_depot_ids({"7003": {"classifyType": "NORMAL"}}) == ("7003",)


def test_unrecognized_helper_keeps_names_and_the_rest_is_not_guessed() -> None:
    """来源表为空（旧数据）时不给未识别清单 —— 不知道就别假装知道。"""
    assert unrecognized_depot_items(("3003", "7003"), None) == []
    listed = unrecognized_depot_items(("3003", "7003"), {"3003": "kernel"}, names={"7003": "寻访凭证"})
    assert listed == [{"item_id": "7003", "name": "寻访凭证"}]
    assert unrecognized_depot_items(("3003", "7003"), {"3003": "kernel"}, limit=0) == []


def test_merge_detail_carries_sources_and_the_unrecognized_list() -> None:
    """``detail`` 是写进识别记录的过程事实（接口层据此讲清「未识别」）。"""
    merged = merge_depot_sources(
        KERNEL, SCANNED, catalog=CATALOG, depot_ids=("3003", "4001", "7003", "4004")
    )

    detail = merged.detail(scanned={"pages": 3, "stop_reason": "no_new"})

    assert detail["sources"]["7003"] == DEPOT_SOURCE_SELF_SCAN
    assert detail["unrecognized"] == []
    assert detail["scanned"] == {"pages": 3, "stop_reason": "no_new"}


# --------------------------------------------------------------------------- #
# 写库形状（临时 SQLite）
# --------------------------------------------------------------------------- #
@pytest.fixture
def depot(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'depot_merge.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _service(factory, *, min_items: int = 0) -> DepotService:
    settings = Settings()
    settings.agent.depot_stale_days = 3
    settings.agent.depot_min_items = min_items
    return DepotService(factory, settings_provider=lambda: settings)


def test_replace_all_records_sources_and_keeps_count_null(depot) -> None:
    """写库形状仍是 M45 的：来源进 ``sources`` 列、未识别的数量留在 ``NULL``（不是 0）。

    写侧保留这条能力（代码作备档），但**读侧不再返回这些行** —— 见下一个用例。
    """
    service = _service(depot)
    merged = merge_depot_sources(KERNEL, SCANNED, catalog=CATALOG)

    async def scenario():
        written = await service.replace_all(
            merged.rows, recognized_at=NOW, item_sources=merged.sources
        )
        assert written == 4
        async with depot() as session:
            rows = (
                await session.execute(select(AccountDepotItem))
            ).scalars().all()
        return {row.item_id: row for row in rows}

    rows = asyncio.run(scenario())

    assert rows["3003"].count == 3092
    assert rows["3003"].sources == merged.sources
    assert rows["7003"].count is None
    assert rows["7003"].sources == merged.sources


def test_list_items_hides_rows_without_a_count(depot) -> None:
    """用户 2026-10-13 的口径：仓库页只给有明确数量的行，``NULL`` 行留在库里但不出现。"""
    service = _service(depot)
    merged = merge_depot_sources(KERNEL, SCANNED, catalog=CATALOG)

    async def scenario():
        await service.replace_all(
            merged.rows, recognized_at=NOW, item_sources=merged.sources
        )
        payload = await service.list_items()
        summary = await service.summary()
        async with depot() as session:
            stored = len((await session.execute(select(AccountDepotItem))).scalars().all())
        return payload, summary, stored

    payload, summary, stored = asyncio.run(scenario())

    # 表里 4 行都在（没有删行），读侧只给内核那 2 行。
    assert stored == 4
    assert {item["item_id"] for item in payload["items"]} == {"3003", "4001"}
    assert payload["total"] == 2
    assert summary["total"] == 2
    # 每一行都带得出数量，且形状回到 007/0021 之前那五列（来源不再透出）。
    assert set(payload["items"][0]) == {
        "item_id",
        "name",
        "count",
        "category",
        "recognized_at",
        "source",
    }
    assert all(isinstance(item["count"], int) for item in payload["items"])


def test_replace_all_without_sources_keeps_the_old_shape(depot) -> None:
    """不传 ``item_sources`` 时整列留 NULL、``source`` 读出来是 None（M44 路径不变）。"""
    service = _service(depot)

    async def scenario():
        await service.replace_all(
            [{"item_id": "3003", "name": "赤金", "count": 3092}], recognized_at=NOW
        )
        return await service.list_items()

    payload = asyncio.run(scenario())

    assert [item["item_id"] for item in payload["items"]] == ["3003"]
    assert payload["items"][0]["source"] is None


def test_recognition_detail_stays_a_record_without_reaching_the_wire(depot) -> None:
    """未识别清单仍写进识别记录 ``detail``（事后查得到），但不随接口透出。"""
    service = _service(depot)
    merged = merge_depot_sources(
        {"3003": 3092}, {}, catalog=CATALOG, depot_ids=("3003", "4004", "7003")
    )

    async def scenario():
        await service.replace_all(
            merged.rows,
            recognized_at=NOW,
            item_sources=merged.sources,
            detail=merged.detail(scanned={"pages": 2, "stop_reason": "no_new"}),
        )
        payload = await service.list_items()
        async with depot() as session:
            latest = await AccountRecognitionRepository(session).latest("depot")
        return payload, latest

    payload, latest = asyncio.run(scenario())

    # 记录里那两件「这一轮没识别到」还在（备档：将来重开自识别时查得到）。
    assert latest is not None and latest.detail is not None
    assert [entry["item_id"] for entry in latest.detail["unrecognized"]] == ["4004", "7003"]
    # 接口只剩这五列：来源 / 未识别 / 覆盖三项都不在了。
    assert set(payload) == {"items", "total", "limit", "offset", "categories"}
    assert payload["total"] == 1


def test_merge_path_still_respects_the_min_items_guard(depot) -> None:
    """M44 的下限护栏在合并路径上照旧生效：整轮少于 ``depot_min_items`` 就整轮不写。"""
    service = _service(depot, min_items=5)
    merged = merge_depot_sources(KERNEL, SCANNED, catalog=CATALOG)

    async def scenario():
        assert (
            await service.replace_all(
                merged.rows, recognized_at=NOW, item_sources=merged.sources
            )
            == 0
        )
        return await service.summary()

    summary = asyncio.run(scenario())

    assert summary["total"] == 0
    assert summary["recognized_at"] is None, "被护栏拦下的那一轮不记识别记录"
