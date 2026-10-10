"""外部来源的运行状态（规格 §3.2、§5.11）：供设置页与 `GET /api/sources/status`。

只记「什么时候成功、什么时候失败、为什么、冷却到什么时候、命中多少」，不记响应正文。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from maa_api.services.external.sources import SOURCES

__all__ = ["SourceRuntimeStatus", "SourceStatusRegistry"]

#: 连续失败到这个次数就暂停该来源（规格 §3.2、S2）。
BREAKER_THRESHOLD = 3

#: 熔断暂停时长（秒）。
BREAKER_SECONDS = 60.0


def _iso(moment: float | None) -> str | None:
    """单调转墙上时间；``None`` 原样返回。"""
    if moment is None:
        return None
    return datetime.fromtimestamp(moment).astimezone().isoformat()


@dataclass
class SourceRuntimeStatus:
    """一个来源的运行时状态（可变，由访问层更新）。"""

    kind: str
    label: str
    last_success_at: float | None = None
    last_error_at: float | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    consecutive_failures: int = 0
    cooldown_until: float | None = None
    requests: int = 0
    bytes_read: int = 0
    retries: int = 0

    def as_dict(self, *, now: float, cache_stats: dict[str, Any]) -> dict[str, Any]:
        """给 REST 与设置页的形状；``cooldown_seconds`` 是剩余秒数，直接可用。"""
        remaining = (
            max(int(self.cooldown_until - now), 0) if self.cooldown_until else 0
        )
        return {
            "kind": self.kind,
            "label": self.label,
            "last_success_at": _iso(self.last_success_at),
            "last_error_at": _iso(self.last_error_at),
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_until": _iso(self.cooldown_until) if remaining else None,
            "cooldown_seconds": remaining,
            "requests": self.requests,
            "bytes_read": self.bytes_read,
            "retries": self.retries,
            "cache": cache_stats,
        }


@dataclass
class SourceStatusRegistry:
    """全部来源的状态表；访问层直接更新它，REST 与设置页读快照。"""

    clock: Callable[[], float] = time.time
    _statuses: dict[str, SourceRuntimeStatus] = field(default_factory=dict)

    def status(self, kind: str) -> SourceRuntimeStatus:
        """取（必要时创建）某个来源的状态。"""
        status = self._statuses.get(kind)
        if status is None:
            spec = SOURCES.get(kind)
            status = SourceRuntimeStatus(
                kind=kind, label=spec.label if spec is not None else kind
            )
            self._statuses[kind] = status
        return status

    # -- 冷却与熔断 --------------------------------------------------------
    def cooldown_remaining(self, kind: str) -> int:
        """该来源还在冷却时返回剩余秒数，否则 0。"""
        status = self.status(kind)
        if status.cooldown_until is None:
            return 0
        remaining = int(status.cooldown_until - self.clock())
        if remaining <= 0:
            status.cooldown_until = None
            return 0
        return remaining

    def open_cooldown(self, kind: str, seconds: float) -> None:
        """进入冷却（429 的 `Retry-After` 或熔断）。"""
        status = self.status(kind)
        until = self.clock() + max(seconds, 0)
        if status.cooldown_until is None or until > status.cooldown_until:
            status.cooldown_until = until

    # -- 记录 -------------------------------------------------------------
    def record_success(self, kind: str, *, bytes_read: int = 0, retries: int = 0) -> None:
        status = self.status(kind)
        status.last_success_at = self.clock()
        status.consecutive_failures = 0
        status.cooldown_until = None
        status.requests += 1
        status.bytes_read += max(int(bytes_read), 0)
        status.retries += max(int(retries), 0)

    def record_failure(
        self,
        kind: str,
        *,
        code: str,
        message: str,
        retries: int = 0,
    ) -> None:
        """记一次失败；连续失败到阈值就熔断 60 秒（429 的冷却由调用方另行设置）。"""
        status = self.status(kind)
        status.last_error_at = self.clock()
        status.last_error_code = code
        status.last_error_message = message
        status.consecutive_failures += 1
        status.requests += 1
        status.retries += max(int(retries), 0)
        if status.consecutive_failures >= BREAKER_THRESHOLD:
            self.open_cooldown(kind, BREAKER_SECONDS)

    # -- 快照 -------------------------------------------------------------
    def snapshot(self, cache_stats: Callable[[str], dict[str, Any]]) -> list[dict[str, Any]]:
        """全部来源的状态快照，按登记顺序（PRTS 在前）。"""
        now = self.clock()
        return [
            self.status(kind).as_dict(now=now, cache_stats=cache_stats(kind))
            for kind in SOURCES
        ]

    def reset(self) -> None:
        """清空状态（测试与「清空缓存」后的重来用）。"""
        self._statuses.clear()
