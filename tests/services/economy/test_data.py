"""M88 T4：活动取数、仓库、理智药与模型合并（规格 §4.5.1–§4.5.2）。

全部用固定假对象与真实裁剪夹具（``tests/fixtures/prts/activity_zuori_shop.wikitext``、
``tests/fixtures/gamedata/item_table_ap_supplies.json``）；不联网。
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.planner import FarmingInputs, StageOption
from maa_api.services.economy.data import load_events, medicine_pool, merge, read_depot
from maa_api.services.economy.model import FarmingRequest, MedicineItem, MedicinePool
from maa_api.services.external.envelope import SourceRef
from maa_api.services.external.gamedata import GamedataData
from maa_api.services.game.activities import (
    ActivityReport,
    CurrentActivity,
    StageEntry,
)
from maa_api.services.prts_parse.event_shop import TokenDrop

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))
WORD = "异铁块"
TOKEN = "“未来瞻望镜”"

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PRTS = FIXTURES / "prts"
GAMEDATA = FIXTURES / "gamedata"


# --------------------------------------------------------------------------- #
# 假对象
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FakePage:
    title: str
    content: str
    missing: bool = False
    revision: int = 7


class FakeBatch:
    def __init__(self, pages: list[FakePage], *, fetched_at: datetime = NOW) -> None:
        self.pages = tuple(pages)
        self._fetched_at = fetched_at

    def source_refs(self) -> list[SourceRef]:
        return [
            SourceRef(
                kind="prts",
                label="PRTS",
                title=page.title,
                url=f"https://prts.wiki/w/{page.title}",
                revision=page.revision,
                fetched_at=self._fetched_at,
            )
            for page in self.pages
        ]


class FakePrts:
    def __init__(
        self,
        pages: Mapping[str, FakePage] | None = None,
        *,
        fetched_at: datetime = NOW,
        error: Exception | None = None,
    ) -> None:
        self.pages = dict(pages or {})
        self.fetched_at = fetched_at
        self.error = error
        self.calls: list[list[str]] = []

    async def wikitext(self, titles: list[str]) -> FakeBatch:
        self.calls.append(list(titles))
        if self.error is not None:
            raise self.error
        return FakeBatch(
            [self.pages[title] for title in titles if title in self.pages],
            fetched_at=self.fetched_at,
        )


class FakeActivities:
    def __init__(self, report: ActivityReport) -> None:
        self.report = report
        self.calls: list[str] = []

    async def current(self, *, detail: str = "summary") -> ActivityReport:
        self.calls.append(detail)
        return self.report


class FakeGamedata:
    def __init__(
        self,
        payload: Any = None,
        *,
        fetched_at: datetime = NOW,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.fetched_at = fetched_at
        self.error = error
        self.calls: list[str] = []

    async def excel(self, name: str) -> GamedataData:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return GamedataData(
            data=self.payload,
            url="https://example.invalid/item_table.json",
            fetched_at=self.fetched_at,
            cached=False,
            stale=False,
            via="github",
        )


class FakeDepot:
    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.items = list(items or [])
        self.error = error
        self.calls: list[tuple[int, int]] = []

    async def list_items(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        self.calls.append((limit, offset))
        if self.error is not None:
            raise self.error
        return {"items": list(self.items), "total": len(self.items)}


class FakePlanner:
    def __init__(self, inputs: FarmingInputs) -> None:
        self.inputs = inputs
        self.calls: list[tuple[dict[str, int], str | None]] = []

    async def farming_inputs(
        self,
        needs: Mapping[str, int],
        *,
        server: str | None = None,
        display: Mapping[str, str] | None = None,
    ) -> FarmingInputs:
        self.calls.append((dict(needs), server))
        return copy.deepcopy(self.inputs)


# --------------------------------------------------------------------------- #
# 夹具与构造 helper
# --------------------------------------------------------------------------- #


def wikitext(name: str) -> str:
    return (PRTS / name).read_text(encoding="utf-8")


def ap_supplies_payload() -> dict[str, Any]:
    return json.loads((GAMEDATA / "item_table_ap_supplies.json").read_text(encoding="utf-8"))


def stage_entries() -> tuple[StageEntry, ...]:
    return tuple(
        StageEntry(code=code, name=None, stage_id=stage_id)
        for code, stage_id in (
            ("YW-1", "act52side_01"),
            ("YW-2", "act52side_02"),
            ("YW-3", "act52side_03"),
            ("YW-4", "act52side_04"),
            ("YW-5", "act52side_05"),
            ("YW-6", "act52side_06"),
            ("YW-7", "act52side_07"),
            ("YW-8", "act52side_08"),
            ("YW-EX-1", "act52side_ex1"),
        )
    )


def activity(
    *,
    page: str = "昨日海",
    name: str = "昨日海",
    navigation: str = "available",
    end_at: datetime | None = NOW + timedelta(days=3),
    exchange_end_at: datetime | None = NOW + timedelta(days=10),
    stages: tuple[StageEntry, ...] = (),
) -> CurrentActivity:
    return CurrentActivity(
        name=name,
        page=page,
        start_at=NOW - timedelta(days=3),
        end_at=end_at,
        stage_id_prefix="act52side",
        stages=stages,
        navigation=navigation,
        matched_by="smw",
        source="prts",
    )


def inputs(
    *,
    needs: Mapping[str, int] | None = None,
    options: list[StageOption] | None = None,
    recipes: list[Any] | None = None,
    notes: list[str] | None = None,
) -> FarmingInputs:
    needs = dict(needs or {})
    return FarmingInputs(
        needs=needs,
        display={key: key for key in needs},
        options=list(options or []),
        recipes=list(recipes or []),
        rate_status={},
        notes=list(notes or []),
        server="CN",
    )


def penguin_yw1() -> StageOption:
    return StageOption(
        stage_id="act52side_01",
        stage_code="YW-1",
        ap_cost=9,
        rates={WORD: 0.2},
        details=[{"item_name": WORD, "sanity_per_item": 45.0}],
    )


def request(
    *,
    materials: Mapping[str, int] | None = None,
    plan_id: str | None = None,
    use_depot: bool = True,
    current_sanity: int = 0,
    window_days: float | None = None,
    use_medicine: bool = False,
    include_events: bool = True,
    include_unnavigable: bool = False,
    owned_tokens: Mapping[str, int] | None = None,
) -> FarmingRequest:
    return FarmingRequest(
        materials=materials,
        plan_id=plan_id,
        use_depot=use_depot,
        current_sanity=current_sanity,
        window_days=window_days,
        use_medicine=use_medicine,
        include_events=include_events,
        include_unnavigable=include_unnavigable,
        owned_tokens=dict(owned_tokens or {}),
    )


def medicine_pool_stub() -> MedicinePool:
    return MedicinePool(
        items=[],
        owned_sanity=0,
        counted_sanity=0,
        scope="no_expiry_data",
        expiring_medicine=0,
        note="没有用药：用户没有同意用理智药",
    )


def events_for(fixture: str = "activity_zuori_shop.wikitext") -> list[Any]:
    page = FakePage(title="昨日海", content=wikitext(fixture))
    report = ActivityReport(activities=(activity(stages=stage_entries()),))
    events, _, _ = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts({"昨日海": page}), now=NOW)
    )
    return events


# --------------------------------------------------------------------------- #
# load_events
# --------------------------------------------------------------------------- #


def test_event_stage_merges_with_penguin_option() -> None:
    page = FakePage(title="昨日海", content=wikitext("activity_zuori_shop.wikitext"))
    report = ActivityReport(activities=(activity(stages=stage_entries()),))
    events, sources, _ = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts({"昨日海": page}), now=NOW)
    )

    model = merge(
        inputs(needs={WORD: 10}, options=[penguin_yw1()]),
        depot={},
        events=events,
        request=request(materials={WORD: 10}),
        medicine=medicine_pool_stub(),
        now=NOW,
    )

    assert model.tokens_of["act52side_01"] == (TOKEN, 9)
    assert model.event_of["act52side_01"] == "昨日海"
    assert any(source["kind"] == "prts" for source in sources)
    assert all(option.stage_code != "YW-EX-1" for option in model.options)


def test_unnavigable_event_is_excluded_by_default() -> None:
    page = FakePage(title="昨日海", content=wikitext("activity_zuori_shop.wikitext"))
    report = ActivityReport(
        activities=(activity(navigation="missing", stages=stage_entries()),)
    )
    events, _, _ = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts({"昨日海": page}), now=NOW)
    )
    feeding = inputs(needs={WORD: 10}, options=[penguin_yw1()])

    model = merge(
        feeding,
        depot={},
        events=events,
        request=request(materials={WORD: 10}, include_unnavigable=False),
        medicine=medicine_pool_stub(),
        now=NOW,
    )

    assert "act52side_01" not in model.event_of
    assert all(option.stage_code != "YW-1" for option in model.options)
    assert any("MAA 还不能自动导航" in note for note in feeding.notes)


def test_include_unnavigable_adds_event_stages() -> None:
    page = FakePage(title="昨日海", content=wikitext("activity_zuori_shop.wikitext"))
    report = ActivityReport(
        activities=(activity(navigation="missing", stages=stage_entries()),)
    )
    events, _, _ = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts({"昨日海": page}), now=NOW)
    )

    model = merge(
        inputs(needs={WORD: 10}, options=[penguin_yw1()]),
        depot={},
        events=events,
        request=request(materials={WORD: 10}, include_unnavigable=True),
        medicine=medicine_pool_stub(),
        now=NOW,
    )

    assert "act52side_01" in model.event_of
    assert model.navigable_of["act52side_01"] is False


def test_cumulative_deadline_budget_does_not_duplicate_current_sanity() -> None:
    pages = {
        "昨日海": FakePage(title="昨日海", content=wikitext("activity_zuori_shop.wikitext")),
        "昨日海·复刻": FakePage(
            title="昨日海·复刻", content=wikitext("activity_zuori_shop.wikitext")
        ),
    }
    deadline = NOW + timedelta(days=2)
    report = ActivityReport(
        activities=(
            activity(page="昨日海", end_at=deadline, stages=stage_entries()),
            activity(
                page="昨日海·复刻",
                name="昨日海·复刻",
                end_at=deadline,
                stages=stage_entries(),
            ),
        )
    )
    events, _, _ = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts(pages), now=NOW)
    )

    model = merge(
        inputs(needs={WORD: 10}, options=[penguin_yw1()]),
        depot={},
        events=events,
        request=request(materials={WORD: 10}, current_sanity=120),
        medicine=medicine_pool_stub(),
        now=NOW,
    )

    assert len(model.deadline_budgets) == 1
    moment, budget = model.deadline_budgets[0]
    assert moment == deadline
    assert budget == pytest.approx(120 + 2 * 240)


def test_fallback_reason_is_noted() -> None:
    report = ActivityReport(activities=(), fallback_reason="PRTS 不可用")

    events, sources, notes = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts(), now=NOW)
    )

    assert events == []
    assert sources == []
    assert any("当期活动没有取到" in note for note in notes)


def test_event_without_shop_keeps_stages_and_notes() -> None:
    page = FakePage(title="昨日海", content=wikitext("activity_zuori.wikitext"))
    report = ActivityReport(activities=(activity(stages=stage_entries()),))
    events, _, notes = asyncio.run(
        load_events(activities=FakeActivities(report), prts=FakePrts({"昨日海": page}), now=NOW)
    )

    assert len(events) == 1
    event = events[0]
    assert event.shop == ()
    assert event.drops["YW-1"] == TokenDrop("YW-1", 9, 9)
    assert any("没有商店小节" in note for note in notes)

    model = merge(
        inputs(needs={WORD: 10}, options=[penguin_yw1()]),
        depot={},
        events=events,
        request=request(materials={WORD: 10}),
        medicine=medicine_pool_stub(),
        now=NOW,
    )

    assert model.shop == []
    assert model.tokens_of["act52side_01"] == (TOKEN, 9)
    token_only = next(option for option in model.options if option.stage_code == "YW-2")
    assert token_only.stage_id == "act52side_02"
    assert token_only.rates == {}


# --------------------------------------------------------------------------- #
# read_depot
# --------------------------------------------------------------------------- #


def test_read_depot_indexes_item_id_and_name() -> None:
    depot = FakeDepot(
        [
            {"item_id": "30013", "name": WORD, "count": 7},
            {"item_id": "ap_supply_lt_60", "name": "应急理智合剂", "count": 3},
        ]
    )

    counts, notes = asyncio.run(read_depot(depot))

    assert counts["30013"] == 7
    assert counts[WORD] == 7
    assert counts["应急理智合剂"] == 3
    assert notes == []


def test_read_depot_failure_is_noted() -> None:
    counts, notes = asyncio.run(read_depot(FakeDepot(error=RuntimeError("db 挂了"))))

    assert counts == {}
    assert any("仓库数据读取失败" in note for note in notes)


# --------------------------------------------------------------------------- #
# medicine_pool
# --------------------------------------------------------------------------- #


def test_medicine_pool_from_real_ap_supplies() -> None:
    pool = asyncio.run(
        medicine_pool(
            gamedata=FakeGamedata(ap_supplies_payload()),
            depot_counts={"应急理智合剂": 3, "应急理智小样": 2},
            use_medicine=True,
        )
    )

    assert pool.items == [
        MedicineItem(name="应急理智合剂", ap=60, count=3),
        MedicineItem(name="应急理智小样", ap=10, count=2),
    ]
    assert pool.owned_sanity == 200
    assert pool.counted_sanity == 0
    assert pool.scope == "no_expiry_data"
    assert pool.expiring_medicine == 999


def test_medicine_counts_zero_without_expiry_data() -> None:
    payload = ap_supplies_payload()
    on = asyncio.run(
        medicine_pool(
            gamedata=FakeGamedata(payload),
            depot_counts={"应急理智合剂": 3},
            use_medicine=True,
        )
    )
    off = asyncio.run(
        medicine_pool(
            gamedata=FakeGamedata(payload),
            depot_counts={"应急理智合剂": 3},
            use_medicine=False,
        )
    )

    assert on.counted_sanity == 0
    assert on.scope == "no_expiry_data"
    assert on.expiring_medicine == 999
    assert off.counted_sanity == 0
    assert off.expiring_medicine == 0
    assert "没有用药" in off.note


def test_missing_item_table_keeps_result() -> None:
    sources: list[dict[str, Any]] = []

    pool = asyncio.run(
        medicine_pool(
            gamedata=FakeGamedata(error=AppError(ErrorCode.INTERNAL_ERROR, "拆包不可用")),
            depot_counts={"应急理智合剂": 3},
            use_medicine=True,
            sources=sources,
        )
    )

    assert pool.items == []
    assert pool.owned_sanity == 0
    assert pool.expiring_medicine == 0
    assert pool.counted_sanity == 0
    assert "没有读到理智药的恢复量" in pool.note
    assert sources == []


def test_medicine_pool_adds_gamedata_source() -> None:
    sources: list[dict[str, Any]] = []

    asyncio.run(
        medicine_pool(
            gamedata=FakeGamedata(ap_supplies_payload()),
            depot_counts={},
            use_medicine=False,
            sources=sources,
        )
    )

    assert len(sources) == 1
    source = sources[0]
    assert source["kind"] == "gamedata"
    assert source["title"] == "item_table.json"
    assert source["fetched_at"] == NOW.isoformat()
