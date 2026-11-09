"""``optimize_farming`` 工具：把活动关卡与商店纳入的刷图方案（M88 规格 §4.3.1）。

工具壳只负责参数形状、报告门控与服务取用；取数、合并、求解与报告都在
:class:`~maa_api.services.economy.service.FarmingOptimizer` 里。评测与单测通过
``app.state.farming_optimizer`` 替换服务，不碰真实 PRTS、企鹅物流与拆包客户端。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maa_api.agent.harness.context import CURRENT_RUN, require_run
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools._services import app_state
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.economy.model import FarmingRequest
from maa_api.services.economy.service import default_farming_optimizer

__all__ = [
    "OPTIMIZE_FARMING_DESCRIPTION",
    "OptimizeFarmingParams",
    "optimize_farming",
    "register_tools",
]

OPTIMIZE_FARMING_DESCRIPTION = (
    "算刷图方案：给定要凑的材料或养成计划，按当前理智、时间窗口与是否用药，"
    "结合企鹅物流掉率、当期活动关卡和活动商店，算出去哪几关刷几次、商店先换什么、"
    "要几天、要不要用药。只计算，不提交任务。比 plan_upgrade 多算活动商店与时间窗口；"
    "要建每日计划仍用 start_upgrade_plan。"
)


class OptimizeFarmingParams(BaseModel):
    """``optimize_farming`` 的参数（规格 §4.3.1）。"""

    model_config = ConfigDict(extra="forbid")

    materials: dict[str, Annotated[int, Field(ge=1, le=99999)]] | None = Field(
        default=None, max_length=60
    )
    plan_id: str | None = Field(default=None, min_length=1, max_length=36)
    use_depot: bool = True
    current_sanity: int = Field(default=0, ge=0, le=999)
    window_days: float | None = Field(default=None, ge=0.5, le=90)
    use_medicine: bool = False
    include_events: bool = True
    include_unnavigable: bool = False
    owned_tokens: dict[str, Annotated[int, Field(ge=0, le=999999)]] = Field(
        default_factory=dict, max_length=5
    )
    server: str | None = Field(default=None, min_length=1, max_length=16)
    write_report: bool = True

    @model_validator(mode="after")
    def _exactly_one(self) -> "OptimizeFarmingParams":
        if bool(self.materials) + bool(self.plan_id) != 1:
            raise ValueError("materials 与 plan_id 必须恰好给一个")
        return self


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``optimize_farming``（``economy`` 组，REST 可见）。"""

    registry.register(
        ToolDefinition(
            name="optimize_farming",
            group="economy",
            risk=ToolRisk.SAFE,
            description=OPTIMIZE_FARMING_DESCRIPTION,
            params_model=OptimizeFarmingParams,
            handler=optimize_farming,
            device="none",
            read_only=True,
            refetchable=True,
            untrusted_output=True,
            rest_visible=True,
        )
    )


async def optimize_farming(
    params: OptimizeFarmingParams, ctx: ToolContext
) -> dict[str, Any]:
    """算刷图方案；只有内置 Worker 且 ``write_report=true`` 时写报告产物。"""
    run = CURRENT_RUN.get()
    state = _state(run, ctx)
    optimizer = getattr(state, "farming_optimizer", None)
    if optimizer is None:
        optimizer = default_farming_optimizer(state)
        setattr(state, "farming_optimizer", optimizer)

    write_report = False
    session_id: str | None = None
    ticket_id: str | None = None
    if (
        ctx.caller is CallerType.INTERNAL
        and run is not None
        and run.spec.kind == "worker"
        and params.write_report
    ):
        run = require_run(
            kinds={"worker"},
            message="optimize_farming 的报告只能由工单里的 Worker 写",
        )
        write_report = True
        session_id = run.session_id or ctx.session_id
        ticket_id = run.ticket_id

    return await optimizer.optimize(
        FarmingRequest(
            materials=params.materials,
            plan_id=params.plan_id,
            use_depot=params.use_depot,
            current_sanity=params.current_sanity,
            window_days=params.window_days,
            use_medicine=params.use_medicine,
            include_events=params.include_events,
            include_unnavigable=params.include_unnavigable,
            owned_tokens=params.owned_tokens,
            server=params.server,
        ),
        write_report=write_report,
        session_id=session_id,
        ticket_id=ticket_id,
    )


def _state(run: Any, ctx: ToolContext) -> Any:
    """取应用运行时状态：运行里用 ``run.app``，REST 用请求上的 ``app``。"""
    if run is not None:
        return app_state(run, ctx)
    state = getattr(getattr(ctx.request, "app", None), "state", None)
    if state is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供应用运行时状态")
    return state
