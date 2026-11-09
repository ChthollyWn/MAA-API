"""截图归档公共函数（spec §4.2、§6 T2）。

用 ``tests/fakes/v3_db.make_session_factory`` 的临时库，文件落到 ``DB_PATH`` 所在
目录（``tmp_path``），不碰仓库的 ``resource/``。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from maa_api.db import session as db_session
from maa_api.db.models import Screenshot
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.services.screenshot_archive import archive_screenshot
from tests.fakes.nav import make_screen
from tests.fakes.v3_db import make_session_factory


def test_archive_screenshot_stores_file_and_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "nav.db"
    monkeypatch.setattr(db_session, "DB_PATH", db_path)
    engine, factory = asyncio.run(make_session_factory(db_path))

    async def scenario() -> None:
        async with factory() as session:
            screenshot, stored = await archive_screenshot(
                session,
                make_screen("home"),
                trigger=ScreenshotTrigger.AGENT,
                backend=ScreenshotBackend.ADB,
            )
            row = await ScreenshotRepository(session).get(screenshot.id)
            assert row is not None

            relative = f"image/screenshot/{stored['sha256'][:2]}/{stored['sha256']}.jpg"
            assert row.path == relative
            assert (tmp_path / relative).is_file()
            assert (tmp_path / relative).read_bytes()
            assert hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest() == (
                stored["sha256"]
            )
            assert (tmp_path / relative.replace(".jpg", ".thumb.jpg")).is_file()

            assert row.trigger == "agent"
            assert row.backend == "adb"
            assert row.format == "jpg"
            assert (row.width, row.height) == (1280, 720)
            assert row.size_bytes == (tmp_path / relative).stat().st_size
            # 归档函数不 commit：调用方回滚后，另一个会话读不到这一行。
            await session.rollback()

        async with factory() as fresh:
            assert await fresh.get(Screenshot, screenshot.id) is None

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_archive_screenshot_returns_thumb_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "nav.db"
    monkeypatch.setattr(db_session, "DB_PATH", db_path)
    engine, factory = asyncio.run(make_session_factory(db_path))

    async def scenario() -> None:
        async with factory() as session:
            screenshot, stored = await archive_screenshot(
                session,
                make_screen("infrast"),
                trigger=ScreenshotTrigger.AGENT,
                backend=ScreenshotBackend.ADB,
            )
            assert set(stored) == {"screenshot_id", "sha256", "full_url", "thumb_url"}
            assert stored["screenshot_id"] == screenshot.id
            sha = stored["sha256"]
            assert stored["full_url"] == f"/api/images/{sha}/full"
            assert stored["thumb_url"] == f"/api/images/{sha}/thumb"
            await session.rollback()

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
