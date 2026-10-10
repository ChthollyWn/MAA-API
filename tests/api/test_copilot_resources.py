"""作业资源的三条 HTTP 面（M29，规格 §9.5 / §9.10，docs/05 §6.14）。

前端「作业」页（``web/src/features/copilots/api.ts``）按下面三条形状调用：

- ``GET    /api/resources/copilots/{asset_id}``        资源 wire + ``content``
- ``DELETE /api/resources/copilots/{asset_id}``        ``{id, removed}``
- ``POST   /api/resources/copilots/{asset_id}/retain`` ``{retain}`` → 更新后的资源

用例在临时库里放真的作业资源（走 ``ResourceService.upload_copilot``，而不是手搓行），
这样 ``meta`` / ``content`` / 大作业落盘的形状与线上一致。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册建表用的元数据
from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import resources
from maa_api.db import session as db_session
from maa_api.db.models import ResourceAsset
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.services.resource_service import ResourceService
from maa_api.settings import Settings


def _copilot(title: str = "test operation") -> dict[str, Any]:
    """一份协议校验能过的作业（与 ``tests/services/test_agent_resource_service.py`` 同形）。"""
    return {
        "minimum_required": "v4.0.0",
        "stage_name": "1-7",
        "doc": {"title": title, "details": "validated sample"},
        "opers": [{"name": "阿米娅", "skill": 1}],
        "actions": [
            {"type": "Deploy", "name": "阿米娅", "location": [3, 4], "direction": "Left"}
        ],
    }


@pytest.fixture
def copilot_app(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, async_sessionmaker[AsyncSession]]]:
    """临时库 + 临时资源目录的路由 app（不进生产 lifespan、不加载 MaaCore）。"""
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    resource_root = tmp_path / "resource"
    service = ResourceService(factory, resource_root=resource_root)
    # 路由的 ``_resource_root`` 兜底路径与 ``REPO_ROOT`` 都指到临时目录：即使没有
    # ``app.state.resource_service``，也不会去碰仓库里真实的 resource/。
    monkeypatch.setattr(resources, "_resource_root", lambda: resource_root)
    monkeypatch.setattr(resources, "REPO_ROOT", tmp_path)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(resources.router)
    application.state.resource_service = service

    async def get_test_session():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = get_test_session
    try:
        yield application, factory
    finally:
        engine.sync_engine.dispose()


@pytest.fixture
def client(copilot_app, make_client) -> Any:
    return make_client(copilot_app[0])


def _upload(service: ResourceService, name: str, content: dict[str, Any]) -> str:
    saved = asyncio.run(
        service.upload_copilot(name=name, description=None, content=content)
    )
    return str(saved["id"])


# ---------------------------------------------------------------------------
# GET /api/resources/copilots/{asset_id}
# ---------------------------------------------------------------------------


def test_detail_returns_resource_wire_with_content(copilot_app, client) -> None:
    app, _factory = copilot_app
    asset_id = _upload(app.state.resource_service, "查看 1-7", _copilot())

    response = client.get(f"/api/resources/copilots/{asset_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == asset_id
    assert body["kind"] == "copilot"
    assert body["meta"]["stage_name"] == "1-7"
    assert body["meta"]["title"] == "test operation"
    # 查看 JSON 与下载共用这一次请求：content 必须带上（列表端点刻意不带）。
    assert body["content"] == _copilot()
    assert client.get("/api/resources/copilots").json()["items"][0].get("content") is None


def test_detail_reads_content_from_disk_for_large_assets(copilot_app, client) -> None:
    app, _factory = copilot_app
    content = _copilot("large") | {"doc": {"title": "large", "details": "y" * 70_000}}
    asset_id = _upload(app.state.resource_service, "大作业", content)

    async def stored_path() -> str | None:
        async with app.state.resource_service.session_factory() as session:
            row = await ResourceAssetRepository(session).get(asset_id)
            assert row is not None
            return row.path

    path = asyncio.run(stored_path())
    assert path, "超过内联上限的作业应当落盘（content 列为空）"

    response = client.get(f"/api/resources/copilots/{asset_id}")

    assert response.status_code == 200, response.text
    assert response.json()["content"] == content


def test_detail_missing_or_wrong_kind_is_404(copilot_app, client) -> None:
    app, factory = copilot_app
    unknown = client.get(f"/api/resources/copilots/{'0' * 32}")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "RESOURCE_ASSET_NOT_FOUND"

    async def seed_custom_task() -> str:
        async with factory() as session:
            created = await ResourceAssetRepository(session).create(
                ResourceAsset(
                    kind="custom_task", name="not-a-copilot", content={"a": 1}
                )
            )
            await session.commit()
            return str(created.id)

    wrong_kind = client.get(f"/api/resources/copilots/{asyncio.run(seed_custom_task())}")
    assert wrong_kind.status_code == 404
    assert wrong_kind.json()["error"]["code"] == "RESOURCE_ASSET_NOT_FOUND"


# ---------------------------------------------------------------------------
# DELETE /api/resources/copilots/{asset_id}
# ---------------------------------------------------------------------------


def test_delete_removes_asset_and_reports_removed(copilot_app, client) -> None:
    app, _factory = copilot_app
    asset_id = _upload(app.state.resource_service, "删掉我", _copilot())

    response = client.delete(f"/api/resources/copilots/{asset_id}")

    assert response.status_code == 200, response.text
    assert response.json() == {"id": asset_id, "removed": True}
    assert client.get(f"/api/resources/copilots/{asset_id}").status_code == 404
    assert client.get("/api/resources/copilots").json()["total"] == 0


def test_delete_missing_asset_is_404(client) -> None:
    response = client.delete(f"/api/resources/copilots/{'1' * 32}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_ASSET_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /api/resources/copilots/{asset_id}/retain
# ---------------------------------------------------------------------------


def test_retain_round_trip_updates_meta_and_returns_wire(copilot_app, client) -> None:
    app, factory = copilot_app
    asset_id = _upload(app.state.resource_service, "保留我", _copilot())

    async def seed_meta() -> None:
        async with factory() as session:
            repo = ResourceAssetRepository(session)
            row = await repo.get(asset_id)
            assert row is not None
            row.meta = {**(row.meta or {}), "source": "ai", "retain": False}
            await session.merge(row)
            await session.commit()

    asyncio.run(seed_meta())

    retained = client.post(
        f"/api/resources/copilots/{asset_id}/retain", json={"retain": True}
    )
    assert retained.status_code == 200, retained.text
    assert retained.json()["meta"]["retain"] is True
    # 保留不碰其它 meta 键，也不改内容。
    assert retained.json()["meta"]["source"] == "ai"
    assert retained.json()["meta"]["stage_name"] == "1-7"
    assert "content" not in retained.json()
    assert client.get(f"/api/resources/copilots/{asset_id}").json()["meta"]["retain"] is True

    released = client.post(
        f"/api/resources/copilots/{asset_id}/retain", json={"retain": False}
    )
    assert released.status_code == 200, released.text
    assert released.json()["meta"]["retain"] is False
    assert client.get(f"/api/resources/copilots/{asset_id}").json()["meta"]["retain"] is False


def test_retain_rejects_missing_body_field_and_unknown_asset(copilot_app, client) -> None:
    app, _factory = copilot_app
    asset_id = _upload(app.state.resource_service, "校验我", _copilot())

    invalid = client.post(f"/api/resources/copilots/{asset_id}/retain", json={})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    missing = client.post(
        f"/api/resources/copilots/{'2' * 32}/retain", json={"retain": True}
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_ASSET_NOT_FOUND"


def test_copilot_routes_keep_the_standard_business_auth_dependency() -> None:
    from maa_api.api.deps import require_auth

    assert any(
        dependency.dependency is require_auth
        for dependency in resources.router.dependencies
    )
