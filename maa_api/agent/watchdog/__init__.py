"""M83 Watchdog 的值守件：进程内记录器、确定性检查与告警落库助手。

T1 只放不依赖服务的部分（spec §5 T1）：

- :mod:`.checks`：六项确定性检查，纯函数，输入 :class:`WatchdogSnapshot`；
- :mod:`.recorder`：内核回调与流水线生命周期的进程内标记，重启清空；
- :mod:`.store`：未解除告警的去重 upsert 与「条件消失」的自动解除。

服务、动作、端点与语义检查在 G2–G5（T2–T4）。
"""

from maa_api.agent.watchdog.checks import (
    AlertDraft,
    CoreSample,
    PipelineSample,
    QueueSample,
    ReportSample,
    RunSample,
    TicketSample,
    WatchdogSnapshot,
    WatchdogThresholds,
    deterministic_checks,
)
from maa_api.agent.watchdog.recorder import RecorderSnapshot, WatchdogRecorder

__all__ = [
    "AlertDraft",
    "CoreSample",
    "PipelineSample",
    "QueueSample",
    "RecorderSnapshot",
    "ReportSample",
    "RunSample",
    "TicketSample",
    "WatchdogRecorder",
    "WatchdogSnapshot",
    "WatchdogThresholds",
    "deterministic_checks",
]
