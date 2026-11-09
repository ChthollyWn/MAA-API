"""示教步骤 → 自定义任务链的编译器（M89 spec §4.5 编译）。

每个步骤编译成一个 ``JustReturn`` 动作节点：

- ``click`` → ``ClickRect``，``specificRect`` 是 10×10 的小矩形；
- ``swipe`` → ``Swipe``，``specificRect`` / ``rectMove`` 都是 100×100；
- ``long_press`` 没有独立 action → 起终点相同的 ``Swipe``；
- 链尾固定追加 ``<prefix>_Done`` 的 ``Stop`` 节点。

坐标换算与 :meth:`DeviceManager.click` 同一规则（``round(px * 720 / height)``），
再夹到 MaaCore 的 1280×720 基准里；``postDelay`` 取下一步的 ``delay_ms``，最后一步
固定 800。编译器只做纯计算，不碰库、不碰文件。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Compiled",
    "CompileError",
    "Step",
    "compile_steps",
    "parse_step",
    "scale_point",
]

#: MaaCore 的 16:9 基准高度；与 ``DeviceManager.click`` 的换算一致。
_MAACORE_HEIGHT = 720
#: 基准分辨率的边界（spec §4.5 第 2、3 条）。
_SCREEN_WIDTH = 1280
_SCREEN_HEIGHT = 720
_CLICK_MAX_X = 1270
_CLICK_MAX_Y = 710
_SWIPE_MAX_X = 1180
_SWIPE_MAX_Y = 620
_CLICK_RECT_SIZE = 10
_SWIPE_RECT_SIZE = 100
#: 动作节点的默认时长（录制值缺省时）与夹取范围。
_DEFAULT_SWIPE_DURATION_MS = 300
_DEFAULT_LONG_PRESS_DURATION_MS = 600
_SWIPE_MIN_DURATION_MS = 100
_LONG_PRESS_MIN_DURATION_MS = 600
_MAX_DURATION_MS = 5000
#: 最后一步的固定后摇（spec §4.5 第 6 条）。
_LAST_POST_DELAY = 800
_MAX_STEP_DELAY_MS = 10_000
_MAX_SLUG_LENGTH = 40
_DEFAULT_SLUG = "Demo"
_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True, slots=True)
class Step:
    """一条扁平的示教步骤（``demonstration.steps`` 的元素，spec §4.1）。"""

    action: str
    x: int | None = None
    y: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = None
    delay_ms: int = 0
    screen: tuple[int, int] = (0, 0)


@dataclass(frozen=True, slots=True)
class Compiled:
    """一条编译好的任务链：入口名、全部节点与从入口到 ``Stop`` 的路径。"""

    entry: str
    tasks: dict[str, dict[str, Any]]
    chain: tuple[str, ...]


class CompileError(ValueError):
    """步骤无法编译；``errors`` 是 ``{"field", "reason"}`` 列表。"""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        detail = "；".join(
            f"{error.get('field')}: {error.get('reason')}" for error in errors
        )
        super().__init__(detail or "示教步骤无法编译")


def parse_step(raw: Mapping[str, Any]) -> Step:
    """把 ``demonstration.steps`` 里的扁平对象转成 :class:`Step`。

    缺省的整数字段取 ``None``；``delay_ms`` 缺省为 0；``screen`` 转成二元组，
    形状不对时按 ``(0, 0)`` 处理，由 :func:`compile_steps` 统一报错。
    """
    screen = raw.get("screen")
    if isinstance(screen, (list, tuple)) and len(screen) == 2:
        width, height = screen
        try:
            screen_pair = (int(width or 0), int(height or 0))
        except (TypeError, ValueError):
            screen_pair = (0, 0)
    else:
        screen_pair = (0, 0)
    return Step(
        action=str(raw.get("action") or ""),
        x=_optional_int(raw.get("x")),
        y=_optional_int(raw.get("y")),
        x1=_optional_int(raw.get("x1")),
        y1=_optional_int(raw.get("y1")),
        x2=_optional_int(raw.get("x2")),
        y2=_optional_int(raw.get("y2")),
        duration_ms=_optional_int(raw.get("duration_ms")),
        delay_ms=_optional_int(raw.get("delay_ms")) or 0,
        screen=screen_pair,
    )


def scale_point(value: int, height: int, maximum: int) -> int:
    """设备像素换到 MaaCore 基准并夹到 ``0..maximum``（spec §4.5 第 2 条）。"""
    if height <= 0:
        raise CompileError(
            [{"field": "screen", "reason": "录制分辨率的高度必须大于 0"}]
        )
    scaled = round(int(value) * _MAACORE_HEIGHT / height)
    return max(0, min(scaled, maximum))


def compile_steps(*, prefix: str, steps: Sequence[Step]) -> Compiled:
    """把步骤编译成 ``Custom_*`` 任务链；非法输入抛 :class:`CompileError`。"""
    errors: list[dict[str, str]] = []
    if not steps:
        raise CompileError([{"field": "steps", "reason": "至少需要一步示教步骤"}])
    for index, step in enumerate(steps):
        errors.extend(_step_errors(index, step))
    if errors:
        raise CompileError(errors)

    slug = _slug(prefix)
    action_names = [f"Custom_{slug}_{index}" for index in range(1, len(steps) + 1)]
    done_name = f"Custom_{slug}_Done"
    tasks: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(steps):
        next_name = (
            action_names[index + 1]
            if index + 1 < len(action_names)
            else done_name
        )
        tasks[action_names[index]] = _action_node(
            step,
            next_name=next_name,
            post_delay=_post_delay(steps, index),
        )
    tasks[done_name] = {"algorithm": "JustReturn", "action": "Stop"}
    return Compiled(
        entry=action_names[0],
        tasks=tasks,
        chain=tuple(action_names) + (done_name,),
    )


# --------------------------------------------------------------------------- 内部


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _step_errors(index: int, step: Step) -> list[dict[str, str]]:
    """校验单个步骤的屏幕与坐标；任何违规都让编译整体失败。"""
    label = f"steps[{index}]"
    width, height = step.screen
    if width <= 0 or height <= 0:
        return [
            {
                "field": "screen",
                "reason": f"{label} 缺少有效的录制分辨率（width、height 必须大于 0）",
            }
        ]
    if step.action == "click":
        points = (("x", step.x, "y", step.y),)
    elif step.action == "swipe":
        points = (
            ("x1", step.x1, "y1", step.y1),
            ("x2", step.x2, "y2", step.y2),
        )
    elif step.action == "long_press":
        points = (("x", step.x, "y", step.y),)
    else:
        return [
            {
                "field": "action",
                "reason": f"{label} 的动作 {step.action!r} 不能编译成自定义任务",
            }
        ]

    errors: list[dict[str, str]] = []
    for x_field, x_value, y_field, y_value in points:
        if x_value is None or y_value is None:
            errors.append(
                {
                    "field": x_field,
                    "reason": f"{label} 缺少坐标 {x_field}/{y_field}",
                }
            )
        elif not (0 <= x_value < width and 0 <= y_value < height):
            errors.append(
                {
                    "field": x_field,
                    "reason": (
                        f"{label} 的坐标 ({x_value}, {y_value}) 超出录制分辨率 "
                        f"{width}×{height}"
                    ),
                }
            )
    return errors


def _action_node(
    step: Step, *, next_name: str, post_delay: int
) -> dict[str, Any]:
    height = step.screen[1]
    if step.action == "click":
        x = min(scale_point(step.x or 0, height, _SCREEN_WIDTH - 1), _CLICK_MAX_X)
        y = min(scale_point(step.y or 0, height, _SCREEN_HEIGHT - 1), _CLICK_MAX_Y)
        return {
            "algorithm": "JustReturn",
            "action": "ClickRect",
            "specificRect": [x, y, _CLICK_RECT_SIZE, _CLICK_RECT_SIZE],
            "postDelay": post_delay,
            "next": [next_name],
        }
    if step.action == "long_press":
        x1 = min(scale_point(step.x or 0, height, _SCREEN_WIDTH - 1), _SWIPE_MAX_X)
        y1 = min(scale_point(step.y or 0, height, _SCREEN_HEIGHT - 1), _SWIPE_MAX_Y)
        x2, y2 = x1, y1
        duration = _clamp(
            step.duration_ms,
            _LONG_PRESS_MIN_DURATION_MS,
            _MAX_DURATION_MS,
            default=_DEFAULT_LONG_PRESS_DURATION_MS,
        )
    else:
        x1 = min(scale_point(step.x1 or 0, height, _SCREEN_WIDTH - 1), _SWIPE_MAX_X)
        y1 = min(scale_point(step.y1 or 0, height, _SCREEN_HEIGHT - 1), _SWIPE_MAX_Y)
        x2 = min(scale_point(step.x2 or 0, height, _SCREEN_WIDTH - 1), _SWIPE_MAX_X)
        y2 = min(scale_point(step.y2 or 0, height, _SCREEN_HEIGHT - 1), _SWIPE_MAX_Y)
        duration = _clamp(
            step.duration_ms,
            _SWIPE_MIN_DURATION_MS,
            _MAX_DURATION_MS,
            default=_DEFAULT_SWIPE_DURATION_MS,
        )
    return {
        "algorithm": "JustReturn",
        "action": "Swipe",
        "specificRect": [x1, y1, _SWIPE_RECT_SIZE, _SWIPE_RECT_SIZE],
        "rectMove": [x2, y2, _SWIPE_RECT_SIZE, _SWIPE_RECT_SIZE],
        "specialParams": [duration, 1, 37, 1],
        "postDelay": post_delay,
        "next": [next_name],
    }


def _post_delay(steps: Sequence[Step], index: int) -> int:
    """下一步的 ``delay_ms``；最后一步固定 800。"""
    if index + 1 >= len(steps):
        return _LAST_POST_DELAY
    return max(0, min(int(steps[index + 1].delay_ms), _MAX_STEP_DELAY_MS))


def _clamp(
    value: int | None, low: int, high: int, *, default: int
) -> int:
    resolved = default if value is None else int(value)
    return max(low, min(resolved, high))


def _slug(prefix: str) -> str:
    """spec §4.5 第 8 条：去 ``Custom_``、非白名单字符换 ``_``、截 40、空则 ``Demo``。

    清理后首字符可能不是字母或数字；节点名必须匹配 ``NAME_PATTERN``，此时补
    ``Demo_`` 前缀并重新截断。
    """
    base = prefix.removeprefix("Custom_")
    slug = _SLUG_PATTERN.sub("_", base)[:_MAX_SLUG_LENGTH]
    if not slug:
        return _DEFAULT_SLUG
    if not slug[0].isalnum():
        slug = f"{_DEFAULT_SLUG}_{slug}"[:_MAX_SLUG_LENGTH]
    return slug
