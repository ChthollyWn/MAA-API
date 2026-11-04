"""来源测试的共用桩与断言工具（不是测试文件，pytest 不收集）。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from sqlalchemy import select

from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.db.models import KnowledgeDocument, KnowledgeSource
from maa_api.services.external.copilot_site import CopilotRow

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "corpora"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 ``asyncio.run`` 跑。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


class SettingsStub:
    """可改语料开关的 settings 桩；``**flags`` 直接挂到 ``rag`` 上。"""

    def __init__(self, *, enabled: bool = True, **flags: Any) -> None:
        values: dict[str, Any] = {
            "enabled": enabled,
            "chunk_chars": 200,
            "chunk_overlap": 20,
            "maa_docs_enabled": True,
            "prts_enabled": True,
            "gamedata_enabled": True,
            "copilot_site_enabled": True,
        }
        values.update(flags)
        self.rag = SimpleNamespace(**values)


class NullProvider:
    """``embedding_provider=none`` 的等价桩：服务在调用 ``embed`` 前就短路。"""

    name = "none"
    dim = 0

    async def embed(
        self, texts: Sequence[str], *, purpose: str
    ) -> np.ndarray:  # pragma: no cover - 服务不会调用
        raise AssertionError("provider 为 none 时不应调用 embed")


class Resolver:
    def __init__(self) -> None:
        self.provider = NullProvider()

    async def current(self) -> Any:
        return self.provider

    def status(self) -> Any:
        return SimpleNamespace(
            provider="none", model="none", status="disabled", error=None
        )


def make_service(
    session_factory: Any, source: Any, settings: SettingsStub
) -> KnowledgeIndexService:
    service = KnowledgeIndexService(
        session_factory,
        settings_provider=lambda: settings,
        embeddings=Resolver(),
    )
    service.register(source)
    return service


async def documents(factory: Any, source_id: str) -> dict[str, KnowledgeDocument]:
    async with factory() as session:
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.source_id == source_id)
        )
        return {row.external_id: row for row in result.scalars().all()}


async def source_row(factory: Any, source_id: str) -> KnowledgeSource | None:
    async with factory() as session:
        return await session.get(KnowledgeSource, source_id)


def copilot_rows() -> list[CopilotRow]:
    """把 ``copilot_query.json`` 的两行变成真正的 :class:`CopilotRow`。"""
    payload = fixture("copilot_query.json")
    rows: list[CopilotRow] = []
    for item in payload["data"]["data"]:
        rows.append(
            CopilotRow(
                site_id=int(item["id"]),
                upload_time=item.get("upload_time"),
                uploader=item.get("uploader"),
                views=item.get("views"),
                rating_ratio=item.get("rating_ratio"),
                content=item.get("content", ""),
                raw=item,
            )
        )
    return rows
