"""v3 工具取运行时服务的小工具（M70 规格 §4.2.2、§4.5.11）。

``report``、``delegate`` 与五个工单工具的 handler 都从当前运行（``CURRENT_RUN``）
拿到应用，再按 ``app.state`` 上的装配名取服务。服务由 lifespan 第 8 步装配；
T9–T11 落地之前测试通过替身注入，这里的取法与既有工具一致。
"""

from __future__ import annotations

from typing import Any

from maa_api.agent.harness.context import RunContext
from maa_api.agent.registry import ToolContext
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["app_state", "v3_service"]


def app_state(run: RunContext, ctx: ToolContext) -> Any:
    """运行所属应用的 ``state``；v3 运行用 ``run.app``，兜底用工具上下文的请求。"""
    state = getattr(getattr(run, "app", None), "state", None)
    if state is None:
        state = getattr(
            getattr(getattr(ctx, "request", None), "app", None), "state", None
        )
    if state is None:
        raise AppError(
            ErrorCode.INTERNAL_ERROR, "工具上下文未提供应用运行时状态"
        )
    return state


def v3_service(run: RunContext, ctx: ToolContext, name: str, label: str) -> Any:
    """取 ``app.state`` 上的服务；还没装配时给出可读的工具错误。"""
    service = getattr(app_state(run, ctx), name, None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, f"{label}尚未启动")
    return service
