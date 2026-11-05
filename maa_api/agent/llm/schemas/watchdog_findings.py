"""``watchdog_findings`` schema：值守语义检查对一张工单的判断（M83 规格 §4.5）。

一次调用只针对一张被确定性检查挑中的工单；``items`` 为空表示看不出异常。
``check`` 只允许 ``ticket_no_progress`` / ``ticket_drift``；``suggested_action``
只在建议把工单标成 blocked 时填 ``mark_blocked``，否则省略（``None``）。
模型只给建议，动作由 ``WatchdogService.apply_action`` 执行。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["WatchdogFinding", "WatchdogFindings"]

#: 语义检查可以给出的两种判断（08 §6.1）。
WatchdogCheck = Literal["ticket_no_progress", "ticket_drift"]


class WatchdogFinding(BaseModel):
    """一条语义检查结论；目标固定是工单。"""

    model_config = ConfigDict(extra="forbid")

    check: WatchdogCheck
    target_kind: Literal["ticket"]
    target_id: str = Field(min_length=1, max_length=36)
    severity: Literal["info", "warn", "critical"]
    message: str = Field(min_length=1, max_length=300)
    suggested_action: Literal["mark_blocked"] | None = None


class WatchdogFindings(BaseModel):
    """一次语义检查的输出；``items`` 为空表示看不出异常。"""

    model_config = ConfigDict(extra="forbid")

    items: list[WatchdogFinding] = Field(default_factory=list, max_length=8)


register_schema("watchdog_findings", WatchdogFindings)
