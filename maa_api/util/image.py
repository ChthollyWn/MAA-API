"""Content-addressed screenshot storage and inline thumbnail generation."""

from __future__ import annotations

import hashlib
import io
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from maa_api.settings import get_settings

__all__ = ["store_screenshot"]

_STORE_LOCK = threading.RLock()
_THUMBNAIL_EDGE = 320
_THUMBNAIL_QUALITY = 60


def store_screenshot(image: Any, *, quality: int | None = None) -> dict[str, Any]:
    """Store an image as a deduplicated JPEG and return its HTTP attachment data.

    ``image`` may be a Pillow image, encoded image bytes, or a filesystem path.
    Files live below the resource directory derived from ``DB_PATH`` at call
    time. The corresponding database ``Screenshot`` record is owned by callers.
    """

    output_quality = (
        int(get_settings().adb.screenshot_quality) if quality is None else int(quality)
    )
    if not 1 <= output_quality <= 95:
        raise ValueError("截图质量必须在 1 到 95 之间")

    decoded = _decode_image(image)
    width, height = decoded.size
    full_bytes = _encode_jpeg(decoded, quality=output_quality)
    sha256 = hashlib.sha256(full_bytes).hexdigest()

    # Import the path-bearing module at invocation time. Tests and isolated
    # deployments replace DB_PATH after importing this helper.
    from maa_api.db import session as db_session

    resource_root = Path(db_session.DB_PATH).parent
    relative_dir = Path("image") / "screenshot" / sha256[:2]
    target_dir = resource_root / relative_dir
    full_path = target_dir / f"{sha256}.jpg"
    thumb_path = target_dir / f"{sha256}.thumb.jpg"

    with _STORE_LOCK:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not full_path.exists():
            _atomic_write(full_path, full_bytes)
        if not thumb_path.exists():
            thumbnail = decoded.copy()
            thumbnail.thumbnail(
                (_THUMBNAIL_EDGE, _THUMBNAIL_EDGE), Image.Resampling.LANCZOS
            )
            _atomic_write(
                thumb_path,
                _encode_jpeg(thumbnail, quality=_THUMBNAIL_QUALITY),
            )
        captured_at = full_path.stat().st_mtime

    return {
        "kind": "screenshot",
        "sha256": sha256,
        "width": width,
        "height": height,
        "bytes": full_path.stat().st_size,
        "thumb_url": f"/api/images/{sha256}/thumb",
        "full_url": f"/api/images/{sha256}/full",
        "captured_at": captured_at,
    }


def _decode_image(image: Any) -> Image.Image:
    if isinstance(image, Image.Image):
        decoded = image.copy()
        decoded.load()
    elif isinstance(image, (bytes, bytearray, memoryview)):
        with Image.open(io.BytesIO(bytes(image))) as opened:
            decoded = opened.copy()
    elif isinstance(image, (str, Path, os.PathLike)):
        with Image.open(Path(image)) as opened:
            decoded = opened.copy()
    else:
        raise TypeError("image 必须是 Pillow Image、编码图像 bytes 或文件路径")

    decoded = ImageOps.exif_transpose(decoded)
    if decoded.mode in ("RGBA", "LA") or "transparency" in decoded.info:
        rgba = decoded.convert("RGBA")
        background = Image.new("RGB", rgba.size, (0, 0, 0))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return decoded.convert("RGB")


def _encode_jpeg(image: Image.Image, *, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality)
    return output.getvalue()


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
