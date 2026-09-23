"""M4-09 screenshot archive routes and content-addressed image reads."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from PIL import Image
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers.screenshots import router
from maa_api.db import session as db_session
from maa_api.db.models import Pipeline, Screenshot, utcnow
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import PipelineSource, Priority, ScreenshotBackend, ScreenshotTrigger
from maa_api.util.image import store_screenshot


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    return app


def test_screenshot_list_detail_expiry_and_hashed_images(
    tmp_settings, isolated_db, make_client
):
    image = Image.new("RGB", (640, 360), (15, 90, 170))
    attachment = store_screenshot(image, quality=73)
    root = db_session.DB_PATH.parent
    relative_path = (
        f"image/screenshot/{attachment['sha256'][:2]}/{attachment['sha256']}.jpg"
    )
    full_path = root / relative_path
    thumb_path = full_path.with_name(f"{attachment['sha256']}.thumb.jpg")
    async def seed() -> tuple[str, str, str]:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        pipeline = Pipeline(
            source=PipelineSource.MANUAL,
            priority=Priority.MANUAL,
            task_count=1,
            title="screenshot pipeline",
        )
        now = utcnow()
        async with db_session.session_factory() as session:
            session.add(pipeline)
            await session.commit()
            repo = ScreenshotRepository(session)
            current = await repo.create(
                Screenshot(
                    pipeline_id=pipeline.id,
                    trigger=ScreenshotTrigger.TASK_SNAPSHOT,
                    backend=ScreenshotBackend.ADB,
                    path=relative_path,
                    format="jpeg",
                    width=attachment["width"],
                    height=attachment["height"],
                    size_bytes=attachment["bytes"],
                    created_at=now,
                )
            )
            expired = await repo.create(
                Screenshot(
                    pipeline_id=pipeline.id,
                    trigger=ScreenshotTrigger.TASK_SNAPSHOT,
                    backend=ScreenshotBackend.ADB,
                    path=relative_path,
                    format="jpeg",
                    width=attachment["width"],
                    height=attachment["height"],
                    size_bytes=attachment["bytes"],
                    created_at=now - timedelta(days=10),
                    deleted_at=now - timedelta(days=1),
                )
            )
            await session.commit()
            return pipeline.id, current.id, expired.id

    pipeline_id, screenshot_id, expired_id = asyncio.run(seed())
    isolated_db.sync_engine.dispose()
    client = make_client(_app())

    listing = client.get(
        "/api/screenshots",
        params={"pipeline_id": pipeline_id, "trigger": "task_snapshot", "size": 1},
    )
    assert listing.status_code == 200, listing.text
    page = listing.json()
    assert (page["total"], page["page"], page["size"]) == (2, 1, 1)
    assert page["items"][0]["id"] == screenshot_id
    assert page["items"][0]["deleted_at"] is None

    by_trigger = client.get("/api/screenshots?trigger=manual")
    assert by_trigger.status_code == 200 and by_trigger.json()["total"] == 0
    bad_page = client.get("/api/screenshots?page=0")
    assert bad_page.status_code == 400
    assert bad_page.json()["error"]["code"] == "INVALID_PAGINATION"
    too_large = client.get("/api/screenshots?size=201")
    assert too_large.status_code == 400
    assert too_large.json()["error"]["code"] == "INVALID_PAGINATION"
    by_since = client.get(
        "/api/screenshots",
        params={"since": datetime.now(timezone.utc).timestamp() - timedelta(days=2).total_seconds()},
    )
    assert by_since.status_code == 200
    assert [item["id"] for item in by_since.json()["items"]] == [screenshot_id]

    raw = client.get(f"/api/screenshots/{screenshot_id}")
    assert raw.status_code == 200 and raw.content == full_path.read_bytes()
    assert raw.headers["content-type"] == "image/jpeg"
    wrapped = client.get(f"/api/screenshots/{screenshot_id}?as=base64")
    assert wrapped.status_code == 200
    payload = wrapped.json()
    assert set(payload) == {
        "id", "format", "width", "height", "size_bytes", "captured_at", "data"
    }
    assert payload["id"] == screenshot_id
    assert base64.b64decode(payload["data"]) == full_path.read_bytes()

    expired = client.get(f"/api/screenshots/{expired_id}")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "SCREENSHOT_EXPIRED"
    missing = client.get("/api/screenshots/no-such-id")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SCREENSHOT_NOT_FOUND"

    full = client.get(f"/api/images/{attachment['sha256']}/full")
    thumb = client.get(f"/api/images/{attachment['sha256']}/thumb")
    assert full.status_code == thumb.status_code == 200
    assert full.content == full_path.read_bytes()
    assert thumb.content == thumb_path.read_bytes()
    assert full.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert thumb.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get(f"/api/images/{'a' * 64}/full").status_code == 404
    assert client.get("/api/images/../thumb").status_code == 404
