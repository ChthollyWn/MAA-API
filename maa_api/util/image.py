"""Content-addressed screenshot storage and inline thumbnail generation."""

from __future__ import annotations

import hashlib
import io
import math
import os
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from maa_api.settings import get_settings

__all__ = [
    "AnnotationResult",
    "GRID_DIVISIONS",
    "GRID_LINE_COLOR",
    "draw_coordinate_grid",
    "find_cjk_font",
    "render_annotation",
    "store_screenshot",
]

_STORE_LOCK = threading.RLock()
_THUMBNAIL_EDGE = 320
_THUMBNAIL_QUALITY = 60

#: 坐标网格的线色（琥珀色，在深色与浅色截图上都看得见）。
GRID_LINE_COLOR = (255, 196, 0)
#: 网格标签颜色。
GRID_LABEL_COLOR = (255, 236, 160)
#: 半透明网格覆盖层的 alpha。
GRID_LINE_ALPHA = 132
#: 每张图横向/纵向的格数（``width / GRID_DIVISIONS`` 为格宽）。
GRID_DIVISIONS = 10

#: 标注框颜色（规格 §3.3 的红）。
ANNOTATION_BOX_COLOR = "#e5484d"
#: 放大后长边的硬上限：超过就下调有效 zoom（规格 §3.3）。
ANNOTATION_MAX_EDGE = 2048
#: 线宽的分母：``max(2, round(min(w, h) / 400))``，按**裁剪前**原图尺寸算。
ANNOTATION_LINE_DIVISOR = 400
#: 标注文字字号的分母（规格没有定死字号，按原图短边换算，最小 12px）。
ANNOTATION_LABEL_DIVISOR = 45
ANNOTATION_MIN_LABEL_SIZE = 12

#: CJK 字体候选（macOS / Linux / Windows）。全都不存在时 :func:`find_cjk_font` 返回
#: ``None``，调用方只画框、不画文字，**不报错**（规格 §3.3）。
CJK_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",
)


@dataclass(frozen=True, slots=True)
class AnnotationResult:
    """``render_annotation`` 的产物：图像本身 + 实际生效的几何。

    ``zoom_applied`` 不一定等于请求的 ``zoom``：放大后长边超 :data:`ANNOTATION_MAX_EDGE`
    时会下调（甚至小于 1，即缩小），调用方要把它写进卡片 caption（规格 §3.3）。
    """

    image: Image.Image
    zoom_applied: float
    crop: tuple[int, int, int, int]
    width: int
    height: int


def find_cjk_font(size: int) -> ImageFont.FreeTypeFont | None:
    """按候选列表探测一个能画中文的字体；一个都找不到返回 ``None``。

    只探测、不抛异常：标注是增强信息，缺字体不该让整个工具失败（规格 §3.3）。
    """
    if size < 1:
        return None
    for candidate in CJK_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, ValueError):
            continue
    return None


