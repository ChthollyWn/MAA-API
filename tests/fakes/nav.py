"""M86 导航图测试用的假图、假视觉与假设备（spec §5 T2/T4）。

``make_screen(name)`` 用 ``sha256(name)`` 决定一张 1280×720 假截图上 6 个 120×120
色块的位置与颜色：不同名字的图不仅颜色不同，dHash 的梯度位置也不同，因此既能
当「不同界面」，也能在 ±3 灰度噪声下保持稳定。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from PIL import Image

SCREEN_SIZE = (1280, 720)
BACKGROUND = (128, 128, 128)
BLOCK_SIZE = 120
BLOCK_COUNT = 6
GRID_COLUMNS = 4
GRID_ROWS = 3


def make_screen(name: str) -> Image.Image:
    """按名字生成一张确定的 1280×720 假截图。

    6 个色块各占 4×3 候选网格里一个互不相同的格子；摘要字节依次决定格子、
    格内偏移与 RGB。6 块 × 6 字节超过 sha256 的 32 字节，字节循环取用。
    """
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    data = digest * 2
    image = Image.new("RGB", SCREEN_SIZE, BACKGROUND)
    cell_width = SCREEN_SIZE[0] // GRID_COLUMNS
    cell_height = SCREEN_SIZE[1] // GRID_ROWS
    used: set[int] = set()
    cursor = 0
    for _ in range(BLOCK_COUNT):
        cell = data[cursor] % (GRID_COLUMNS * GRID_ROWS)
        cursor += 1
        while cell in used:
            cell = (cell + 1) % (GRID_COLUMNS * GRID_ROWS)
        used.add(cell)
        column, row = cell % GRID_COLUMNS, cell // GRID_COLUMNS
        offset_x = data[cursor] % (cell_width - BLOCK_SIZE + 1)
        cursor += 1
        offset_y = data[cursor] % (cell_height - BLOCK_SIZE + 1)
        cursor += 1
        color = (data[cursor], data[cursor + 1], data[cursor + 2])
        cursor += 3
        left = column * cell_width + offset_x
        top = row * cell_height + offset_y
        image.paste(color, (left, top, left + BLOCK_SIZE, top + BLOCK_SIZE))
    return image


class FakeVision:
    """可预设返回值的视觉客户端替身；记录调用次数与入参。"""

    def __init__(
        self,
        *,
        classify_result: tuple[str | None, float, str] | None = None,
        locate_result: tuple[float, float, float] | None = None,
        available_result: bool = True,
    ) -> None:
        self.classify_result = classify_result
        self.locate_result = locate_result
        self.available_result = available_result
        self.classify_calls = 0
        self.locate_calls = 0
        self.available_calls = 0
        self.classify_candidates: list[Any] = []
        self.locate_queries: list[str] = []

    async def available(self) -> bool:
        self.available_calls += 1
        return self.available_result

    async def classify(
        self, image: Image.Image, candidates: Sequence[Any]
    ) -> tuple[str | None, float, str] | None:
        self.classify_calls += 1
        self.classify_candidates.append(list(candidates))
        return self.classify_result

    async def locate(
        self, image: Image.Image, query: str
    ) -> tuple[float, float, float] | None:
        self.locate_calls += 1
        self.locate_queries.append(query)
        return self.locate_result


#: ``unknown_once`` 时返回的图；任何种子/学习节点都不应匹配它。
UNKNOWN_SCREEN = "__unknown__"


class FakeNavDevice:
    """假导航设备（M86 spec §5 T4）：按动作键在界面上跳转。

    动作键是 ``tap:x,y``（设备像素）、``swipe:x1,y1,x2,y2,duration``、
    ``key:BACK`` 与 ``home``；命中 ``transitions`` 时把当前界面改成目标界面，未登记
    的动作停在原地。``unknown_once`` 只影响下一次 ``screenshot()``：返回一张认不出
    的图，随后自动复位。
    """

    def __init__(
        self,
        transitions: dict[str, str] | None = None,
        start: str = "home",
        home_result: bool = True,
        resolution: tuple[int, int] = (1280, 720),
    ) -> None:
        self.transitions = dict(transitions or {})
        self.screen = start
        self.home_result = home_result
        self.unknown_once = False
        self._resolution = resolution
        self.calls: list[tuple[Any, ...]] = []

    async def screenshot(self) -> Image.Image:
        self.calls.append(("screenshot",))
        if self.unknown_once:
            self.unknown_once = False
            return make_screen(UNKNOWN_SCREEN)
        return make_screen(self.screen)

    async def resolution(self) -> tuple[int, int]:
        return self._resolution

    def _act(self, key: str) -> None:
        self.calls.append((key,))
        self.screen = self.transitions.get(key, self.screen)

    async def tap(self, x: int, y: int) -> None:
        self._act(f"tap:{x},{y}")

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int
    ) -> None:
        self._act(f"swipe:{x1},{y1},{x2},{y2},{duration_ms}")

    async def key(self, key: str) -> None:
        self._act(f"key:{key}")

    async def home(self) -> bool:
        self.calls.append(("home",))
        self.screen = "home"
        return self.home_result
