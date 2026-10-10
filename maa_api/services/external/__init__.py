"""外部访问层（规格 §3.1–§3.3）：白名单、限流、熔断、缓存、状态与来源信封。

唯一出口是 :class:`~maa_api.services.external.http.ExternalClient`；工具层与领域层通过
``external/prts.py`` 这类**按来源**的客户端取数，不自己拼 URL。
"""

from __future__ import annotations

__all__ = [
    "cache",
    "envelope",
    "http",
    "sources",
    "status",
]
