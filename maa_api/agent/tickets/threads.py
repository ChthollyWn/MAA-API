"""话题键的格式、解析与来源前缀（M69 规格 §4.2、v3 03 §15）。

``ThreadRouter`` 在 M69 T5 加进本模块；这里先放路由需要的纯函数与数据形状。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from maa_api.domain.enums import EventSource
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "MAX_THREAD_KEY_CHARS",
    "SOURCE_PREFIXES",
    "THREAD_KEY_PATTERNS",
    "RouteResult",
    "ThreadKey",
    "parse_thread_key",
]

MAX_THREAD_KEY_CHARS = 160
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

#: 十种话题键前缀的完整格式（03 §15）。
THREAD_KEY_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "chat": re.compile(r"^chat:orchestrator$"),
    "ticket": re.compile(rf"^ticket:{_UUID}$"),
    "pipeline": re.compile(rf"^pipeline:{_UUID}$"),
    "core": re.compile(r"^core:[a-z0-9][a-z0-9_-]{0,31}$"),
    "device": re.compile(r"^device:[a-z0-9][a-z0-9_-]{0,31}$"),
    "schedule": re.compile(r"^schedule:[a-z0-9][a-z0-9_-]{0,47}$"),
    "system": re.compile(r"^system:[a-z][a-z0-9_]{0,63}$"),
    "im": re.compile(
        r"^im:(telegram|feishu|dingtalk|qq):[A-Za-z0-9_@.:=+/-]{1,128}$"
    ),
    "goal": re.compile(rf"^goal:{_UUID}$"),
    "watchdog": re.compile(r"^watchdog:[a-z][a-z0-9_]{0,47}$"),
}
#: 03 §15 的来源与前缀对应，M81 用它校验事件。
SOURCE_PREFIXES: Mapping[EventSource, frozenset[str]] = {
    EventSource.WEB: frozenset({"chat", "ticket"}),
    EventSource.IM_TELEGRAM: frozenset({"im"}),
    EventSource.IM_FEISHU: frozenset({"im"}),
    EventSource.IM_DINGTALK: frozenset({"im"}),
    EventSource.IM_QQ: frozenset({"im"}),
    EventSource.PIPELINE: frozenset({"pipeline"}),
    EventSource.CORE: frozenset({"core"}),
    EventSource.DEVICE: frozenset({"device"}),
    EventSource.SCHEDULE: frozenset({"schedule"}),
    EventSource.SYSTEM: frozenset({"system"}),
    EventSource.WATCHDOG: frozenset({"watchdog"}),
    EventSource.GOAL: frozenset({"goal"}),
}


@dataclass(frozen=True, slots=True)
class ThreadKey:
    """解析后的话题键；``value`` 是第一个冒号之后的部分。"""

    raw: str
    prefix: str
    value: str


@dataclass(frozen=True, slots=True)
class RouteResult:
    """一次 ``ThreadRouter.publish`` 的落点（T5 使用）。"""

    routed_to: Literal["ticket", "orchestrator", "none"]
    inbox_ids: tuple[int, ...]
    ticket_ids: tuple[str, ...]


def parse_thread_key(key: str) -> ThreadKey:
    """长度超过 160、前缀未知或格式不符 → ``INVALID_PARAMETER``。"""
    if not isinstance(key, str) or len(key) > MAX_THREAD_KEY_CHARS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "话题键格式不对",
            {"thread_key": key},
        )
    prefix, separator, value = key.partition(":")
    pattern = THREAD_KEY_PATTERNS.get(prefix)
    if not separator or pattern is None or pattern.fullmatch(key) is None:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "话题键格式不对",
            {"thread_key": key},
        )
    return ThreadKey(raw=key, prefix=prefix, value=value)
