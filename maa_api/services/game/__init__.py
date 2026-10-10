"""游戏数据领域层（规格 §3.1）：拼「回答形状」，不直接发 HTTP。

``activities``
    当期活动：PRTS 优先（多个活动全部列出），本机资源包兜底。
"""

from __future__ import annotations

__all__ = ["activities"]
