"""M83 Watchdog 的值守件：记录器、确定性检查、告警落库与服务编排。

- :mod:`.checks`：六项确定性检查，纯函数，输入 :class:`WatchdogSnapshot`；
- :mod:`.recorder`：内核回调与流水线生命周期的进程内标记，重启清空；
- :mod:`.store`：未解除告警的去重 upsert 与「条件消失」的自动解除；
- :mod:`.service`：G2 的 :class:`WatchdogService`：周期巡检、动作与通知。

语义检查在 G3（T3），端点与前端在 G4–G5（T4）。
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
from maa_api.agent.watchdog.service import ActionResult, WatchdogService

__all__ = [
    "ActionResult",
    "AlertDraft",
    "CoreSample",
    "PipelineSample",
    "QueueSample",
    "RecorderSnapshot",
    "ReportSample",
    "RunSample",
    "TicketSample",
    "WatchdogRecorder",
    "WatchdogService",
    "WatchdogSnapshot",
    "WatchdogThresholds",
    "deterministic_checks",
]
