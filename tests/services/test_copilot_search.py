"""作业站筛选（规格 §9.4）：三档判定、排序、缺口、体积与降级。

用 `tests/fixtures/copilot/` 的真实响应快照当**素材**（把夹具里的行改成用例需要的
练度），再配一份手工的账号干员表——单测不打网、也不碰真实数据库。
"""

from __future__ import annotations

import asyncio
import json
from functools import wraps
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.copilot_site import CopilotRow, CopilotSiteData
from maa_api.services.game.copilots import MAX_LIMIT, MAX_RESULT_BYTES, CopilotService

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "copilot"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wraps(func)(wrapper)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------
class FakeStageIndex:
    """本机关卡索引替身：``1-7`` → ``main_01-07``。"""

    def __init__(self, stage_id: str | None = "main_01-07") -> None:
        self.stage_id = stage_id
        self.queries: list[str] = []

    def find_stage(self, query: str) -> dict[str, Any]:
        self.queries.append(query)
        if self.stage_id is None:
            return {"matches": [], "matched_field": None}
        return {
            "matches": [{"code": "1-7", "name": "暴君", "stage_id": self.stage_id}],
            "matched_field": "code",
        }


class FakeSiteClient:
    """作业站客户端替身：回一页给定的行，并记下收到的查询参数。"""

    def __init__(
        self,
        rows: list[CopilotRow] | None = None,
        *,
        error: AppError | None = None,
        total: int = 0,
    ) -> None:
        self.rows = rows or []
        self.error = error
        self.total = total or len(self.rows)
        self.calls: list[dict[str, Any]] = []

    async def query(self, *, stage: str, order: str = "hot", page: int = 1, limit: int = 5):
        self.calls.append({"stage": stage, "order": order, "page": page, "limit": limit})
        if self.error is not None:
            raise self.error
        return CopilotSiteData(
            endpoint="query",
            url="https://prts.maa.plus/copilot/query",
            fetched_at=datetime(2026, 10, 10, 12, 0),
            cached=False,
            stale=False,
            rows=tuple(self.rows),
            page=page,
            limit=limit,
            total=self.total,
            has_next=False,
            status_code=200,
        )


