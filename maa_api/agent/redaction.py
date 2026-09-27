"""凭据遮蔽：本地代码里唯一一份「写日志/出接口前先脱敏」的实现。

两个调用点共享它：组装发往模型的上下文（``runtime.py``）与整理上游错误
（``llm.py``）。上游错误文本可能回显 API Key 或 ``Authorization`` 头，而出接口的
``app_error`` 会带 ``details``，因此进 ``details`` 之前必须先过这里。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["redact_remote_data"]

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SK_PATTERN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]+")
_SECRET_KEY_MARKERS = (
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "password",
    "passwd",
    "clientsecret",
    "secretkey",
    "privatekey",
    "credential",
    "cookie",
    "encryptionkey",
    "token",
    "secret",
)
_SECRET_KEY_NAMES = {"auth", "token", "secret", "password", "passwd", "credential"}


def redact_remote_data(value: Any) -> Any:
    """递归复制数据并遮蔽常见凭据字段、Bearer token 与 OpenAI 风格 key。"""
    if isinstance(value, str):
        value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        value = _SK_PATTERN.sub("[REDACTED]", value)
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
        if isinstance(parsed, (Mapping, Sequence)) and not isinstance(parsed, (str, bytes)):
            return json.dumps(
                redact_remote_data(parsed), ensure_ascii=False, separators=(",", ":")
            )
        return value
    if isinstance(value, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            sensitive = normalized in _SECRET_KEY_NAMES or any(
                normalized.endswith(marker) for marker in _SECRET_KEY_MARKERS
            )
            cleaned[key] = "[REDACTED]" if sensitive else redact_remote_data(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact_remote_data(item) for item in value]
    return value