def draw_coordinate_grid(image: Any, *, divisions: int = GRID_DIVISIONS) -> Image.Image:
    """在截图上叠加带原始像素坐标刻度的网格（M57，docs/13 §8.80）。

    视觉模型从缩略图估计点击坐标经常差几十像素：2026-10-17 的实机会话里，突袭结算
    后 12 次点击有 11 次打空（Y 方向差 ~80px）。网格把「图上看到的位置」换算成
    ``click`` 需要的原图像素坐标，标签直接写像素值，不需要模型自己乘缩放比。

    网格只影响发给模型的图像副本，落库/发给用户的截图保持原样。任何解码或绘制失败
    都原样返回：网格是增强信息，不该让截图失败。
    """
    decoded = _decode_image(image).convert("RGBA")
    width, height = decoded.size
    if width < 2 or height < 2:
        return decoded.convert("RGB")
    divisions = max(int(divisions), 1)
    step_x = max(round(width / divisions), 1)
    step_y = max(round(height / divisions), 1)
    line_width = max(1, round(min(width, height) / 720))
    font_size = max(12, round(min(width, height) / 48))
    try:
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.load_default(size=font_size)
    except TypeError:  # Pillow < 10.1 没有 size 参数
        font = ImageFont.load_default()  # type: ignore[assignment]
    overlay = Image.new("RGBA", decoded.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    line_color = (*GRID_LINE_COLOR, GRID_LINE_ALPHA)
    label_color = (*GRID_LABEL_COLOR, 255)
    for x in range(step_x, width, step_x):
        draw.line([(x, 0), (x, height)], fill=line_color, width=line_width)
        draw.text((x + 3, 3), str(x), fill=label_color, font=font)
    for y in range(step_y, height, step_y):
        draw.line([(0, y), (width, y)], fill=line_color, width=line_width)
        draw.text((3, y + 1), str(y), fill=label_color, font=font)
    return Image.alpha_composite(decoded, overlay).convert("RGB")


def render_annotation(
    image: Any,
    *,
    crop: Mapping[str, Any] | None = None,
    zoom: float = 1.0,
    boxes: Sequence[Mapping[str, Any]] = (),
) -> AnnotationResult:
    """按规格 §3.3 的顺序加工截图：**先画框 → 再裁剪 → 再放大**。

    坐标系是原图归一化坐标（0–1，原点左上）：``crop`` 是 ``{x, y, width, height}``
    （缺省＝整图），``boxes`` 每项同形并可带 ``label``。框线宽按裁剪前原图尺寸算，
    文字用 :func:`find_cjk_font` 探测到的字体画在框左上角；找不到字体就只画框。
    放大用 LANCZOS，长边超 2048 时下调有效倍数并记在 ``zoom_applied`` 里。
    """
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("zoom 必须是大于 0 的有限数")

    decoded = _decode_image(image)
    width, height = decoded.size
    line_width = max(2, round(min(width, height) / ANNOTATION_LINE_DIVISOR))

    draw = ImageDraw.Draw(decoded)
    labeled: list[tuple[tuple[int, int, int, int], str]] = []
    for box in boxes:
        rect = _normalized_rect(box, width, height)
        if rect is None:
            continue
        draw.rectangle(rect, outline=ANNOTATION_BOX_COLOR, width=line_width)
        label = box.get("label") if isinstance(box, Mapping) else None
        if isinstance(label, str) and label.strip():
            labeled.append((rect, label.strip()))

    if labeled:
        font = find_cjk_font(
            max(ANNOTATION_MIN_LABEL_SIZE, round(min(width, height) / ANNOTATION_LABEL_DIVISOR))
        )
        if font is not None:
            for rect, label in labeled:
                draw.text(
                    (rect[0] + line_width, rect[1] + line_width),
                    label,
                    fill=ANNOTATION_BOX_COLOR,
                    font=font,
                )

    crop_rect = _normalized_rect(
        {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0} if crop is None else crop,
        width,
        height,
    )
    if crop_rect is None:
        crop_rect = (0, 0, width, height)
    cropped = decoded.crop(crop_rect)

    crop_width, crop_height = cropped.size
    longest_edge = max(crop_width, crop_height)
    effective = float(zoom)
    if longest_edge * effective > ANNOTATION_MAX_EDGE:
        effective = ANNOTATION_MAX_EDGE / longest_edge
    if abs(effective - 1.0) > 1e-9:
        cropped = cropped.resize(
            (
                max(1, round(crop_width * effective)),
                max(1, round(crop_height * effective)),
            ),
            Image.Resampling.LANCZOS,
        )

    return AnnotationResult(
        image=cropped,
        zoom_applied=round(effective, 4),
        crop=crop_rect,
        width=cropped.width,
        height=cropped.height,
    )


def _normalized_rect(
    value: Any, width: int, height: int
) -> tuple[int, int, int, int] | None:
    """归一化矩形 → 像素矩形（左闭右开），越界裁剪、退化矩形至少 1px。"""
    if not isinstance(value, Mapping):
        return None
    try:
        x = float(value["x"])
        y = float(value["y"])
        box_width = float(value["width"])
        box_height = float(value["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(number) for number in (x, y, box_width, box_height)):
        return None

    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    box_width = min(max(box_width, 0.0), 1.0 - x)
    box_height = min(max(box_height, 0.0), 1.0 - y)

    left = min(int(round(x * width)), width - 1)
    top = min(int(round(y * height)), height - 1)
    right = min(width, max(left + 1, int(round((x + box_width) * width))))
    bottom = min(height, max(top + 1, int(round((y + box_height) * height))))
    return left, top, right, bottom


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
