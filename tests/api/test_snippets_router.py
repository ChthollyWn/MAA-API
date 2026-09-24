"""REST contract for saved API snippets."""

from __future__ import annotations

import asyncio

import maa_api.db.models  # noqa: F401
from fastapi import FastAPI
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import snippets as snippets_router
from maa_api.db import session as db_session
from maa_api.domain.errors import ErrorCode
from maa_api.services.api_snippet_service import ApiSnippetService


def _snippet_client(isolated_db: AsyncEngine, make_client):
    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(snippets_router.router)
    app.state.api_snippet_service = ApiSnippetService(db_session.session_factory)
    return make_client(app)


@pytest.mark.parametrize("tmp_settings", ["snippet-secret"], indirect=True)
def test_snippet_crud_normalizes_names_and_never_persists_credentials(
    isolated_db, make_client, tmp_settings
):
    client = _snippet_client(isolated_db, make_client)
    headers = {"X-Token": "snippet-secret"}
    payload = {
        "name": "  每日剿灭  ",
        "method": "POST",
        "path": "/api/pipelines",
        "path_params": {},
        "query": {"stage": "Annihilation", "token": "query-secret"},
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer header-secret",
            "X-Api-Key": "api-key-secret",
            "Cookie": "session=cookie-secret",
        },
        "body": {"tasks": [{"name": "Fight"}]},
    }

    unauthenticated = client.get("/api/snippets")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "UNAUTHORIZED"

    created = client.post("/api/snippets", json=payload, headers=headers)
    assert created.status_code == 201
    snippet_id = created.json()["id"]
    assert created.headers["location"] == f"/api/snippets/{snippet_id}"
    saved = created.json()
    assert saved["name"] == "每日剿灭"
    assert saved["created_at"].endswith("Z")
    assert saved["updated_at"].endswith("Z")
    assert saved["query"] == {"stage": "Annihilation"}
    assert saved["headers"] == {"Content-Type": "application/json"}
    assert "secret" not in created.text
    assert saved["body"] == payload["body"]

    listed = client.get("/api/snippets", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == snippet_id

    other = client.post(
        "/api/snippets", json={**payload, "name": "备用收藏"}, headers=headers
    )
    assert other.status_code == 201
    update_conflict = client.put(
        f"/api/snippets/{snippet_id}",
        json={**payload, "name": "备用收藏"},
        headers=headers,
    )
    assert update_conflict.status_code == 409
    assert update_conflict.json()["error"]["code"] == ErrorCode.API_SNIPPET_NAME_CONFLICT

    detail = client.get(f"/api/snippets/{snippet_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json() == saved

    updated = client.put(
        f"/api/snippets/{snippet_id}",
        json={**payload, "name": "  周三剿灭  ", "headers": {"X-Debug": "1"}},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "周三剿灭"
    assert updated.json()["headers"] == {"X-Debug": "1"}
    assert updated.json()["updated_at"] >= saved["updated_at"]
    assert updated.json()["updated_at"].endswith("Z")

    duplicate = client.post(
        "/api/snippets", json={**payload, "name": "周三剿灭"}, headers=headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == ErrorCode.API_SNIPPET_NAME_CONFLICT

    deleted = client.delete(f"/api/snippets/{snippet_id}", headers=headers)
    assert deleted.status_code == 204
    missing = client.get(f"/api/snippets/{snippet_id}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == ErrorCode.API_SNIPPET_NOT_FOUND


@pytest.mark.parametrize("tmp_settings", ["snippet-secret"], indirect=True)
def test_snippet_name_uniqueness_is_case_sensitive(isolated_db, make_client, tmp_settings):
    client = _snippet_client(isolated_db, make_client)
    headers = {"X-Token": "snippet-secret"}
    base = {"method": "GET", "path": "/api/system/health"}

    upper = client.post("/api/snippets", json={**base, "name": "Health"}, headers=headers)
    lower = client.post("/api/snippets", json={**base, "name": "health"}, headers=headers)

    assert upper.status_code == 201
    assert lower.status_code == 201


@pytest.mark.parametrize("tmp_settings", ["snippet-secret"], indirect=True)
def test_snippet_name_validation_and_missing_update(isolated_db, make_client, tmp_settings):
    client = _snippet_client(isolated_db, make_client)
    headers = {"X-Token": "snippet-secret"}

    for name in ("", "   ", "x" * 65):
        response = client.post(
            "/api/snippets",
            json={"name": name, "method": "GET", "path": "/api/system/health"},
            headers=headers,
        )
        assert response.status_code == 422

    missing = client.put(
        "/api/snippets/00000000-0000-0000-0000-000000000000",
        json={"name": "missing", "method": "GET", "path": "/api/system/health"},
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == ErrorCode.API_SNIPPET_NOT_FOUND


@pytest.mark.parametrize("tmp_settings", ["snippet-secret"], indirect=True)
def test_snippet_openapi_keeps_timestamp_format(isolated_db, make_client, tmp_settings):
    client = _snippet_client(isolated_db, make_client)
    schema = client.get("/openapi.json").json()
    response = schema["components"]["schemas"]["ApiSnippetView"]["properties"]

    assert response["created_at"]["format"] == "date-time"
    assert response["updated_at"]["format"] == "date-time"
