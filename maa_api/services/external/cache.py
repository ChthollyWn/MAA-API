"""外部内容的进程内缓存（规格 §3.3）：TTL + LRU，不落盘，进程重启清空。

键是（来源，端点，规范化后的参数），值是解析后的 JSON。字节数按
``json.dumps`` 的长度估算——不精确，但上限只是防一条大响应把内存吃光，不需要精确。
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "STALE_MAX_SECONDS",
    "CacheEntry",
    "CacheHit",
    "CacheStore",
    "cache_key",
]

#: 过期后还能当旧值回退的时长（规格 §3.3、S26）：超过 24 小时的回退等于给用户
#: 看一份过期的当期数据，不如如实报「取不到」。
STALE_MAX_SECONDS = 24 * 60 * 60


def cache_key(kind: str, endpoint: str, params: Mapping[str, str]) -> str:
    """规范化缓存键：参数按名字排序后拼 JSON，保证同一次请求总是同一个键。"""
    normalized = json.dumps(
        {key: params[key] for key in sorted(params)}, ensure_ascii=False, sort_keys=True
    )
    return f"{kind}\x1f{endpoint}\x1f{normalized}"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """一条缓存：值、写入时刻、过期时刻与估算字节数。"""

    key: str
    kind: str
    value: Any
    stored_at: float
    expires_at: float
    size: int

    def fetched_at(self) -> datetime:
        return datetime.fromtimestamp(self.stored_at).astimezone()

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at

    def is_usable_stale(self, now: float) -> bool:
        """过期但还在 24 小时回退窗口内。"""
        return now < self.stored_at + STALE_MAX_SECONDS


@dataclass(frozen=True, slots=True)
class CacheHit:
    """命中结果：值 + 是否旧值。``entry`` 供调用方取原抓取时间。"""

    entry: CacheEntry
    stale: bool

    @property
    def value(self) -> Any:
        return self.entry.value


class CacheStore:
    """TTL + LRU 缓存；同键并发合并由 :class:`~maa_api.services.external.http.ExternalClient` 负责。"""

    def __init__(
        self,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_bytes = max(int(max_bytes), 1)
        self._clock = clock
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._bytes = 0
        # 命中/未命中按 kind 统计，供设置页显示命中率。
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}
        self._evictions = 0

    # -- 读 ---------------------------------------------------------------
    def get(self, key: str, *, allow_stale: bool = False) -> CacheHit | None:
        """取一条缓存；``allow_stale`` 时把 24 小时内的过期值也算命中。"""
        entry = self._entries.get(key)
        if entry is None:
            kind = _kind_of(key)
            self._misses[kind] = self._misses.get(kind, 0) + 1
            return None
        now = self._clock()
        if entry.is_fresh(now):
            self._entries.move_to_end(key)
            self._hits[entry.kind] = self._hits.get(entry.kind, 0) + 1
            return CacheHit(entry=entry, stale=False)
        if allow_stale and entry.is_usable_stale(now):
            self._entries.move_to_end(key)
            self._hits[entry.kind] = self._hits.get(entry.kind, 0) + 1
            return CacheHit(entry=entry, stale=True)
        # 过期且不能回退：只记未命中，条目留着（可能还有别的键在用同一份内存预算）。
        self._misses[entry.kind] = self._misses.get(entry.kind, 0) + 1
        return None

    def peek(self, key: str) -> CacheEntry | None:
        """不看 TTL 直接取条目（测试与状态用）。"""
        return self._entries.get(key)

    # -- 写 ---------------------------------------------------------------
    def put(self, key: str, kind: str, value: Any, *, ttl: int) -> CacheEntry:
        """写入并淘汰超限的旧条目；同键覆盖。"""
        now = self._clock()
        size = _estimate_size(value)
        entry = CacheEntry(
            key=key,
            kind=kind,
            value=value,
            stored_at=now,
            expires_at=now + max(int(ttl), 0),
            size=size,
        )
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= previous.size
            # 覆盖写不等于命中也不算未命中，命中率只统计读路径；用 peek 时才加。
        self._entries[key] = entry
        self._bytes += size
        self._evict()
        return entry

    def _evict(self) -> None:
        """按 LRU 淘汰到上限以内；超限时连最新写入的那条也可能被淘汰。"""
        while self._bytes > self.max_bytes and self._entries:
            _, dropped = self._entries.popitem(last=False)
            self._bytes -= dropped.size
            self._evictions += 1

    def clear(self, kind: str | None = None) -> int:
        """清空全部或单个来源的缓存，返回清掉的条数。"""
        if kind is None:
            count = len(self._entries)
            self._entries.clear()
            self._bytes = 0
            return count
        keys = [key for key, entry in self._entries.items() if entry.kind == kind]
        for key in keys:
            dropped = self._entries.pop(key)
            self._bytes -= dropped.size
        return len(keys)

    # -- 状态 -------------------------------------------------------------
    def stats(self, kind: str | None = None) -> dict[str, Any]:
        """条数、字节数与命中率的快照；``kind`` 为空时给全部来源的合计。"""
        if kind is None:
            entries = list(self._entries.values())
            hits = sum(self._hits.values())
            misses = sum(self._misses.values())
        else:
            entries = [entry for entry in self._entries.values() if entry.kind == kind]
            hits = self._hits.get(kind, 0)
            misses = self._misses.get(kind, 0)
        total = hits + misses
        return {
            "entries": len(entries),
            "bytes": sum(entry.size for entry in entries),
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else None,
            "evictions": self._evictions,
        }

    def entries_of(self, kind: str | None = None) -> Iterable[CacheEntry]:
        """遍历缓存条目（测试与状态用）。"""
        for entry in self._entries.values():
            if kind is None or entry.kind == kind:
                yield entry


def _estimate_size(value: Any) -> int:
    """值的估算字节数：JSON 序列化长度；序列化不了就按 1 KB 记。"""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 1024


def _kind_of(key: str) -> str:
    """缓存键里的 kind 段（``kind\\x1fendpoint\\x1fparams``）。"""
    return key.split("\x1f", 1)[0]


#: 旧值回退窗口的边界，供测试引用（`timedelta` 表达更直观）。
STALE_WINDOW = timedelta(seconds=STALE_MAX_SECONDS)
