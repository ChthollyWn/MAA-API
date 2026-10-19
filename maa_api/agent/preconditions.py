"""M32 任务执行前提护栏（规格 §4.1）：纯函数、无 I/O，供工具层与策略层共用。

MaaCore 的每个任务第一步都匹配主界面标志，**只有 ``StartUp`` 会拉起客户端**：
游戏没启动、或设备停在模拟器桌面／登录页时，提交任何游戏内任务都只会反复重试
到失败（M32 的用户会话实测代价是一次 200 秒量级的空跑加重试）。本模块只做
「判定 + 固定文案」，不拦截提交、不自动补任务、不改用户已确认的内容——用户裁决
是「提醒，不拦」（规格 §1）。M58 起 ``wait_pipeline`` 超时也用同一套固定文案，
把「先看画面再决定续等」写成提示（docs/13 §8.81）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from maa_api.domain.errors import ErrorCode
from maa_api.domain.task import TASK_MODELS

__all__ = [
    "FAILURE_HINT",
    "GAME_RUNNING_TASK_TYPES",
    "MISSING_START_UP_WARNING",
    "WAIT_TIMEOUT_HINT",
    "failure_hint",
    "missing_start_up_warning",
    "needs_running_game",
    "task_type_names",
    "wait_timeout_hint",
]

#: 需要「游戏已启动并停在主界面」的任务类型＝ ``TASK_MODELS`` 的全部键减去两个
#: 例外：``StartUp`` 是解法本身，``CloseDown`` 直接关客户端、不需要停在某一页。
#: 现算而不手抄任务名：内核清单变化时这里自动跟上。
GAME_RUNNING_TASK_TYPES: frozenset[str] = frozenset(TASK_MODELS) - {
    "StartUp",
    "CloseDown",
}

#: 有游戏内任务却没有 ``StartUp`` 时的提交提醒（单一来源，测试逐字断言）。
MISSING_START_UP_WARNING = (
    "流水线里没有 StartUp：内核的任务都从主界面起手，"
    "游戏没启动或停在桌面时只会反复重试到失败。"
    "游戏状态不确定时，把 StartUp（start_game_enabled: true）放在第一条，"
    "或先让用户确认游戏已启动。"
)

#: 流水线因 ``CORE_COMMAND_FAILED`` 失败时的处置提示（单一来源，测试逐字断言）。
FAILURE_HINT = (
    "这个任务需要游戏已启动并停在主界面：先看现场截图，"
    "如果画面是设备桌面或登录页，先补一条 StartUp（start_game_enabled: true）再重跑，"
    "不要原样重试。"
)

#: ``wait_pipeline`` 等待超时（流水线仍在跑）时的处置提示（M58，docs/13 §8.81）。
#: 2026-10-20 的会话里模型把单次等待设成 5 分钟，等待期间没有任何输出；超时后它自己
#: 截图才发现游戏根本没起来。把「先看画面」写成固定提示，不依赖模型自觉。
WAIT_TIMEOUT_HINT = (
    "等待超时，流水线还没结束。先 get_screenshot 看现场画面："
    "画面显示任务确实在跑就续等（timeout_seconds 可以加大，最多 600）；"
    "画面是设备桌面、登录页或与任务无关时，先 stop_pipeline 停车再排查，"
    "不要原样反复等待。"
)

#: `StartUp` 的小写形式：判定一律大小写不敏感（规格 §4.1）。
_START_UP_NAME = "startup"
_GAME_RUNNING_TASK_NAMES = frozenset(name.lower() for name in GAME_RUNNING_TASK_TYPES)


def task_type_names(tasks: Any) -> list[str]:
    """取出 ``PipelineCreate.tasks`` 这类结构里每条任务的 ``name``。

    大小写不敏感、去空白；条目可能是 dict、pydantic／SQLModel 模型，取 ``name``
    时两种都成立。缺 ``name``、``name`` 不是字符串、或整体不是任务列表的条目
    **跳过**——宁可漏报，也不凭猜测报警（规格 §4.1）。
    """
    if tasks is None or isinstance(tasks, (str, bytes, Mapping)):
        # 整个参数体（``{"tasks": [...]}``）不是任务列表：不猜它的形状。
        return []
    try:
        items = list(tasks)
    except TypeError:
        return []
    names: list[str] = []
    for item in items:
        name = _task_name(item)
        if name is not None:
            names.append(name)
    return names


def needs_running_game(tasks: Any) -> bool:
    """任一条任务命中 :data:`GAME_RUNNING_TASK_TYPES` 即为真。"""
    return any(
        name.lower() in _GAME_RUNNING_TASK_NAMES for name in task_type_names(tasks)
    )


def missing_start_up_warning(tasks: Any) -> str | None:
    """有游戏内任务、整条流水线却没有 ``StartUp`` 时返回提醒，否则 ``None``。"""
    names = [name.lower() for name in task_type_names(tasks)]
    if not any(name in _GAME_RUNNING_TASK_NAMES for name in names):
        return None
    if _START_UP_NAME in names:
        return None
    return MISSING_START_UP_WARNING


def failure_hint(error_code: str | None) -> str | None:
    """``CORE_COMMAND_FAILED`` 才给处置提示；其它错误码与缺失一律 ``None``。"""
    if error_code == ErrorCode.CORE_COMMAND_FAILED:
        return FAILURE_HINT
    return None


def wait_timeout_hint(finished: bool) -> str | None:
    """等待超时（``finished=False``）才给处置提示；终态一律 ``None``。"""
    return None if finished else WAIT_TIMEOUT_HINT


def _task_name(item: Any) -> str | None:
    """单条任务的 ``name``；不是映射／模型或没有可用名字时返回 ``None``。"""
    raw = item.get("name") if isinstance(item, Mapping) else getattr(item, "name", None)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None
