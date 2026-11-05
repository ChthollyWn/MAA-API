"""M84 T7：``ArtifactService.update`` 的正文与 meta 替换（spec §4.5.9）。

简报产物先以 ``pending`` 建出来，收尾时用 ``update`` 整体换掉正文与 meta；这里
钉住大小、sha256、``updated_at`` 与库内/文件存储之间的切换。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.artifacts.service import (
    ARTIFACTS_DIR,
    DB_STORAGE_MAX_BYTES,
    ArtifactService,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory


@pytest.fixture
def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine, session_factory = asyncio.run(make_session_factory(tmp_path / "artifacts.db"))
    yield session_factory
    engine.sync_engine.dispose()


def test_update_replaces_content_and_meta(factory, tmp_path) -> None:
    async def scenario() -> None:
        service = ArtifactService(factory, resource_root=tmp_path / "resource")
        artifact = await service.put(
            kind="report",
            title="每日简报 · 11 月 5 日",
            body="生成中".encode(),
            meta={"brief": True, "status": "pending", "ticket_id": None},
        )
        created_at = artifact.created_at

        content = "# 每日简报 · 11 月 5 日\n\n今天状态不错。\n\n## 今日建议\n1. 先刷 1-7"
        updated = await service.update(
            str(artifact.id),
            content=content,
            meta={"brief": True, "status": "ready", "ticket_id": "ticket-1"},
        )
        body = content.encode("utf-8")
        assert updated.content == content
        assert updated.meta == {
            "brief": True,
            "status": "ready",
            "ticket_id": "ticket-1",
        }
        assert updated.size_bytes == len(body)
        assert updated.sha256 == hashlib.sha256(body).hexdigest()
        assert updated.title == "每日简报 · 11 月 5 日"
        assert updated.updated_at >= created_at

        detail = await service.get(str(artifact.id))
        assert detail.content == content
        assert detail.artifact.meta["status"] == "ready"

    asyncio.run(scenario())


def test_update_missing_artifact_raises(factory, tmp_path) -> None:
    async def scenario() -> None:
        service = ArtifactService(factory, resource_root=tmp_path / "resource")
        with pytest.raises(AppError) as exc:
            await service.update("missing", content="x", meta={})
        assert exc.value.code == ErrorCode.ARTIFACT_NOT_FOUND

    asyncio.run(scenario())


def test_update_switches_between_db_and_file_storage(factory, tmp_path) -> None:
    async def scenario() -> None:
        resource_root = tmp_path / "resource"
        service = ArtifactService(factory, resource_root=resource_root)
        artifact = await service.put(
            kind="report", title="每日简报", body=b"pending", meta={"status": "pending"}
        )
        spilled = "长" * (DB_STORAGE_MAX_BYTES + 16)

        updated = await service.update(
            str(artifact.id), content=spilled, meta={"status": "ready"}
        )
        spilled_path = resource_root / ARTIFACTS_DIR / str(artifact.id)
        assert updated.storage == "file"
        assert updated.content is None
        assert updated.path == f"{ARTIFACTS_DIR}/{artifact.id}"
        assert spilled_path.read_text(encoding="utf-8") == spilled
        downloaded, _mime = await service.download(str(artifact.id))
        assert downloaded == spilled.encode("utf-8")

        shrunk = await service.update(
            str(artifact.id), content="收尾", meta={"status": "facts_only"}
        )
        assert shrunk.storage == "db"
        assert shrunk.content == "收尾"
        assert shrunk.path is None
        assert shrunk.size_bytes == len("收尾".encode())
        assert not spilled_path.exists()

    asyncio.run(scenario())
