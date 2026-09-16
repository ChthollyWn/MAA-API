"""领域错误码与领域异常。

骨架来自 docs/05 §4 与 docs/13 ADR-04：错误码集中定义在 ``ErrorCode(StrEnum)`` 中，
每个错误码在代码里绑定**固定**的 HTTP 状态码，路由层不再自行决定状态码 ——
保证同一个错误在不同端点上表现一致；响应体里的 ``error.code`` 是可枚举字符串，
agent 能据此做确定性分支。

本模块当前只包含**内核层**的 6 条错误码（docs/05 §4.4）。其余错误码
（鉴权 / 参数 / 通用 / 流水线 / 更新 …）归后续里程碑，M1 不提前抄全表。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

__all__ = ["AppError", "ERROR_HTTP_STATUS", "ErrorCode"]


class ErrorCode(StrEnum):
    """与领域异常一一对应的错误码；成员名即 ``error.code`` 的线上取值。"""

    # docs/05 §4.4 内核状态
    CORE_NOT_READY = "CORE_NOT_READY"
    CORE_RESTARTING = "CORE_RESTARTING"
    CORE_CRASHED = "CORE_CRASHED"
    CORE_START_FAILED = "CORE_START_FAILED"
    CORE_COMMAND_FAILED = "CORE_COMMAND_FAILED"
    CORE_COMMAND_TIMEOUT = "CORE_COMMAND_TIMEOUT"


#: 错误码 → 固定 HTTP 状态码（docs/05 §4.4）。新增错误码必须同时登记到这里。
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.CORE_NOT_READY: 503,
    ErrorCode.CORE_RESTARTING: 503,
    ErrorCode.CORE_CRASHED: 503,
    ErrorCode.CORE_START_FAILED: 500,
    ErrorCode.CORE_COMMAND_FAILED: 502,
    ErrorCode.CORE_COMMAND_TIMEOUT: 504,
}


class AppError(Exception):
    """领域异常：携带错误码、人类可读消息与固定 HTTP 状态码。

    ``http_status`` 在构造时由 :data:`ERROR_HTTP_STATUS` 派生。未登记的错误码
    **fail loud**：立即抛 ``KeyError``，不返回"默认 500" —— 未登记说明调用方用错了
    错误码，是编码缺陷，应当在与错误码定义同一次改动里暴露，而不是等线上把
    一个本该是 503 的场景伪装成 500。

    实例只持有 ``ErrorCode`` / ``str`` / ``dict``，因此可被 pickle
    （异常对象会进多进程 IPC 与任务队列，序列化往返必须保真）。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            http_status = ERROR_HTTP_STATUS[code]
        except KeyError:
            raise KeyError(f"未登记的 ErrorCode，无法派生 HTTP 状态码: {code!r}") from None

        # args 保存完整的构造参数，Exception 默认的 __reduce__ 依赖它实现 pickle 往返。
        super().__init__(code, message, details)
        self.code = code
        self.message = message
        self.details = details
        self.http_status = http_status

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def __repr__(self) -> str:
        return (
            f"AppError(code={self.code!r}, message={self.message!r}, "
            f"details={self.details!r})"
        )
