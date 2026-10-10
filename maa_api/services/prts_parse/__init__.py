"""PRTS wikitext 解析（规格 §5.5）：纯函数，不联网、不读盘。

``wikitext``
    MediaWiki 文本的通用解析：模板、链接、标签、章节（本模块不含任何领域规则）。
``activity`` / ``stage``（M25 Task 5 起）
    活动页与关卡页的字段白名单解析，建在 ``wikitext`` 之上。
"""

from __future__ import annotations

__all__ = [
    "wikitext",
]
