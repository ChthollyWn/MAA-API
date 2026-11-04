"""``report`` 工具：Worker 每轮交回状态与摘要（M70 规格 §4.5.11）。

工具壳只负责参数形状、调用方检查与转发给 :class:`WorkerService.submit_report`；
落库、终态处理与 SSE ``report`` 事件都在服务里（T9）。``ReportMiddleware`` 负责
「一批只能有一个、且必须放在最后」这一条，handler 不重复判断。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maa_api.agent.harness.context import require_run
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools._services import v3_service
from maa_api.domain.enums import ReportStatus

__all__ = ["HumanAction", "ReportParams", "register_tools", "report"]

REPORT_DESCRIPTION = "每一轮结束前调用一次，交回这一轮的状态与摘要；放在这一批调用的最后。"


class HumanAction(BaseModel):
    """需要用户处理的动作；``type`` 决定前端弹哪一种卡片。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["confirmation", "question", "decision"]
    prompt: str = Field(min_length=1, max_length=200)
    ref_id: str | None = Field(default=None, max_length=64)


class ReportParams(BaseModel):
    """``report`` 的参数（规格 §4.5.11）。"""

    model_config = ConfigDict(extra="forbid")

    status: ReportStatus
    summary: str = Field(min_length=1, max_length=600)
    human_action: HumanAction | None = None
    details: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> "ReportParams":
        if (
            self.status in (ReportStatus.WAITING, ReportStatus.BLOCKED)
            and self.human_action is None
        ):
            raise ValueError("status 为 waiting 或 blocked 时必须填写 human_action")
        if self.details is not None and len(
            json.dumps(self.details, ensure_ascii=False)
        ) > 4000:
            raise ValueError("details 序列化后不能超过 4000 字符")
        return self


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``report``（``worker`` 组，不进 REST 目录）。"""

    registry.register(
        ToolDefinition(
            name="report",
            group="worker",
            risk=ToolRisk.SAFE,
            description=REPORT_DESCRIPTION,
            params_model=ReportParams,
            handler=report,
            # 写工具：含它的一批由 toolexec 串行执行，保证它在同批其它调用之后执行。
            read_only=False,
            rest_visible=False,
        )
    )


async def report(params: ReportParams, ctx: ToolContext) -> dict[str, Any]:
    """把本轮状态交给 Worker 服务；只有工单里的 Worker 能调用。"""
    run = require_run(kinds={"worker"}, message="report 只能由工单里的 Worker 调用")
    worker_service = v3_service(run, ctx, "worker_service", "Worker 服务")
    return await worker_service.submit_report(run, params)
