"""M23 §3.3：截图加工（画框 → 裁剪 → 放大、字体降级、质量 90）。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from maa_api.db import session as db_session
from maa_api.settings import AdbSettings, Settings
from maa_api.util import image as image_module

WHITE = (255, 255, 255)
BOX_COLOR = (229, 72, 77)  # #e5484d


@pytest.fixture
def resource_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "resource"
    root.mkdir()
    monkeypatch.setattr(db_session, "DB_PATH", root / "maa_api.db")
    return root


def _is_red(pixel: tuple[int, ...], *, tolerance: int = 24) -> bool:
    return all(
        abs(channel - expected) <= tolerance
        for channel, expected in zip(pixel[:3], BOX_COLOR, strict=True)
    )


def _is_white(pixel: tuple[int, ...]) -> bool:
    return all(channel >= 250 for channel in pixel[:3])


def test_box_is_drawn_before_crop_and_zoom() -> None:
    """顺序是**先画框 → 再裁剪 → 再放大**：框必须落在原图的位置上。"""
    image = Image.new("RGB", (400, 400), WHITE)

    result = image_module.render_annotation(
        image,
        crop={"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5},
        zoom=2.0,
        boxes=[{"x": 0.5, "y": 0.5, "width": 0.25, "height": 0.25}],
    )

    # 裁剪出的 200×200 再放大 2 倍，线宽按裁剪前原图算：max(2, round(400/400)) = 2。
    assert result.image.size == (400, 400)
    assert (result.width, result.height) == (400, 400)
    assert result.zoom_applied == 2.0
    assert result.crop == (200, 200, 400, 400)

    pixels = result.image.load()
    # 框的左上角正好落在裁剪框的左上角 → 输出 (0, 0) 是红。
    assert _is_red(pixels[0, 0])
    assert _is_red(pixels[3, 0]) or _is_red(pixels[0, 3])
    # 框内不填充。
    assert _is_white(pixels[100, 100])
    # 框的右下角在输出 (200, 200) 附近：线宽 2 × 放大 2 = 4px。
    assert _is_red(pixels[199, 199])
    # 框外（裁剪框右下之外没有，但裁剪框左上角以外的原图像素已不在图里）。
    assert not _is_red(pixels[300, 300])


def test_drawing_after_crop_would_put_the_box_in_the_wrong_place() -> None:
    """反证：同一个归一化框若在裁剪后画，会落在输出正中而不是左上角。"""
    image = Image.new("RGB", (400, 400), WHITE)

    result = image_module.render_annotation(
        image,
        crop={"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5},
        zoom=1.0,
        boxes=[{"x": 0.5, "y": 0.5, "width": 0.25, "height": 0.25}],
    )
    pixels = result.image.load()

    assert _is_red(pixels[0, 0])
    assert _is_white(pixels[150, 150])


def test_line_width_scales_with_the_original_short_edge() -> None:
    small = image_module.render_annotation(
        Image.new("RGB", (300, 300), WHITE),
        boxes=[{"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}],
    )
    large = image_module.render_annotation(
        Image.new("RGB", (2400, 2400), WHITE),
        boxes=[{"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}],
    )

    assert small.zoom_applied == 1.0 and small.image.size == (300, 300)
    # 300/400 → 2px；2400/400 → 6px（放大后长边被 2048 上限压到 2048/2400）。
    assert large.image.size == (2048, 2048)
    assert large.zoom_applied == pytest.approx(2048 / 2400, abs=1e-4)


def test_zoom_is_capped_so_the_long_edge_never_exceeds_2048() -> None:
    image = Image.new("RGB", (1600, 1200), WHITE)

    result = image_module.render_annotation(image, zoom=8.0)

    assert max(result.image.size) == image_module.ANNOTATION_MAX_EDGE
    assert result.image.size == (2048, 1536)
    assert result.zoom_applied == pytest.approx(1.28, abs=1e-4)


def test_oversized_crop_is_shrunk_to_the_edge_limit() -> None:
    """裁剪块本身就超过 2048 长边时，有效倍数会小于 1（图仍然有界）。"""
    result = image_module.render_annotation(Image.new("RGB", (4000, 1000), WHITE), zoom=1.0)

    assert result.image.size == (2048, 512)
    assert result.zoom_applied == pytest.approx(0.512, abs=1e-4)


def test_label_is_drawn_when_a_font_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = ImageFont.truetype(
        next(
            (
                candidate
                for candidate in image_module.CJK_FONT_CANDIDATES
                if Path(candidate).is_file()
            ),
            "",
        ),
        20,
    ) if any(Path(item).is_file() for item in image_module.CJK_FONT_CANDIDATES) else None
    if font is None:
        pytest.skip("本机没有任何候选 CJK 字体")

    monkeypatch.setattr(image_module, "find_cjk_font", lambda _size: font)

    result = image_module.render_annotation(
        Image.new("RGB", (600, 600), WHITE),
        boxes=[{"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.3, "label": "理智"}],
    )

    # 框内左上角出现非白像素（文字），框内右下角仍是白 —— 文字只画在左上角。
    pixels = result.image.load()
    region = [
        pixels[x, y]
        for x in range(72, 200)
        for y in range(72, 140)
    ]
    assert any(not _is_white(pixel) for pixel in region)


def test_missing_font_draws_the_box_without_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_module, "CJK_FONT_CANDIDATES", ())

    assert image_module.find_cjk_font(16) is None
    result = image_module.render_annotation(
        Image.new("RGB", (400, 400), WHITE),
        boxes=[
            {
                "x": 0.25,
                "y": 0.25,
                "width": 0.5,
                "height": 0.5,
                "label": "找不到字体也不该报错",
            }
        ],
    )

    pixels = result.image.load()
    assert _is_red(pixels[100, 100])
    assert _is_white(pixels[150, 150])
    # 没有字体时不画文字：框内只允许纯白（文字若画了会留下红/灰像素）。
    inner = {
        pixels[x, y]
        for x in range(110, 190)
        for y in range(110, 190)
    }
    assert inner == {WHITE}


def test_find_cjk_font_probes_candidates_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_module, "CJK_FONT_CANDIDATES", ("/no/such/font-one.ttc", "/no/font-two.ttf")
    )
    assert image_module.find_cjk_font(24) is None
    assert image_module.find_cjk_font(0) is None


def test_invalid_boxes_are_ignored_and_zoom_must_be_positive() -> None:
    result = image_module.render_annotation(
        Image.new("RGB", (200, 200), WHITE),
        boxes=[{"x": "nope"}, {"x": 1.5, "y": -1, "width": 0.5, "height": 0.5}],
    )

    assert result.image.size == (200, 200)
    pixels = result.image.load()
    # 坏框整条忽略；越界坐标夹到边界 → 只剩贴右边缘的窄条，且仍然画出来了。
    assert any(
        _is_red(pixels[x, y]) for x in range(195, 200) for y in range(0, 4)
    )
    assert _is_white(pixels[0, 0])
    with pytest.raises(ValueError, match="zoom"):
        image_module.render_annotation(Image.new("RGB", (10, 10)), zoom=0)


def test_annotation_output_is_stored_at_quality_90(
    resource_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`store_screenshot(..., quality=90)` 的产物与默认质量（adb.screenshot_quality）不同。"""
    monkeypatch.setattr(
        image_module, "get_settings", lambda: Settings(adb=AdbSettings(screenshot_quality=25))
    )
    result = image_module.render_annotation(
        Image.new("RGB", (600, 400), (200, 40, 40)),
        boxes=[{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "label": "标注"}],
    )

    annotated = image_module.store_screenshot(result.image, quality=90)
    default_quality = image_module.store_screenshot(result.image)

    assert annotated["sha256"] != default_quality["sha256"]
    assert annotated["bytes"] > default_quality["bytes"]
    full_path = (
        resource_root
        / "image"
        / "screenshot"
        / annotated["sha256"][:2]
        / f"{annotated['sha256']}.jpg"
    )
    with Image.open(full_path) as stored:
        assert stored.format == "JPEG"
        assert stored.size == (600, 400)


def test_bytes_and_path_inputs_are_accepted(tmp_path: Path) -> None:
    image = Image.new("RGB", (120, 80), WHITE)
    output = io.BytesIO()
    image.save(output, format="PNG")
    path = tmp_path / "shot.png"
    image.save(path, format="PNG")

    from_bytes = image_module.render_annotation(output.getvalue(), zoom=2.0)
    from_path = image_module.render_annotation(path, zoom=2.0)

    assert from_bytes.image.size == (240, 160)
    assert from_bytes.image.tobytes() == from_path.image.tobytes()
