"""发件箱的两种失败（v3 specs/m85 §4.5「适配器」）。

适配器的 ``send`` 失败时只抛这两个异常，由 ``OutboxWorker`` 决定状态迁移：

- :class:`OutboxRetry`：网络错误、429、5xx 等可重试失败 → ``failed``，按
  ``min(3600, 2**attempts)`` 秒退避；累计 8 次后 ``dead``。
- :class:`OutboxFatal`：4xx（除 429）等致命失败 → 直接 ``dead``，不重试。

G2 的四个平台适配器与测试的 FakeAdapter 都从这里导入，不再另造异常类型。
"""


class OutboxError(Exception):
    """发件箱发送失败的基类；调用方只需区分下面两个子类。"""


class OutboxRetry(OutboxError):
    """可重试失败：写 ``failed``、累加 ``attempts`` 并按退避重排。"""


class OutboxFatal(OutboxError):
    """致命失败：直接写 ``dead``，不再尝试。"""


__all__ = ["OutboxError", "OutboxFatal", "OutboxRetry"]
