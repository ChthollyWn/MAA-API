"""M76 T8：``search_knowledge`` 工具（规格 §4.2、§6 的三条用例）。

工具本身只做投影与信封：检索交给 ``app.state.retriever``，这里用假检索器钉住
调用的参数、返回字段、固定说明与不可信来源信封。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from maa_api.agent.knowledge.index.retriever import RetrievalHit, RetrievalResult
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.knowledge import SEARCH_NOTE
from maa_api.domain.enums import CallerType

FETCHED_AT = datetime(2026, 11, 2, 8, 0, 0)


def _hit(**overrides: Any) -> RetrievalHit:
    values: dict[str, Any] = {
        "chunk_id": 1,
        "document_id": "doc-1",
        "source_id": "builtin",
        "source_title": "内置知识",
        "trusted": True,
        "title": "固源岩",
        "heading": "刷取",
        "url": None,
        "version": "v1",
        "fetched_at": FETCHED_AT,
        "license": None,
        "snippet": "以 1-7 刷固源岩",
        "text": "以 1-7 刷固源岩为推荐做法",
        "score": 0.5,
        "fts_rank": 1,
        "vector_rank": None,
    }
    values.update(overrides)
    return RetrievalHit(**values)


class _Retriever:
    """记录调用参数的假检索器；结果由用例给定。"""

    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        top_k: int | None = None,
        caller: str = "retrieval_api",
    ) -> RetrievalResult:
        self.calls.append(
            {"query": query, "sources": sources, "top_k": top_k, "caller": caller}
        )
        return self.result


def _result(*hits: RetrievalHit) -> RetrievalResult:
    return RetrievalResult(
        query="怎么刷固源岩",
        top_k=8,
        enabled=True,
        vector="disabled",
        vector_model=None,
        sources=["builtin"],
        skipped_sources=[],
        hits=list(hits),
        took_ms=3,
    )


async def _invoke(
    retriever: _Retriever, params: dict[str, Any], *, caller: CallerType = CallerType.INTERNAL
) -> dict[str, Any]:
    definition = build_registry().get("search_knowledge")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(retriever=retriever))
    )
    context = ToolContext(caller, None, None, request, None)
    validated = definition.params_model.model_validate(params)
    return await definition.handler(validated, context)


def test_tool_returns_hits_and_note() -> None:
    retriever = _Retriever(_result(_hit()))
    payload = asyncio.run(
        _invoke(retriever, {"query": "怎么刷固源岩", "top_k": 5})
    )

    assert payload["note"] == SEARCH_NOTE
    assert payload["query"] == "怎么刷固源岩"
    assert payload["vector"] == "disabled"
    assert payload["hits"] == [
        {
            "chunk_id": 1,
            "source": "builtin",
            "trusted": True,
            "title": "固源岩",
            "heading": "刷取",
            "url": None,
            "text": "以 1-7 刷固源岩为推荐做法",
            "version": "v1",
            "fetched_at": "2026-11-02T08:00:00Z",
        }
    ]
    assert retriever.calls == [
        {"query": "怎么刷固源岩", "sources": None, "top_k": 5, "caller": "agent"}
    ]


def test_untrusted_hits_add_envelope() -> None:
    untrusted = _hit(
        chunk_id=2,
        document_id="doc-2",
        source_id="history",
        source_title="历史记录",
        trusted=False,
        title="上次的结论",
        url="https://example.test/history/2",
    )
    # 同一来源的第二条片段：信封只列一条出处。
    same_source = _hit(
        chunk_id=3,
        document_id="doc-3",
        source_id="history",
        source_title="历史记录",
        trusted=False,
        title="另一条结论",
    )
    payload = asyncio.run(
        _invoke(_Retriever(_result(untrusted, same_source)), {"query": "以前怎么做过"})
    )

    assert payload["untrusted"] is True
    assert [source["kind"] for source in payload["sources"]] == ["history"]
    assert payload["sources"][0]["label"] == "历史记录"
    assert payload["sources"][0]["url"] == "https://example.test/history/2"
    assert [hit["trusted"] for hit in payload["hits"]] == [False, False]

    # 全可信时没有信封，但顶层显式写 untrusted=false 让 M80 的防护中间件免包装。
    trusted_payload = asyncio.run(
        _invoke(_Retriever(_result(_hit())), {"query": "怎么刷固源岩"})
    )
    assert trusted_payload["untrusted"] is False
    assert "sources" not in trusted_payload


def test_tool_visible_to_rest_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import maa_api.settings as settings_module
    from maa_api.api.errors import register_exception_handlers
    from maa_api.api.routers import agent

    # 临时关掉鉴权，直接看目录内容；monkeypatch 会在用例后还原进程内设置缓存。
    monkeypatch.setattr(settings_module, "_CACHE", settings_module.Settings())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent.router)
    app.state.confirmation_service = SimpleNamespace(registry=build_registry())

    client = TestClient(app, raise_server_exceptions=False)
    with client:
        listed = client.get("/api/agent/tools")
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()["items"]}
    assert "search_knowledge" in names
