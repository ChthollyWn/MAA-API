"""M45 W3a：两路结果的合并规则与写库形状（规格 §4.5）。

内核 `Depot` 那一趟（95 个材料 + 5 个硬编码基础物品）与 M45 自识别的那一趟按 id 合并：
**并集**、同一 id 以内核为准（它给得出数量）、自识别独有的 id 写 ``NULL`` ＝ 未识别、
每行的来源进 ``sources``、整轮的过程事实进识别记录的 ``detail``。

本文件前半段是纯函数（不碰库），后半段用临时 SQLite 验写库形状：
来源列、``count`` 可空、整份快照的 ``sources`` / ``unrecognized`` / ``coverage``，
以及 M44 的下限护栏在合并路径上仍然生效。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
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
    """来源进 ``sources`` 列、未识别的数量留在 ``NULL``（不是 0）。"""
    service = _service(depot)
    merged = merge_depot_sources(KERNEL, SCANNED, catalog=CATALOG)

    async def scenario():
        written = await service.replace_all(
            merged.rows, recognized_at=NOW, item_sources=merged.sources
        )
        assert written == 4
        return await service.list_items()

    payload = asyncio.run(scenario())

    rows = {row["item_id"]: row for row in payload["items"]}
    assert rows["3003"]["count"] == 3092 and rows["3003"]["source"] == "kernel"
    assert rows["7003"]["count"] is None and rows["7003"]["source"] == "self_scan"
    assert payload["sources"] == {
        "3003": "kernel",
        "4001": "kernel",
        "7003": "self_scan",
        "4004": "self_scan",
    }
    assert payload["coverage"] == {
        "total": 4,
        "kernel": 2,
        "self_scan": 2,
        "unrecognized": 0,
        "scanned": {},
    }


def test_replace_all_without_sources_keeps_the_old_shape(depot) -> None:
    """不传 ``item_sources`` 时整列留 NULL、``source`` 读出来是 None（M27 路径不变）。"""
    service = _service(depot)

    async def scenario():
        await service.replace_all(
            [{"item_id": "3003", "name": "赤金", "count": 3092}], recognized_at=NOW
        )
        return await service.list_items()

    payload = asyncio.run(scenario())

    assert payload["items"][0]["source"] is None
    assert payload["sources"] == {}


def test_depot_state_reports_the_unrecognized_list_from_the_record(depot) -> None:
    """未识别清单来自识别记录的 ``detail``：接口层据此显示「未识别」而不是 0。"""
    service = _service(depot)
    merged = merge_depot_sources(KERNEL, SCANNED, catalog=CATALOG)

    async def scenario():
        await service.replace_all(
            merged.rows,
            recognized_at=NOW,
            item_sources=merged.sources,
            detail=merged.detail(scanned={"pages": 2, "stop_reason": "no_new"}),
        )
        return await service.depot_state()

    payload = asyncio.run(scenario())

    assert payload["unrecognized"] == []
    assert payload["coverage"]["scanned"] == {"pages": 2, "stop_reason": "no_new"}


def test_depot_state_lists_ids_neither_source_covered(depot) -> None:
    """两路都没覆盖到的 id 如实列出（M45 W0：缺失与 0 库存无法区分 ⇒ 不能当 0）。"""
    service = _service(depot)
    merged = merge_depot_sources(
        {"3003": 3092}, {}, catalog=CATALOG, depot_ids=("3003", "4004", "7003")
    )

    async def scenario():
        await service.replace_all(
            merged.rows,
            recognized_at=NOW,
            item_sources=merged.sources,
            detail=merged.detail(),
        )
        return await service.depot_state()

    payload = asyncio.run(scenario())

    assert payload["unrecognized_total"] == 2
    assert [entry["item_id"] for entry in payload["unrecognized"]] == ["4004", "7003"]
    assert payload["coverage"]["unrecognized"] == 2


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
