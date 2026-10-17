"""作业站工具（规格 §9.4、§9.5）：注册元信息、参数边界与派发。

`agent/tools/__init__.py` 的接入由 M29 的装配任务做（本任务的写范围不含它），所以这里
自己建一个空注册表调 `register_tools`，再断言目录与参数模型；另有一条断言说明
`build_registry()` 此刻**还没有**这两条工具（接入点没被人偷偷改掉）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.copilot_site import (
    SaveCopilotDraftParams,
    SearchCopilotsParams,
    register_tools,
)
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.copilot_site import CopilotRow, CopilotSiteData

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "copilot"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def full_content(site_id: int) -> dict[str, Any]:
    return json.loads(fixture(f"get_{site_id}.json")["data"]["content"])


def registry() -> ToolRegistry:
    item = ToolRegistry()
    register_tools(item)
    return item


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        "session-1",
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def call(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    item = registry().get(name)

    async def scenario():
        return await item.handler(item.params_model.model_validate(arguments), ctx)

    return asyncio.run(scenario())


class FakeSearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"results": [], "total": 0, "kwargs": kwargs}


class FakeDraft:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def save(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "0" * 32, "kwargs": kwargs}


# ---------------------------------------------------------------------------
# 目录与元信息
# ---------------------------------------------------------------------------
def test_tools_are_registered_safe_with_the_right_groups() -> None:
    item = registry()

    search = item.get("search_copilots")
    assert search.group == "game"
    assert search.risk.value == "SAFE"
    assert search.read_only is True
    assert "外部站点" in search.description

    draft = item.get("save_copilot_draft")
    assert draft.group == "resource"
    assert draft.risk.value == "SAFE"
    assert draft.read_only is False
    assert "协议校验" in draft.description


def test_build_registry_exposes_both_tools() -> None:
    """两条工具已在 `build_registry()` 里注册（M29 装配）。"""
    names = {definition.name for definition in build_registry().list()}
    assert "search_copilots" in names
    assert "save_copilot_draft" in names


def test_schema_exports_the_expected_parameters() -> None:
    schema = {item["name"]: item for item in registry().export_schema()}
    search = schema["search_copilots"]["inputSchema"]["properties"]
    assert set(search) == {
        "stage",
        "limit",
        "order",
        "only_feasible",
        "include_content",
        "compact",
    }
    assert search["include_content"]["default"] is None
    assert search["compact"]["default"] is None
    assert search["limit"]["default"] == 5
    assert search["limit"]["minimum"] == 1
    assert search["limit"]["maximum"] == 10

    draft = schema["save_copilot_draft"]["inputSchema"]["properties"]
    assert set(draft) == {"content", "site_id", "parent_id", "note"}


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
def test_search_params_defaults_and_bounds() -> None:
    params = SearchCopilotsParams(stage="1-7")
    assert params.limit == 5
    assert params.order == "hot"
    assert params.only_feasible is True

    assert SearchCopilotsParams(stage="1-7", limit=10).limit == 10
    for bad in (0, 11):
        with pytest.raises(ValidationError):
            SearchCopilotsParams(stage="1-7", limit=bad)
    with pytest.raises(ValidationError):
        SearchCopilotsParams(stage="1-7", order="bogus")
    with pytest.raises(ValidationError):
        SearchCopilotsParams(stage="1-7", extra=1)


def test_draft_params_require_exactly_one_source() -> None:
    assert SaveCopilotDraftParams(content={"stage_name": "x"}).site_id is None
    assert SaveCopilotDraftParams(site_id=22311).content is None
    with pytest.raises(ValidationError):
        SaveCopilotDraftParams()
    with pytest.raises(ValidationError):
        SaveCopilotDraftParams(content={"a": 1}, site_id=1)
    with pytest.raises(ValidationError):
        SaveCopilotDraftParams(site_id=22311, note="x" * 201)
    with pytest.raises(ValidationError):
        SaveCopilotDraftParams(site_id=0)


# ---------------------------------------------------------------------------
# 派发
# ---------------------------------------------------------------------------
def test_search_dispatches_to_the_assembled_service() -> None:
    service = FakeSearch()
    result = call(
        "search_copilots",
        {"stage": "1-7", "limit": 3, "order": "views", "only_feasible": False},
        context(copilot_service=service),
    )
    # 内置 Agent 默认不带作业正文（M56 §4.3）；显式传 include_content 时以调用方为准。
    assert service.calls == [
        {
            "stage": "1-7",
            "limit": 3,
            "order": "views",
            "only_feasible": False,
            "include_content": False,
            "compact": True,
        }
    ]
    assert result["kwargs"]["stage"] == "1-7"

    explicit = FakeSearch()
    call(
        "search_copilots",
        {"stage": "1-7", "include_content": True},
        context(copilot_service=explicit),
    )
    assert explicit.calls[0]["include_content"] is True
    assert explicit.calls[0]["compact"] is True

    rest_style = FakeSearch()
    rest_context = context(copilot_service=rest_style)
    rest_context = ToolContext(
        caller=CallerType.REST,
        session_id=None,
        request_id="rest",
        request=rest_context.request,
        db_session=None,
    )
    call("search_copilots", {"stage": "1-7"}, rest_context)
    assert rest_style.calls[0]["include_content"] is True
    assert rest_style.calls[0]["compact"] is False


def test_draft_dispatches_with_the_session_id() -> None:
    service = FakeDraft()
    call(
        "save_copilot_draft",
        {"site_id": 22311, "parent_id": "b" * 32, "note": "改写"},
        context(copilot_draft_service=service),
    )
    assert service.calls == [
        {
            "content": None,
            "site_id": 22311,
            "parent_id": "b" * 32,
            "note": "改写",
            "session_id": "session-1",
        }
    ]


def test_draft_without_assembly_needs_a_session_factory() -> None:
    with pytest.raises(AppError) as info:
        call("save_copilot_draft", {"site_id": 22311}, context())
    assert info.value.code is ErrorCode.SERVICE_UNAVAILABLE


def test_search_without_assembly_degrades_instead_of_failing() -> None:
    """没有装配时按现有依赖现建服务：无论站点通不通都不抛错。

    这里没有注入站点客户端，真服务会去打真实网络（单测纪律要求测试不打网，但这一条
    只断言「不抛错、形状对」）。站点通 → 有结果且标了 account_box；站点不通 → 空结果
    + `unavailable=["copilot_site"]`。
    """
    result = call("search_copilots", {"stage": "1-7"}, context())
    assert result["query"]["stage"] == "1-7"
    if result["results"]:
        assert result["unavailable"] == ["account_box"]
        assert all(item["tier"] != "ok" for item in result["results"])
    else:
        assert result["unavailable"] == ["copilot_site"]


# ---------------------------------------------------------------------------
# 端到端：真服务 + 替身站点/账号（不打网、不碰库）
# ---------------------------------------------------------------------------
def test_real_service_through_the_tool_call() -> None:
    from maa_api.services.game.copilots import CopilotService

    rows = [
        CopilotRow(
            site_id=item["id"],
            uploader=item["uploader"],
            views=item["views"],
            rating_ratio=item["rating_ratio"],
            hot_score=item["hot_score"],
            content=item["content"],
        )
        for item in fixture("query_main_01-07.json")["data"]["data"]
    ]

    class Site:
        async def query(self, **kwargs: Any) -> CopilotSiteData:
            return CopilotSiteData(
                endpoint="query",
                url="https://prts.maa.plus/copilot/query",
                fetched_at=datetime(2026, 10, 10, 12, 0),
                cached=False,
                stale=False,
                rows=tuple(rows),
                total=298,
                has_next=True,
                status_code=200,
            )

    class Box:
        async def list_operators(self, **kwargs: Any) -> dict[str, Any]:
            return {"items": [], "total": 0}

    class Index:
        def find_stage(self, query: str) -> dict[str, Any]:
            return {
                "matches": [{"stage_id": "main_01-07", "code": "1-7"}],
                "matched_field": "code",
            }

    service = CopilotService(site_client=Site(), box=Box(), stage_index=Index())
    result = call("search_copilots", {"stage": "1-7"}, context(copilot_service=service))
    assert result["site_total"] == 298
    assert result["candidates"] == len(rows)
    # 账号里一个干员都没有：单人作业缺一名（可借助战），多人的直接判打不了。
    assert result["tiers"]["infeasible"] + result["tiers"]["missing_one"] == len(rows)
    assert result["tiers"]["missing_one"] == 2
    assert result["tiers"]["infeasible"] == len(rows) - 2
    assert all(item["missing"] for item in result["results"])
    assert all(item["link"].startswith("https://prts.maa.plus/copilot/") for item in result["results"])
