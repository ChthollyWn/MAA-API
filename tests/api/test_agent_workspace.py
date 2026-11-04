"""M75 T4：产物端点 REST 契约（spec §4.3、§6）。

只装 ``artifacts`` 路由，临时库与资源根都落在 ``tmp_path``。覆盖 spec §6 的两条
（文件存储阈值与 16 MiB 上限），以及列表、详情截断、下载与置顶各一条：

- 列表只返回元数据，正文列不出现在响应里；
- 详情正文最多 64 KiB，超出时 ``truncated=true``；
- 下载返回原始字节，``Content-Type`` 取产物 ``mime``；
- 置顶与取消置顶，未知 id 统一 404 ``ARTIFACT_NOT_FOUND``。
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

from maa_api.agent.artifacts.service import (
    DB_STORAGE_MAX_BYTES,
    MAX_ARTIFACT_BYTES,
    ArtifactService,
)
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import artifacts
from maa_api.db import session as db_session
from maa_api.domain.errors import AppError, ErrorCode


@pytest.fixture
def workspace_env(isolated_db, tmp_path, tmp_settings):
    """临时库 + 真 ``ArtifactService`` 的 app；只装产物路由。"""

    async def prepare() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = db_session.session_factory
    root = tmp_path / "resource"
    service = ArtifactService(factory, resource_root=root)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(artifacts.router)
    app.state.artifact_service = service
    return SimpleNamespace(
        app=app,
        service=service,
        factory=factory,
        root=root,
        engine=isolated_db,
    )


def _put(env, **kwargs):
    """在独立事件循环里写一条产物；写完回收连接，避免跨循环复用。"""
    try:
        return asyncio.run(env.service.put(**kwargs))
    finally:
        env.engine.sync_engine.dispose()


def test_file_storage_over_256kib(workspace_env, make_client) -> None:
    """超过 256 KiB 落文件：path/sha256 正确，列表响应不含正文。"""
    body = b"x" * (DB_STORAGE_MAX_BYTES + 1)
    artifact = _put(
        workspace_env,
        kind="spill",
        title="大输出",
        body=body,
        mime="application/json",
    )
    artifact_id = str(artifact.id)
    assert artifact.storage == "file"
    assert artifact.path == f"artifacts/{artifact_id}"
    assert artifact.sha256 == hashlib.sha256(body).hexdigest()
    assert (workspace_env.root / artifact.path).read_bytes() == body

    with make_client(workspace_env.app) as client:
        response = client.get("/api/agent/artifacts", params={"kind": "spill"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        item = payload["items"][0]
        assert item["id"] == artifact_id
        assert item["storage"] == "file"
        assert "content" not in item
        assert "path" not in item


def test_over_16mib_is_413(workspace_env) -> None:
    """超过 16 MiB 的服务层错误是 413 ``ARTIFACT_TOO_LARGE``。"""
    with pytest.raises(AppError) as caught:
        _put(
            workspace_env,
            kind="spill",
            title="太大",
            body=b"x" * (MAX_ARTIFACT_BYTES + 1),
        )
    assert caught.value.code == ErrorCode.ARTIFACT_TOO_LARGE
    assert caught.value.http_status == 413


def test_list_filters_and_paginates_without_body(workspace_env, make_client) -> None:
    """列表按 kind 过滤并分页，响应用统一 ``{items,total,page,size}``。"""
    for index in range(3):
        _put(
            workspace_env,
            kind="note" if index < 2 else "spill",
            title=f"产物 {index}",
            body=f"正文 {index}".encode(),
        )
    with make_client(workspace_env.app) as client:
        response = client.get(
            "/api/agent/artifacts",
            params={"kind": "note", "page": 2, "size": 1},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert (payload["page"], payload["size"]) == (2, 1)
        assert len(payload["items"]) == 1
        assert "content" not in payload["items"][0]
        assert payload["items"][0]["kind"] == "note"

        invalid = client.get("/api/agent/artifacts", params={"page": 0})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "INVALID_PAGINATION"


def test_detail_truncates_body_to_64kib(workspace_env, make_client) -> None:
    """详情正文最多 64 KiB；小正文完整返回且 ``truncated=false``。"""
    big = "a" * (artifacts.ARTIFACT_DETAIL_MAX_CHARS + 1024)
    big_artifact = _put(
        workspace_env, kind="note", title="长笔记", body=big.encode()
    )
    small = _put(workspace_env, kind="note", title="短笔记", body="短正文".encode())

    with make_client(workspace_env.app) as client:
        detail = client.get(f"/api/agent/artifacts/{big_artifact.id}")
        assert detail.status_code == 200
        payload = detail.json()
        assert len(payload["content"]) == artifacts.ARTIFACT_DETAIL_MAX_CHARS
        assert payload["truncated"] is True
        assert payload["id"] == str(big_artifact.id)

        complete = client.get(f"/api/agent/artifacts/{small.id}")
        assert complete.status_code == 200
        assert complete.json()["content"] == "短正文"
        assert complete.json()["truncated"] is False


def test_download_returns_raw_bytes_and_mime(workspace_env, make_client) -> None:
    """下载返回原始字节，``Content-Type`` 取产物 ``mime``。"""
    body = b"y" * (DB_STORAGE_MAX_BYTES + 1)
    artifact = _put(
        workspace_env,
        kind="file",
        title="原始文件",
        body=body,
        mime="application/octet-stream",
    )
    with make_client(workspace_env.app) as client:
        response = client.get(f"/api/agent/artifacts/{artifact.id}/content")
        assert response.status_code == 200
        assert response.content == body
        assert response.headers["content-type"] == "application/octet-stream"


def test_pin_and_missing_artifact(workspace_env, make_client) -> None:
    """置顶/取消置顶写回响应，未知 id 是 404 ``ARTIFACT_NOT_FOUND``。"""
    artifact = _put(
        workspace_env, kind="spill", title="外置输出", body="正文".encode()
    )
    with make_client(workspace_env.app) as client:
        pinned = client.put(
            f"/api/agent/artifacts/{artifact.id}/pin", json={"pinned": True}
        )
        assert pinned.status_code == 200
        assert pinned.json()["pinned"] is True

        listed = client.get("/api/agent/artifacts").json()
        assert listed["items"][0]["pinned"] is True

        unpinned = client.put(
            f"/api/agent/artifacts/{artifact.id}/pin", json={"pinned": False}
        )
        assert unpinned.status_code == 200
        assert unpinned.json()["pinned"] is False

        missing = client.get("/api/agent/artifacts/nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"
        missing_pin = client.put(
            "/api/agent/artifacts/nope/pin", json={"pinned": True}
        )
        assert missing_pin.status_code == 404
        assert missing_pin.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"
