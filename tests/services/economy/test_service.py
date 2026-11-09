"""M88 T4：刷图方案服务的编排、结果形状、确定性与报告产物（规格 §4.5.1–§4.5.6）。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from maa_api.services.account import planner
from maa_api.services.account.planner import FarmingInputs, StageOption
from maa_api.services.economy.model import FarmingRequest
from maa_api.services.economy.service import FarmingOptimizer
from maa_api.services.game.activities import ActivityReport

from tests.services.economy.test_data import (
    NOW,
    WORD,
    FakeActivities,
    FakeDepot,
    FakeGamedata,
    FakePage,
    FakePlanner,
    FakePrts,
    activity,
    ap_supplies_payload,
    stage_entries,
    wikitext,
)


def service_inputs(*, count: int = 10, ap: int = 30) -> FarmingInputs:
    return FarmingInputs(
        needs={WORD: count},
        display={WORD: WORD},
        options=[
            StageOption(
                stage_id="main_01-07",
                stage_code="1-7",
                ap_cost=ap,
                rates={WORD: 1.0},
                details=[{"item_name": WORD, "sanity_per_item": float(ap)}],
            )
        ],
        recipes=[],
        rate_status={},
        notes=[],
        server="CN",
    )


def empty_inputs() -> FarmingInputs:
    return FarmingInputs(
        needs={},
        display={},
        options=[],
        recipes=[],
        rate_status={},
        notes=["没有剩余需求：不需要刷关"],
        server="CN",
    )


SHOP_ITEM = "聚酸酯组"


def shop_service_inputs() -> FarmingInputs:
    """活动商店更划算的场景：1-7 单刷 30 理智/件，商店 8 代币/件。"""
    return FarmingInputs(
        needs={SHOP_ITEM: 15},
        display={SHOP_ITEM: SHOP_ITEM},
        options=[
            StageOption(
                stage_id="main_01-07",
                stage_code="1-7",
                ap_cost=30,
                rates={SHOP_ITEM: 1.0},
                details=[{"item_name": SHOP_ITEM, "sanity_per_item": 30.0}],
            )
        ],
        recipes=[],
        rate_status={},
        notes=[],
        server="CN",
    )


class FakePlans:
    def __init__(self, needs: Mapping[str, int] | None = None) -> None:
        self.needs = dict(needs or {})
        self.calls: list[str] = []

    async def missing_needs(self, plan_id: str) -> dict[str, int]:
        self.calls.append(plan_id)
        return dict(self.needs)


class FakeArtifacts:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def put(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id="artifact-1")


class FakeOptimizerDeps:
    """把 ``FarmingOptimizer`` 的外部依赖全部换成固定假对象。"""

    def __init__(
        self,
        *,
        inputs: FarmingInputs | None = None,
        plan_needs: Mapping[str, int] | None = None,
        activities: Any | None = None,
        prts: Any | None = None,
        gamedata: Any | None = None,
        depot: Any | None = None,
        artifacts: Any | None = None,
    ) -> None:
        self.planner = FakePlanner(inputs if inputs is not None else service_inputs())
        self.plans = FakePlans(plan_needs)
        self.activities = activities or FakeActivities(ActivityReport(activities=()))
        self.prts = prts or FakePrts()
        self.gamedata = gamedata if gamedata is not None else FakeGamedata(ap_supplies_payload())
        self.depot = depot if depot is not None else FakeDepot()
        self.artifacts = artifacts

    def build(self) -> FarmingOptimizer:
        return FarmingOptimizer(
            planner=self.planner,
            depot=self.depot,
            plans=self.plans,
            activities=self.activities,
            prts=self.prts,
            gamedata=self.gamedata,
            artifacts=self.artifacts,
            now=lambda: NOW,
        )


def farming_request(
    *,
    materials: Mapping[str, int] | None = None,
    plan_id: str | None = None,
    use_depot: bool = True,
    current_sanity: int = 0,
    window_days: float | None = None,
    use_medicine: bool = False,
    include_events: bool = True,
    include_unnavigable: bool = False,
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
        owned_tokens={},
    )


def run(service: FarmingOptimizer, request: FarmingRequest, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("write_report", False)
    kwargs.setdefault("session_id", None)
    kwargs.setdefault("ticket_id", None)
    return asyncio.run(service.optimize(request, **kwargs))


def appendix_activities() -> FakeActivities:
    report = ActivityReport(activities=(activity(stages=stage_entries()),))
    return FakeActivities(report)


def appendix_prts() -> FakePrts:
    page = FakePage(title="昨日海", content=wikitext("activity_zuori_shop.wikitext"))
    return FakePrts({"昨日海": page})


def test_complete_within_window() -> None:
    result = run(
        FakeOptimizerDeps().build(),
        farming_request(materials={WORD: 10}, window_days=10),
    )

    assert result["status"] == "complete"
    assert result["fits_window"] is True
    assert result["days_estimated"] == result["days_estimated_full"]


def test_partial_when_window_too_short() -> None:
    result = run(
        FakeOptimizerDeps(inputs=service_inputs(count=80)).build(),
        farming_request(materials={WORD: 80}, window_days=2),
    )

    assert result["status"] == "partial"
    assert result["fits_window"] is False
    assert result["days_estimated_full"] > 2 >= result["days_estimated"]


def test_plan_id_uses_missing_needs_without_depot() -> None:
    deps = FakeOptimizerDeps(
        inputs=service_inputs(count=10),
        plan_needs={WORD: 10},
        depot=FakeDepot([{"item_id": "30013", "name": WORD, "count": 999}]),
    )

    result = run(deps.build(), farming_request(plan_id="p1"))

    assert result["requirements"] == {WORD: 10}
    assert result["sanity"]["needed"] == 300
    assert deps.plans.calls == ["p1"]


def test_plan_complete_returns_empty_solution() -> None:
    deps = FakeOptimizerDeps(inputs=empty_inputs(), plan_needs={})

    result = run(
        deps.build(),
        farming_request(plan_id="p1", include_events=False),
    )

    assert result["status"] == "complete"
    assert result["stages"] == []
    assert result["shop"] == []
    assert result["explanation"] == "没有剩余需求：不需要刷关"
    assert any("没有剩余需求" in note for note in result["notes"])


def test_use_depot_false_keeps_depot_empty_but_reads_medicine() -> None:
    deps = FakeOptimizerDeps(
        inputs=service_inputs(count=20),
        depot=FakeDepot(
            [
                {"item_id": "30013", "name": WORD, "count": 10},
                {"item_id": "ap_supply_lt_60", "name": "应急理智合剂", "count": 3},
            ]
        ),
    )

    result = run(
        deps.build(),
        farming_request(materials={WORD: 20}, use_depot=False),
    )

    assert any("use_depot=False：没有用仓库库存抵扣" in note for note in result["notes"])
    assert result["requirements"] == {WORD: 20}
    assert result["sanity"]["needed"] == 600
    assert result["medicine"]["items"] == [
        {"name": "应急理智合剂", "ap": 60, "count": 3}
    ]


def test_medicine_note_and_expiring_value() -> None:
    depot = FakeDepot(
        [{"item_id": "ap_supply_lt_60", "name": "应急理智合剂", "count": 3}]
    )

    on = run(
        FakeOptimizerDeps(depot=depot).build(),
        farming_request(materials={WORD: 10}, use_medicine=True),
    )
    off = run(
        FakeOptimizerDeps(depot=depot).build(),
        farming_request(materials={WORD: 10}, use_medicine=False),
    )

    assert on["medicine"]["expiring_medicine"] == 999
    assert "expiring_medicine 设为 999" in on["medicine"]["note"]
    assert off["medicine"]["expiring_medicine"] == 0
    assert "没有用药" in off["medicine"]["note"]


def test_result_is_deterministic() -> None:
    service = FakeOptimizerDeps().build()
    request = farming_request(materials={WORD: 10}, include_events=False)

    first = run(service, request)
    second = run(service, request)

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


def test_result_is_deterministic_with_events_shop_and_tokens() -> None:
    service = FakeOptimizerDeps(
        inputs=shop_service_inputs(),
        activities=appendix_activities(),
        prts=appendix_prts(),
    ).build()
    request = farming_request(materials={SHOP_ITEM: 15})

    first = run(service, request)
    second = run(service, request)

    assert any(row["event"] for row in first["stages"])
    assert first["shop"]
    assert first["tokens"]
    assert set(first) == set(second)
    for field in first:
        assert first[field] == second[field], field


def test_solver_failure_explanation_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs: Any) -> None:
        raise RuntimeError("scipy 不在")

    monkeypatch.setattr(planner, "_linprog_call", boom)

    result = run(FakeOptimizerDeps().build(), farming_request(materials={WORD: 10}))

    assert result["status"] == "unobtainable"
    assert result["explanation"] == (
        "求解不可用，无法给完整方案：线性规划不可用（RuntimeError: scipy 不在）。"
    )
    assert "0 理智" not in result["explanation"]
    assert any("线性规划不可用" in note for note in result["notes"])


def test_include_events_false_skips_events() -> None:
    activities = appendix_activities()
    deps = FakeOptimizerDeps(activities=activities, prts=appendix_prts())

    result = run(
        deps.build(),
        farming_request(materials={WORD: 10}, include_events=False),
    )

    assert activities.calls == []
    assert "prts" not in {source["kind"] for source in result["sources"]}


def test_sources_include_penguin_and_gamedata() -> None:
    result = run(
        FakeOptimizerDeps().build(),
        farming_request(materials={WORD: 10}, include_events=False),
    )

    kinds = {source["kind"] for source in result["sources"]}
    assert kinds == {"penguin", "gamedata"}
    gamedata = next(source for source in result["sources"] if source["kind"] == "gamedata")
    assert gamedata["title"] == "item_table.json"
    assert gamedata["fetched_at"] == NOW.isoformat()


def test_result_shape_covers_sources_and_medicine() -> None:
    deps = FakeOptimizerDeps(activities=appendix_activities(), prts=appendix_prts())

    result = run(deps.build(), farming_request(materials={WORD: 10}))

    assert set(result["coverage"]) == set(result["requirements"])
    assert {"penguin", "prts", "gamedata"} <= {
        source["kind"] for source in result["sources"]
    }
    assert result["sanity"]["medicine"] == 0
    assert result["medicine"]["counted_sanity"] == 0
    assert result["medicine"]["scope"] == "no_expiry_data"


def test_report_failure_keeps_result() -> None:
    artifacts = FakeArtifacts(error=RuntimeError("磁盘满了"))

    result = run(
        FakeOptimizerDeps(artifacts=artifacts).build(),
        farming_request(materials={WORD: 10}),
        write_report=True,
        session_id="s-1",
        ticket_id="t-1",
    )

    assert result["report_artifact_id"] is None
    assert any("报告没有保存成功" in note for note in result["notes"])


def test_report_meta_is_farming() -> None:
    artifacts = FakeArtifacts()

    result = run(
        FakeOptimizerDeps(artifacts=artifacts).build(),
        farming_request(materials={WORD: 10}),
        write_report=True,
        session_id="s-1",
        ticket_id="t-1",
    )

    assert len(artifacts.calls) == 1
    call = artifacts.calls[0]
    assert call["kind"] == "report"
    assert call["mime"] == "text/markdown"
    assert isinstance(call["body"], bytes)
    assert call["meta"] == {"farming": True}
    assert call["session_id"] == "s-1"
    assert call["ticket_id"] == "t-1"
    assert result["report_artifact_id"] == "artifact-1"
