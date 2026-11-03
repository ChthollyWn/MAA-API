"""模型层包：OpenAI 兼容客户端、用量归一与后续的档案、路由、成本（规格 §4.2）。

只从 :mod:`.client` 重新导出旧 ``maa_api.agent.llm`` 的公开名字；**不导入其他
子模块**（``usage``、``profiles`` 等），否则 ``harness`` 导入 ``llm.usage`` 时会
先加载整包，容易绕出循环导入。旧 ``llm.py`` 已删除：同名包与模块并存时包优先，
模块永远不会被加载（M67 规格 §4.2）。
"""

from __future__ import annotations

from .client import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    MAX_UPSTREAM_MESSAGE_CHARS,
    RETRYABLE_UPSTREAM_STATUS_FLOOR,
    TIMEOUT_MAX_ATTEMPTS,
    OpenAICompatibleClient,
)

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "MAX_RETRY_AFTER_SECONDS",
    "MAX_UPSTREAM_MESSAGE_CHARS",
    "RETRYABLE_UPSTREAM_STATUS_FLOOR",
    "TIMEOUT_MAX_ATTEMPTS",
    "OpenAICompatibleClient",
]
