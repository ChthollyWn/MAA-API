"""Content-addressed screenshot storage contracts."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from maa_api.db import session as db_session
from maa_api.settings import AdbSettings, Settings
from maa_api.util import image as image_module


@pytest.fixture
def resource_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "resource"
    root.mkdir()
    monkeypatch.setattr(db_session, "DB_PATH", root / "maa_api.db")
    return root


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _paths(resource_root: Path, attachment: dict) -> tuple[Path, Path]:
    directory = resource_root / "image" / "screenshot" / attachment["sha256"][:2]
    return (
        directory / f"{attachment['sha256']}.jpg",
        directory / f"{attachment['sha256']}.thumb.jpg",
    )


def test_bytes_and_pillow_image_store_as_same_jpeg_attachment(
    resource_root: Path,
) -> None:
    image = Image.new("RGB", (800, 400), (20, 80, 140))
    encoded = _png_bytes(image)

    from_bytes = image_module.store_screenshot(encoded, quality=72)
    from_image = image_module.store_screenshot(image, quality=72)

    assert from_bytes == from_image
    assert set(from_bytes) == {
        "kind",
        "sha256",
        "width",
        "height",
        "bytes",
        "thumb_url",
        "full_url",
        "captured_at",
    }
    assert from_bytes["kind"] == "screenshot"
    assert from_bytes["width"] == 800 and from_bytes["height"] == 400
    assert from_bytes["thumb_url"] == f"/api/images/{from_bytes['sha256']}/thumb"
    assert from_bytes["full_url"] == f"/api/images/{from_bytes['sha256']}/full"

    full_path, thumb_path = _paths(resource_root, from_bytes)
    assert full_path.is_file() and thumb_path.is_file()
    assert full_path.relative_to(resource_root).as_posix() == (
        f"image/screenshot/{from_bytes['sha256'][:2]}/{from_bytes['sha256']}.jpg"
    )
    with Image.open(full_path) as full:
        assert full.format == "JPEG"
        assert full.size == (800, 400)
    with Image.open(thumb_path) as thumb:
        assert thumb.format == "JPEG"
        assert max(thumb.size) == 320
        assert thumb.size == (320, 160)


def test_content_hash_deduplicates_without_rewriting_files(resource_root: Path) -> None:
    image = Image.new("RGB", (100, 60), "navy")

    first = image_module.store_screenshot(image, quality=80)
    full_path, thumb_path = _paths(resource_root, first)
    full_stat = full_path.stat()
    thumb_stat = thumb_path.stat()
    second = image_module.store_screenshot(image, quality=80)

    assert first == second
    assert full_path.stat().st_ino == full_stat.st_ino
    assert full_path.stat().st_mtime_ns == full_stat.st_mtime_ns
    assert thumb_path.stat().st_ino == thumb_stat.st_ino
    assert thumb_path.stat().st_mtime_ns == thumb_stat.st_mtime_ns
    assert len(list(full_path.parent.iterdir())) == 2


def test_different_pixels_and_quality_values_have_distinct_hashes(
    resource_root: Path,
) -> None:
    blue = Image.new("RGB", (40, 30), "blue")
    red = Image.new("RGB", (40, 30), "red")

    blue_attachment = image_module.store_screenshot(blue, quality=80)
    red_attachment = image_module.store_screenshot(red, quality=80)
    low_quality = image_module.store_screenshot(blue, quality=25)

    assert blue_attachment["sha256"] != red_attachment["sha256"]
    assert blue_attachment["sha256"] != low_quality["sha256"]
    full_path, _ = _paths(resource_root, blue_attachment)
    assert hashlib.sha256(full_path.read_bytes()).hexdigest() == blue_attachment["sha256"]


def test_path_input_and_default_quality_use_runtime_settings(
    resource_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = Image.new("RGB", (64, 48), "orange")
    input_path = tmp_path / "input.png"
    image.save(input_path, format="PNG")
    monkeypatch.setattr(
        image_module,
        "get_settings",
        lambda: Settings(adb=AdbSettings(screenshot_quality=38)),
    )

    default_quality = image_module.store_screenshot(input_path)
    explicit_quality = image_module.store_screenshot(input_path, quality=38)

    assert default_quality == explicit_quality
    assert default_quality["bytes"] == _paths(resource_root, default_quality)[0].stat().st_size


def test_invalid_image_and_quality_fail_clearly(resource_root: Path) -> None:
    with pytest.raises(TypeError):
        image_module.store_screenshot(object())
    with pytest.raises(ValueError, match="质量"):
        image_module.store_screenshot(Image.new("RGB", (1, 1)), quality=100)