class FakeBox:
    """账号干员服务替身：只实现 ``list_operators``。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_operators(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.rows is None:
            raise AppError(ErrorCode.ACCOUNT_DATA_NOT_FOUND, "从未识别过")
        return {"items": self.rows, "total": len(self.rows)}


#: 账号干员表：覆盖「满足」「练度不够」「未拥有」「缺模组」四种情况。
ACCOUNT_ROWS: list[dict[str, Any]] = [
    {
        "char_id": "char_336_folivo",
        "name": "稀音",
        "rarity": 5,
        "profession": "SUPPORT",
        "own": True,
        "elite": 1,
        "level": 60,
        "potential": 1,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {},
    },
    {
        "char_id": "char_245_cello",
        "name": "塑心",
        "rarity": 6,
        "profession": "SUPPORT",
        "own": True,
        "elite": 2,
        "level": 50,
        "potential": 1,
        "skill_level": 7,
        "masteries": {"1": 1, "2": 0, "3": 0},
        "modules": {"X": 1},
    },
    {
        "char_id": "char_1012_skadi2",
        "name": "浊心斯卡蒂",
        "rarity": 6,
        "profession": "SUPPORT",
        "own": True,
        "elite": 2,
        "level": 90,
        "potential": 3,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 3, "3": 0},
        "modules": {"Y": 2},
    },
    {
        "char_id": "char_2023_ling",
        "name": "令",
        "rarity": 6,
        "profession": "SUPPORT",
        "own": True,
        "elite": 2,
        "level": 90,
        "potential": 1,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {},
    },
    {
        "char_id": "char_187_ccheal",
        "name": "嘉维尔",
        "rarity": 4,
        "profession": "MEDIC",
        "own": True,
        "elite": 1,
        "level": 40,
        "potential": 1,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {},
    },
    {
        "char_id": "char_212_ansel",
        "name": "安赛尔",
        "rarity": 3,
        "profession": "MEDIC",
        "own": True,
        "elite": 1,
        "level": 40,
        "potential": 1,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {},
    },
]


def content(
    opers: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    *,
    stage: str = "main_01-07",
) -> dict[str, Any]:
    return {
        "version": 3,
        "stage_name": stage,
        "minimum_required": "v6.0.0",
        "doc": {"title": "测试作业"},
        "opers": opers or [],
        "groups": groups or [],
        "actions": [{"type": "SpeedUp"}],
    }


def make_row(site_id: int, payload: dict[str, Any], **overrides: Any) -> CopilotRow:
    fields: dict[str, Any] = {
        "site_id": site_id,
        "uploader": f"作者{site_id}",
        "views": 100,
        "hot_score": 1.0,
        "rating_ratio": 1.0,
        "content": json.dumps(payload, ensure_ascii=False),
    }
    fields.update(overrides)
    return CopilotRow(**fields)


def opers_from_fixture(site_id: int) -> dict[str, Any]:
    """把夹具里某一行作业的 ``content`` 取出来当素材。"""
    listing = fixture("query_main_01-07.json")
    for item in listing["data"]["data"]:
        if item["id"] == site_id:
            return json.loads(item["content"])
    raise AssertionError(f"夹具里没有 {site_id}")


def service(
    rows: list[CopilotRow],
    *,
    box: FakeBox | None = None,
    stage_id: str | None = "main_01-07",
) -> tuple[CopilotService, FakeSiteClient, FakeStageIndex]:
    site = FakeSiteClient(rows)
    index = FakeStageIndex(stage_id)
    return (
        CopilotService(site_client=site, box=box or FakeBox(ACCOUNT_ROWS), stage_index=index),
        site,
        index,
    )


# ---------------------------------------------------------------------------
# 三档判定
# ---------------------------------------------------------------------------
@async_test
async def test_all_owned_and_trained_is_ok() -> None:
    row = make_row(
        1,
        content(
            [
                {
                    "name": "稀音",
                    "skill": 2,
                    "skill_usage": 0,
                    "requirements": {"elite": 1, "level": 45, "skill_level": 7},
                }
            ]
        ),
    )
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7")
    (item,) = result["results"]
    assert item["tier"] == "ok"
    assert item["feasible"] is True
    assert item["missing"] == []
    assert item["missing_count"] == 0
    (operator,) = item["operators"]
    assert operator["name"] == "稀音"
    assert operator["owned"] is True
    assert operator["satisfied"] is True
    assert {check["field"] for check in operator["requirements"]} == {
        "elite",
        "level",
        "skill_level",
    }
    assert result["tiers"] == {"ok": 1, "missing_one": 0, "infeasible": 0}


@async_test
async def test_one_missing_oper_is_missing_one() -> None:
    row = make_row(
        2,
        content(
            [
                {"name": "令", "skill": 3},
                {"name": "夜莺", "skill": 3},  # 账号里没有
            ]
        ),
    )
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7")
    (item,) = result["results"]
    assert item["tier"] == "missing_one"
    assert item["feasible"] is True
    assert item["missing"] == ["夜莺"]
    assert item["note"] and "助战" in item["note"]


@async_test
async def test_two_missing_opers_is_infeasible_and_filtered_out() -> None:
    row = make_row(
        3,
        content(
            [
                {"name": "夜莺"},
                {"name": "白面鸮"},
                {"name": "令"},
            ]
        ),
    )
    svc, _site, _index = service([row])
    filtered = await svc.search(stage="1-7", only_feasible=True)
    assert filtered["results"] == []
    assert filtered["total"] == 0
    assert filtered["candidates"] == 1
    assert filtered["tiers"] == {"ok": 0, "missing_one": 0, "infeasible": 1}

    everything = await svc.search(stage="1-7", only_feasible=False)
    (item,) = everything["results"]
    assert item["tier"] == "infeasible"
    assert item["feasible"] is False
    assert item["missing"] == ["夜莺", "白面鸮"]


@async_test
async def test_trained_but_unowned_oper_counts_as_a_gap() -> None:
    """账号里没有的干员是缺口，哪怕作业对它没写练度要求。"""
    row = make_row(4, content([{"name": "夜莺"}]))
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7", only_feasible=False)
    (item,) = result["results"]
    (operator,) = item["operators"]
    assert operator["owned"] is False
    assert operator["known"] is True
    assert operator["satisfied"] is False
    assert item["tier"] == "missing_one"
    # `known_names` 只在拿得到账号干员表时才参与「是不是拼错了」的判断；这里账号表非空，
    # 因此「夜莺」是「账号里没有」而不是「名字写错了」。
    assert operator["note"] and "账号" in operator["note"]


@async_test
async def test_level_below_requirement_is_a_gap() -> None:
    row = make_row(5, content([{"name": "嘉维尔", "requirements": {"level": 80}}]))
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7", only_feasible=False)
    (item,) = result["results"]
    (operator,) = item["operators"]
    assert operator["owned"] is True
    assert operator["satisfied"] is False
    assert item["missing"] == ["嘉维尔"]
    assert operator["note"] and "level" in operator["note"]


@async_test
async def test_module_requirement_without_any_module_is_a_gap() -> None:
    row = make_row(6, content([{"name": "安赛尔", "requirements": {"module": 2}}]))
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7", only_feasible=False)
    (item,) = result["results"]
    (operator,) = item["operators"]
    assert operator["satisfied"] is False
    (check,) = operator["requirements"]
    assert check["field"] == "module"
    assert check["met"] is False


@async_test
async def test_potentiality_key_is_ignored_but_other_requirements_still_apply() -> None:
    """作业站的 ``potentiality`` 是兼容键：不参与判定，同一 entry 的其它要求照判。"""
    row = make_row(
        7,
        content(
            [
                {
                    "name": "塑心",
                    "skill": 1,
                    "requirements": {
                        "elite": 2,
                        "level": 45,
                        "skill_level": 7,
                        "module": -1,
                        "potentiality": 1,
                    },
                }
            ]
        ),
    )
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7")
    (item,) = result["results"]
    (operator,) = item["operators"]
    fields = {check["field"] for check in operator["requirements"]}
    # `potentiality` 与 `module=-1`（不作要求）都不进判定清单。
    assert fields == {"elite", "level", "skill_level"}
    assert item["tier"] == "ok"

    # 同一 entry 的其它要求仍然生效：把等级提到 90（账号是 50）就该判缺口。
    stricter = make_row(
        70,
        content(
            [
                {
                    "name": "塑心",
                    "skill": 1,
                    "requirements": {"level": 90, "potentiality": 1},
                }
            ]
        ),
    )
    svc2, _site2, _index2 = service([stricter])
    result2 = await svc2.search(stage="1-7", only_feasible=False)
    (item2,) = result2["results"]
    assert item2["tier"] == "missing_one"
    assert item2["missing"] == ["塑心"]


@async_test
async def test_skill_level_uses_mastery_of_the_named_skill() -> None:
    """``skill_level=8``（专精一）要看作业指定的那个技能的专精，不是最大的那个。"""
    row = make_row(8, content([{"name": "塑心", "skill": 1, "requirements": {"skill_level": 8}}]))
    svc, _site, _index = service([row])
    ok = await svc.search(stage="1-7")
    (item,) = ok["results"]
    assert item["tier"] == "ok"  # 第 1 个技能专精一

    row2 = make_row(9, content([{"name": "塑心", "skill": 3, "requirements": {"skill_level": 8}}]))
    svc2, _site2, _index2 = service([row2])
    bad = await svc2.search(stage="1-7", only_feasible=False)
    (item2,) = bad["results"]
    assert item2["tier"] == "missing_one"  # 第 3 个技能没有专精


@async_test
async def test_group_is_satisfied_when_any_member_matches() -> None:
    row = make_row(
        10,
        content(
            groups=[
                {
                    "name": "任意奶",
                    "opers": [
                        {"name": "夜莺", "skill": 3},
                        {"name": "嘉维尔", "skill": 1, "requirements": {"elite": 1, "level": 40}},
                    ],
                }
            ]
        ),
    )
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7")
    (item,) = result["results"]
    assert item["tier"] == "ok"
    (operator,) = item["operators"]
    assert operator["kind"] == "group"
    assert operator["satisfied"] is True
    assert operator["members"] == ["夜莺", "嘉维尔"]


@async_test
async def test_group_fails_when_no_member_matches() -> None:
    row = make_row(
        11,
        content(
            groups=[
                {
                    "name": "任意奶",
                    "opers": [
                        {"name": "夜莺"},
                        {"name": "嘉维尔", "requirements": {"elite": 2}},
                    ],
                }
            ]
        ),
    )
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7", only_feasible=False)
    (item,) = result["results"]
    assert item["tier"] == "missing_one"
    assert item["missing"] == ["任意奶"]


# ---------------------------------------------------------------------------
# 排序与查询
# ---------------------------------------------------------------------------
@async_test
async def test_sort_is_gaps_then_rating_then_hot() -> None:
    ok = make_row(
        87576,
        content(
            [
                {"name": "令", "requirements": {"elite": 2}},
                {"name": "塑心", "requirements": {"elite": 2}},
            ]
        ),
        rating_ratio=1.0,
        hot_score=5.3,
    )
    one_gap = make_row(
        101217,
        content([{"name": "予愿安洁莉娜"}]),
        rating_ratio=1.0,
        hot_score=5.39,
    )
    one_gap_hotter = make_row(
        22401,
        content([{"name": "夜莺"}]),
        rating_ratio=1.0,
        hot_score=2.0,
        views=10,
    )
    two_gaps = make_row(
        102455,
        content([{"name": "夜莺"}, {"name": "白面鸮"}]),
        rating_ratio=0.5,
        hot_score=3.8,
    )
    svc, _site, _index = service([two_gaps, one_gap, ok, one_gap_hotter])
    result = await svc.search(stage="1-7", only_feasible=False)
    assert [item["site_id"] for item in result["results"]] == [
        87576,
        101217,
        22401,
        102455,
    ]
    assert [item["missing_count"] for item in result["results"]] == [0, 1, 1, 2]


@async_test
async def test_rating_ratio_breaks_ties_before_hot_score() -> None:
    higher = make_row(1, content([{"name": "夜莺"}]), rating_ratio=1.0, hot_score=1.0)
    lower = make_row(2, content([{"name": "夜莺"}]), rating_ratio=0.5, hot_score=9.0)
    svc, _site, _index = service([lower, higher])
    result = await svc.search(stage="1-7")
    assert [item["site_id"] for item in result["results"]] == [1, 2]


@async_test
async def test_stage_is_resolved_to_stage_id_before_the_request() -> None:
    svc, site, index = service([])
    await svc.search(stage="1-7", limit=5)
    assert index.queries == ["1-7"]
    assert site.calls[0]["stage"] == "main_01-07"
    assert site.calls[0]["order"] == "hot"
    # only_feasible=true 时候选要多取一些（过滤掉不可行的还得凑够 limit）。
    assert site.calls[0]["limit"] == 20


@async_test
async def test_stage_falls_back_to_the_raw_query_when_the_index_misses() -> None:
    svc, site, _index = service([], stage_id=None)
    await svc.search(stage="main_01-07")
    assert site.calls[0]["stage"] == "main_01-07"


@async_test
async def test_limit_is_capped_at_ten() -> None:
    svc, _site, _index = service([])
    with pytest.raises(AppError) as info:
        await svc.search(stage="1-7", limit=MAX_LIMIT + 1)
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_results_carry_no_actions_and_a_link() -> None:
    row = make_row(
        22311,
        opers_from_fixture(22311),
        uploader="Genicur",
        views=833,
        rating_ratio=0.9,
        hot_score=13.15,
    )
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7")
    (item,) = result["results"]
    assert item["site_id"] == 22311
    assert item["title"] == "1-7 稀音 单人 61s 速刷 信赖 无专"
    assert item["uploader"] == "Genicur"
    assert item["views"] == 833
    assert item["rating_ratio"] == 0.9
    assert item["link"] == "https://prts.maa.plus/copilot/22311"
    assert "actions" not in item["content"]
    assert item["content"]["stage_name"] == "main_01-07"
    assert "save_copilot_draft" in (result["hint"] or "")
    assert result["untrusted"] is True
    assert result["sources"][0]["kind"] == "copilot_site"


@async_test
async def test_rows_without_account_data_are_reported_not_claimed_feasible() -> None:
    row = make_row(1, content([{"name": "令"}]))
    svc, _site, _index = service([row], box=FakeBox(None))
    result = await svc.search(stage="1-7", only_feasible=False)
    assert result["unavailable"] == ["account_box"]
    assert result["note"] and "账号" in result["note"]
    (item,) = result["results"]
    assert item["tier"] == "missing_one"
    (operator,) = item["operators"]
    assert operator["owned"] is False
    assert operator["known"] is True
    assert operator["note"] and "无法判断" in operator["note"]


@async_test
async def test_unparseable_content_is_infeasible_with_a_note() -> None:
    row = CopilotRow(site_id=42, content="{not json", uploader="x")
    svc, _site, _index = service([row])
    result = await svc.search(stage="1-7", only_feasible=False)
    (item,) = result["results"]
    assert item["tier"] == "infeasible"
    assert item["content"] is None
    assert item["note"] and "JSON" in item["note"]


# ---------------------------------------------------------------------------
# 降级与体积
# ---------------------------------------------------------------------------
@async_test
async def test_site_unavailable_degrades_without_raising() -> None:
    site = FakeSiteClient(
        error=AppError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "作业站不可用（测试）")
    )
    svc = CopilotService(site_client=site, box=FakeBox(ACCOUNT_ROWS), stage_index=FakeStageIndex())
    result = await svc.search(stage="1-7")
    assert result["results"] == []
    assert result["total"] == 0
    assert result["unavailable"] == ["copilot_site"]
    assert "EXTERNAL_SOURCE_UNAVAILABLE" in (result["note"] or "")


@async_test
async def test_rate_limited_is_also_degraded() -> None:
    site = FakeSiteClient(error=AppError(ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED, "限流"))
    svc = CopilotService(site_client=site, box=FakeBox(ACCOUNT_ROWS), stage_index=FakeStageIndex())
    result = await svc.search(stage="1-7")
    assert result["unavailable"] == ["copilot_site"]


@async_test
async def test_invalid_limit_is_not_degraded() -> None:
    site = FakeSiteClient(error=AppError(ErrorCode.INVALID_PARAMETER, "参数错"))
    svc = CopilotService(site_client=site, box=FakeBox(ACCOUNT_ROWS), stage_index=FakeStageIndex())
    with pytest.raises(AppError) as info:
        await svc.search(stage="1-7")
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_result_stays_within_the_tool_budget() -> None:
    """十条「全都有」的大作业也不能超过 24 KB（规格 §4.4）。"""
    opers = [
        {
            "name": name,
            "skill": 3,
            "skill_usage": 1,
            "requirements": {
                "elite": 2,
                "level": 90,
                "skill_level": 7,
                "module": -1,
                "potentiality": 1,
            },
        }
        for name in ("令", "塑心", "浊心斯卡蒂", "稀音", "嘉维尔", "安赛尔")
    ]
    rows = [make_row(1000 + index, content(opers), views=1000 + index) for index in range(10)]
    svc, _site, _index = service(rows)
    result = await svc.search(stage="1-7", limit=MAX_LIMIT)
    assert len(result["results"]) <= MAX_LIMIT
    payload = json.dumps(result, ensure_ascii=False)
    assert len(payload.encode("utf-8")) <= MAX_RESULT_BYTES
