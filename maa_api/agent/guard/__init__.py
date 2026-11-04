"""提示注入防护的规则检测与包装（M80 §4.2）。

G4 的 ``InjectionGuardMiddleware`` 加入 ``injection.py`` 后，在下面补
``from .injection import InjectionGuardMiddleware``，并把它加进 ``__all__``。
"""

from __future__ import annotations

from .render import render_tool_content
from .rules import scan_text

__all__ = ["render_tool_content", "scan_text"]
