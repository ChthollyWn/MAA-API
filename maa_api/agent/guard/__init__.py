"""提示注入防护的规则检测、包装与中间件（M80 规格 §4.2）。

``scan_text`` 与 ``render_tool_content`` 是纯函数；``InjectionGuardMiddleware``
在运行链的第 11 位扫描不可信结果、打标与降权。
"""

from __future__ import annotations

from .injection import InjectionGuardMiddleware
from .render import render_tool_content
from .rules import scan_text

__all__ = ["InjectionGuardMiddleware", "render_tool_content", "scan_text"]
