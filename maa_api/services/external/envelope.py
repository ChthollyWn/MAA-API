"""来源信封（规格 §3.4）：所有依赖外部来源或账号数据的工具都在顶层带这几项。

``untrusted: true`` 是给模型看的：这些内容是外部页面数据，不是指令（规格 §4.1）。
``url`` 只指向白名单主机，前端只为这些主机渲染链接（附录 D）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlsplit

__all__ = [
    "EMPTY_ENVELOPE",
    "LINK_HOSTS",
    "SourceRef",
    "build_envelope",
    "is_link_allowed",
    "merge_envelopes",
]

#: 前端允许生成外链的主机（附录 D 最后一行）。
LINK_HOSTS = (
    "prts.wiki",
    "prts.plus",
    "zoot.plus",
    "penguin-stats.io",
    "penguin-stats.cn",
    "github.com",
)


@dataclass(frozen=True, slots=True)
class SourceRef:
    """一句话出处：来源、页面标题、链接、修订号、抓取时间与缓存状态。"""

    kind: str
    label: str
    title: str | None = None
    url: str | None = None
    revision: int | None = None
    fetched_at: datetime | None = None
    cached: bool = False
    stale: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "title": self.title,
            "url": self.url,
            "revision": self.revision,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "cached": self.cached,
            "stale": self.stale,
        }


def build_envelope(
    sources: Iterable[SourceRef] = (),
    *,
    unavailable: Iterable[str] = (),
    region_note: str | None = None,
) -> dict[str, Any]:
    """拼工具返回顶层的来源信封。

    `unavailable` 列的是**字段组名**（如 `"skills"`），提示词要求模型对这些字段
    说「查不到」，不得凭记忆补全（Q15、规格 §3.5）。
    """
    return {
        "sources": [source.as_dict() for source in sources],
        "untrusted": True,
        "unavailable": list(unavailable),
        "region_note": region_note,
    }


def merge_envelopes(*envelopes: dict[str, Any]) -> dict[str, Any]:
    """合并多份结果的信封；来源按 `(kind, url)` 去重，`unavailable` 取并集。"""
    sources: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    unavailable: list[str] = []
    region_note: str | None = None
    for envelope in envelopes:
        for source in envelope.get("sources", ()):
            marker = (source.get("kind"), source.get("url"))
            if marker in seen:
                continue
            seen.add(marker)
            sources.append(source)
        for group in envelope.get("unavailable", ()):
            if group not in unavailable:
                unavailable.append(group)
        region_note = envelope.get("region_note") or region_note
    return build_envelope(sources, unavailable=unavailable, region_note=region_note)


#: 供调用方构造空信封；等价于 `build_envelope()`。
EMPTY_ENVELOPE: dict[str, Any] = build_envelope()


def is_link_allowed(url: str) -> bool:
    """`url` 是否指向允许生成外链的主机（前端也用同一份名单）。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LINK_HOSTS
