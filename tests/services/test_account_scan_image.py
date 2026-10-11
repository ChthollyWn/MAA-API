"""专精像素读数（M36 规格 §4.4）：亮圆点计数、阈值、缩放。

全部离线：用例自己造截图（深色底 + 亮/暗圆点），
读数逻辑在 :mod:`maa_api.services.account.scan_image` 里。
"""

from __future__ import annotations

import pytest
from PIL import Image

from maa_api.services.account import scan_image
from maa_api.services.account import scan_tasks as st
from maa_api.services.account.scan_image import mastery_levels_from_image, scale_box

#: 深色底与亮/暗圆点的灰度（真机实测：暗 71、亮 255）。
BACKGROUND = 40
DARK_PIP = 71
BRIGHT_PIP = 255


def make_screen(
    *,
    scale: float = 2.0,
    lit: dict[int, tuple[str, ...]] | None = None,
    background: int = BACKGROUND,
    pip_value: int = DARK_PIP,
    bright_value: int = BRIGHT_PIP,
) -> Image.Image:
    """造一张详情页截图：默认所有圆点都是暗的，``lit`` 里点名的点亮成白。"""
    width, height = int(st.KERNEL_WIDTH * scale), int(720 * scale)
    image = Image.new("L", (width, height), background)
    pixels = image.load()
    for (slot, position), box in st.MASTER_PIP_ROIS.items():
        value = bright_value if position in (lit or {}).get(slot, ()) else pip_value
        left, top, right, bottom = scale_box(box, scale)
        for x in range(left, min(right, width)):
            for y in range(top, min(bottom, height)):
                pixels[x, y] = value
    return image


def test_all_pips_dark_means_no_mastery() -> None:
    levels = mastery_levels_from_image(make_screen())
    assert levels == {1: 0, 2: 0, 3: 0}


def test_all_pips_lit_means_mastery_three() -> None:
    lit = {slot: st.MASTER_PIP_POSITIONS for slot in (1, 2, 3)}
    levels = mastery_levels_from_image(make_screen(lit=lit))
    assert levels == {1: 3, 2: 3, 3: 3}


def test_mastery_counts_the_lit_pips_one_by_one() -> None:
    """内核模板的约定：一级只亮上点、二级加上右下点、三级三个全亮。"""
    levels = mastery_levels_from_image(
        make_screen(
            lit={
                1: ("Top",),
                2: ("Top", "BottomRight"),
                3: ("Top", "BottomRight", "BottomLeft"),
            }
        )
    )
    assert levels == {1: 1, 2: 2, 3: 3}


def test_reading_does_not_depend_on_the_screenshot_size() -> None:
    lit = {1: ("Top",), 2: (), 3: ("Top", "BottomRight", "BottomLeft")}
    expected = {1: 1, 2: 0, 3: 3}
    for scale in (1.0, 1.5, 2.0, 2.5):
        assert mastery_levels_from_image(make_screen(scale=scale, lit=lit)) == expected


def test_dim_pips_stay_below_the_threshold() -> None:
    """比背景亮但没「点亮」的灰圆点（例如选中态的淡色）不算专精。"""
    levels = mastery_levels_from_image(make_screen(pip_value=120))
    assert levels == {1: 0, 2: 0, 3: 0}


def test_partially_covered_box_still_counts_when_the_pip_is_bright() -> None:
    """取样框是 7x7 的死框，真机上多少有点偏：最亮的那部分像素说了算。"""
    image = make_screen(lit={1: ("Top",)})
    pixels = image.load()
    left, top, right, bottom = scale_box(st.MASTER_PIP_ROIS[(1, "Top")], 2.0)
    # 只把框里最上面四分之一涂白，其余保持暗——真机就是这样（框略偏）。
    for x in range(left, right):
        for y in range(top, top + max(1, (bottom - top) // 4)):
            pixels[x, y] = BRIGHT_PIP
    assert mastery_levels_from_image(image)[1] == 1


def test_pip_positions_cover_three_slots_and_three_positions() -> None:
    assert sorted({slot for slot, _position in st.MASTER_PIP_ROIS}) == [1, 2, 3]
    assert set(st.MASTER_PIP_POSITIONS) == {"Top", "BottomRight", "BottomLeft"}
    assert len(st.MASTER_PIP_ROIS) == 9


# ---------------------------------------------------------------------------
# 前后两帧比像素（M39 §4.2）：详情页左滑有没有换人，只看这一条判据
# ---------------------------------------------------------------------------
def _solid(value: int, *, size: tuple[int, int] = (640, 360)) -> Image.Image:
    return Image.new("L", size, value)


def test_frame_delta_measures_the_mean_grey_difference() -> None:
    """全黑 vs 全白：平均差 255（真机「划得动」是 24–48，「划不动」是 0.02）。"""
    delta = scan_image.frame_delta(_solid(0), _solid(255))

    assert delta == pytest.approx(255.0)
    assert scan_image.frame_changed(_solid(0), _solid(255)) is True


def test_an_identical_frame_counts_as_not_moved() -> None:
    before = make_screen(lit={1: ("left",)})

    assert scan_image.frame_delta(before, before.copy()) == pytest.approx(0.0)
    assert scan_image.frame_changed(before, before.copy()) is False


def test_noise_stays_below_the_movement_threshold() -> None:
    """噪声级的差别（一个灰阶）不算「换人了」：阈值取 2.0，比噪声高两个数量级."""
    before = _solid(100)
    after = _solid(101)

    assert scan_image.frame_delta(before, after) == pytest.approx(1.0)
    assert scan_image.frame_changed(before, after) is False
    assert scan_image.FRAME_CHANGED_MIN_DELTA == pytest.approx(2.0)


def test_a_missing_or_mismatched_frame_is_unknown() -> None:
    """拿不到截图、或两张尺寸不一样：回 ``None``（调用方按「没动」处理）。"""
    screen = _solid(10)

    assert scan_image.frame_delta(None, screen) is None
    assert scan_image.frame_delta(screen, None) is None
    assert scan_image.frame_delta(None, None) is None
    assert scan_image.frame_delta(screen, _solid(10, size=(320, 180))) is None
    assert scan_image.frame_changed(None, screen) is None
    assert scan_image.frame_changed(screen, _solid(10, size=(320, 180))) is None


def test_colour_screenshots_are_compared_in_grey() -> None:
    """真机截图是 RGB（``get_image`` 存下来的 PNG），比的是灰度，不是某个通道."""
    before = Image.new("RGB", (16, 16), (0, 0, 0))
    after = Image.new("RGB", (16, 16), (255, 255, 255))

    assert scan_image.frame_delta(before, after) == pytest.approx(255.0)
