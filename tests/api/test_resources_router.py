"""Behavioral tests for the read-only MaaCore item catalog endpoint."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import resources
from maa_api.settings import Settings


@pytest.fixture(autouse=True)
def _clear_item_cache() -> Iterator[None]:
    resources._INDEX_CACHE = None
    yield
    resources._INDEX_CACHE = None


@pytest.fixture
def app(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> FastAPI:
    """Build the router app without importing the production lifespan or MaaCore."""
    resource_root = tmp_path / "core" / "resource"
    monkeypatch.setattr(resources, "_resource_root", lambda: resource_root)
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(resources.router)
    return application


def _write_index(app: FastAPI, payload: Any) -> None:
    # The fixture keeps the configured resource path private to the router; recover it
    # from the patched helper so each test writes only to its own temporary tree.
    resource_root = resources._resource_root()
    resource_root.mkdir(parents=True, exist_ok=True)
    (resource_root / "item_index.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_lists_items_with_mapped_available_icons(
    app: FastAPI, make_client: Any
) -> None:
    resource_root = resources._resource_root()
    items_dir = resource_root / "template" / "items"
    items_dir.mkdir(parents=True)
    (items_dir / "4001.png").write_bytes(b"png-data")
    _write_index(
        app,
        {
            "30014": {"name": "招聘许可", "icon": "missing.png"},
            "30013": {"name": "龙门币", "icon": "4001.png"},
        },
    )

    client = make_client(app)
    response = client.get("/api/resources/items")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"item_id": "30014", "name": "招聘许可", "icon_url": None},
        {
            "item_id": "30013",
            "name": "龙门币",
            "icon_url": "/api/resources/items/icon?item_id=30013",
        },
    ]
    image = client.get(response.json()[1]["icon_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == b"png-data"

    (items_dir / "4001.png").unlink()
    refreshed = client.get("/api/resources/items")
    assert refreshed.status_code == 200
    assert refreshed.json()[1]["icon_url"] is None


@pytest.mark.parametrize("index_contents", [None, "not-json", "[]", '{"30013":{"name":42}}'])
def test_missing_or_invalid_index_returns_unified_api_error(
    app: FastAPI, make_client: Any, index_contents: str | None
) -> None:
    if index_contents is not None:
        resource_root = resources._resource_root()
        resource_root.mkdir(parents=True, exist_ok=True)
        (resource_root / "item_index.json").write_text(
            index_contents, encoding="utf-8"
        )

    response = make_client(app).get("/api/resources/items")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RESOURCE_LOAD_FAILED"
    assert response.json()["error"]["details"] == {"resource": "item_index.json"}


def test_index_update_invalidates_cached_catalog(app: FastAPI, make_client: Any) -> None:
    client = make_client(app)
    _write_index(app, {"30013": {"name": "龙门币"}})
    first = client.get("/api/resources/items")
    assert first.status_code == 200
    assert [item["item_id"] for item in first.json()] == ["30013"]

    _write_index(app, {"30013": {"name": "龙门币"}, "30014": {"name": "招聘许可"}})
    second = client.get("/api/resources/items")
    assert second.status_code == 200
    assert [item["item_id"] for item in second.json()] == ["30014", "30013"]


def test_icon_filename_cannot_escape_template_items(
    app: FastAPI, make_client: Any
) -> None:
    resource_root = resources._resource_root()
    (resource_root / "template" / "items").mkdir(parents=True)
    outside_icon = resource_root.parent / "secret.png"
    outside_icon.write_bytes(b"secret")
    _write_index(app, {"30013": {"name": "恶意条目", "icon": "../secret.png"}})

    client = make_client(app)
    response = client.get("/api/resources/items")

    assert response.status_code == 200
    assert response.json() == [{"item_id": "30013", "name": "恶意条目", "icon_url": None}]
    image = client.get("/api/resources/items/icon", params={"item_id": "30013"})
    assert image.status_code == 404
    assert image.json()["error"]["code"] == "RESOURCE_ASSET_NOT_FOUND"
    assert outside_icon.read_bytes() == b"secret"


def test_index_symlink_cannot_escape_configured_resource_root(
    app: FastAPI, make_client: Any
) -> None:
    resource_root = resources._resource_root()
    resource_root.mkdir(parents=True)
    outside_index = resource_root.parent / "external-item-index.json"
    outside_index.write_text(
        json.dumps({"30013": {"name": "外部条目"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        (resource_root / "item_index.json").symlink_to(outside_index)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    response = make_client(app).get("/api/resources/items")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RESOURCE_LOAD_FAILED"


def test_router_keeps_the_standard_business_auth_dependency() -> None:
    from maa_api.api.deps import require_auth

    assert any(
        dependency.dependency is require_auth
        for dependency in resources.router.dependencies
    )
