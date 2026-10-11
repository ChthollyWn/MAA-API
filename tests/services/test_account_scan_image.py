"""专精像素读数（M36 规格 §4.4）：亮圆点计数、阈值、缩放。

全部离线：用例自己造截图（深色底 + 亮/暗圆点），
读数逻辑在 :mod:`maa_api.services.account.scan_image` 里。
"""

from __future__ import annotations

from PIL import Image

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
