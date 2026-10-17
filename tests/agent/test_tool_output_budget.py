"""M56 工具输出预算与作业搜索的模型侧形状（规格 §4.3）。

两条实机教训（2026-10-17 会话）：

- ``get_operator_box(limit=100, ...)`` 一次回 64 598 字（约 26 000 token），模型只
  需要「有没有、练度够不够」；
- ``search_copilots`` 每条结果带 1–2 KB 作业正文，10 条就上万字，而执行只需要
  ``site_id``；另外本机索引解析不出新关卡时它拿原文去站点模糊搜，返回的是别的关，
  模型会误以为「作业站数据有问题」甚至去更新资源。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.account import OPERATOR_BOX_BUDGET_CHARS
from maa_api.domain.enums import CallerType
from maa_api.services.external.copilot_site import CopilotRow, CopilotSiteData
from maa_api.services.game.copilots import CopilotService


def context(caller: CallerType, **state: Any) -> ToolContext:
    return ToolContext(
        caller,
        "session-1",
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def call(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    definition = build_registry().get(name)

    async def scenario():
        return await definition.handler(
            definition.params_model.model_validate(arguments), ctx
        )

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# get_operator_box
# ---------------------------------------------------------------------------
def _verbose_box_rows(count: int) -> list[dict[str, Any]]:
    return [
        {
            "char_id": f"char_{index:03d}",
            "name": f"干员{index}",
            "rarity": 6,
            "profession": "近卫",
            "own": True,
            "elite": 2,
            "level": 90,
            "potential": 3,
            "skill_level": 7,
            "masteries": {"1": 3, "2": 0, "3": 0},
            "modules": {"X": 2},
            "favor_percent": 200,
            # 下面这些是卡片资源明细，列表不需要；一条就占约 400 字。
            "skill_ids": [f"skchr_{index}_1", f"skchr_{index}_2", f"skchr_{index}_3"],
            "module_details": [
                {"type": "X", "level": 2, "name": f"模组{index}", "unlock": True}
            ],
            "field_sources": {
                "elite": "skland",
                "level": "skland",
                "masteries": "skland",
                "modules": "skland",
            },
            "recognized_at": "2026-10-16T12:00:00Z",
            "updated_at": "2026-10-16T12:00:00Z",
        }
        for index in range(count)
    ]


class FakeBox:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_operators(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        offset = int(kwargs.get("offset") or 0)
        limit = int(kwargs.get("limit") or 50)
        page = self.rows[offset : offset + limit]
        return {
            "items": [dict(row) for row in page],
            "total": len(self.rows),
            "limit": limit,
            "offset": offset,
        }


def test_operator_box_keeps_the_full_wire_for_rest_but_compacts_for_the_agent():
    box = FakeBox(_verbose_box_rows(120))

    internal = call(
        "get_operator_box",
        {"limit": 50, "offset": 0},
        context(CallerType.INTERNAL, box_service=box),
    )
    assert internal["total"] == 120
    assert internal["next_offset"] == 50
    first = internal["items"][0]
    assert first["name"] == "干员0"
    assert first["masteries"] == {"1": 3, "2": 0, "3": 0}
    # 卡片资源明细不进列表：这是体积的主要来源。
    assert "skill_ids" not in first
    assert "module_details" not in first
    assert "field_sources" not in first
    assert len(json.dumps(internal, ensure_ascii=False)) <= OPERATOR_BOX_BUDGET_CHARS

    rest = call(
        "get_operator_box",
        {"limit": 50, "offset": 0},
        context(CallerType.REST, box_service=box),
    )
    assert rest["items"][0]["skill_ids"] == ["skchr_0_1", "skchr_0_2", "skchr_0_3"]
    assert "field_sources" in rest["items"][0]


def test_operator_box_budget_trims_rows_and_says_how_to_continue():
    box = FakeBox(_verbose_box_rows(100))

    result = call(
        "get_operator_box",
        {"limit": 100},
        context(CallerType.INTERNAL, box_service=box),
    )
    assert result["truncated"]["returned"] == len(result["items"])
    assert len(result["items"]) < 100
    assert result["next_offset"] == len(result["items"])
    assert len(json.dumps(result, ensure_ascii=False)) <= OPERATOR_BOX_BUDGET_CHARS


# ---------------------------------------------------------------------------
# search_copilots
# ---------------------------------------------------------------------------
def _copilot_rows() -> tuple[CopilotRow, ...]:
    rows = []
    for site_id in (22311, 22312):
        content = {
            "stage_name": "main_01-07",
            "minimum_required": "v4.0",
            "doc": {"title": f"作业 {site_id}", "details": "作者备注" * 40},
            "opers": [{"name": "能天使", "skill": 3}],
            "groups": [],
            "actions": [{"type": "Deploy", "name": "能天使"}],
        }
        rows.append(
            CopilotRow(
                site_id=site_id,
                uploader="tester",
                views=10,
                rating_ratio=1.0,
                hot_score=1.0,
                content=json.dumps(content, ensure_ascii=False),
            )
        )
    return tuple(rows)


class Site:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    async def query(self, **kwargs: Any) -> CopilotSiteData:
        self.queries.append(kwargs)
        return CopilotSiteData(
            endpoint="query",
            url="https://prts.maa.plus/copilot/query",
            fetched_at=datetime(2026, 10, 17, 12, 0),
            cached=False,
            stale=False,
            rows=_copilot_rows(),
            total=2,
            has_next=False,
            status_code=200,
        )


class BoxWithOperators:
    async def list_operators(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": [{"name": "能天使", "char_id": "char_103_angel"}], "total": 1}


class KnownIndex:
    def find_stage(self, query: str) -> dict[str, Any]:
        return {
            "matches": [{"stage_id": "main_01-07", "code": "1-7"}],
            "matched_field": "code",
        }


class UnknownIndex:
    def find_stage(self, query: str) -> dict[str, Any]:
        return {"matches": [], "matched_field": None}


def _service(*, index: Any) -> CopilotService:
    return CopilotService(site_client=Site(), box=BoxWithOperators(), stage_index=index)


def test_search_copilots_omits_content_by_default_for_the_agent():
    service = _service(index=KnownIndex())
    result = call(
        "search_copilots", {"stage": "1-7"}, context(CallerType.INTERNAL, copilot_service=service)
    )
    assert result["query"]["include_content"] is False
    assert result["results"]
    assert all("content" not in item for item in result["results"])
    assert "不含作业正文" in result["hint"]
    # 正文之外的筛选信息还在：模型靠这些判断要不要存草稿。
    assert result["results"][0]["stage_name"] == "main_01-07"
    assert result["results"][0]["operators"]

    rest = call(
        "search_copilots", {"stage": "1-7"}, context(CallerType.REST, copilot_service=service)
    )
    assert rest["query"]["include_content"] is True
    assert all("content" in item for item in rest["results"])


def test_search_copilots_warns_when_local_stage_index_cannot_resolve_the_stage():
    """新活动关解析不出 stageId 时别再让模型以为「作业站数据有问题」。"""

    service = _service(index=UnknownIndex())
    result = call(
        "search_copilots",
        {"stage": "act52side_ex08"},
        context(CallerType.INTERNAL, copilot_service=service),
    )
    assert result["query"]["stage_id"] is None
    assert "可能不是同一关" in result["note"]
    assert "不需要更新资源" in result["note"]
    assert "save_copilot_draft" in result["hint"]
