"""计划中间件：批准前的工具过滤与动态尾部的 ``plan`` 段（M75 规格 §4.5）。

- ``wrap_tool``：计划 ``awaiting_approval`` 时只放行只读工具与
  ``update_plan`` / ``write_note`` / ``read_artifact`` / ``ask_user`` /
  ``report``（以及目标不独占设备的 ``delegate``），其余返回
  ``error.code = PLAN_APPROVAL_REQUIRED``。它排在 ``PolicyMiddleware`` 之前，
  所以被拦下的调用不会进确认卡片。
- ``before_model``：把当前计划写进动态尾部的 ``plan`` 段（06 §7.2），并在待批准
  时把 ``human_action`` 放进 ``ctx.state[PENDING_HUMAN_ACTION_KEY]``，供
  ``ensure_report`` 在本轮结束时代交 waiting 汇报。

``on_run_end`` 不在这里补汇报：``on_run_end`` 逆序执行，``ReportMiddleware``
（18 号）排在计划（9 号）之后、先跑，靠 ``ctx.state`` 里的 ``human_action``
交接（计划文件的裁决 4）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from maa_api.agent.context.layout import (
    TAIL_SEGMENT_ORDER,
    ContextLayout,
    Segment,
    put_segment,
)
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.context import (
    ModelRequest,
    RunContext,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.middleware import CallNext, Middleware
from maa_api.agent.plans.service import plan_human_action, render_plan_text
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES
from maa_api.domain.enums import PlanStatus
from maa_api.domain.errors import AppError

__all__ = [
    "ALWAYS_ALLOWED_TOOLS",
    "PENDING_HUMAN_ACTION_KEY",
    "PLAN_APPROVAL_REQUIRED",
    "PlanMiddleware",
]

logger = logging.getLogger(__name__)

#: 待批准时额外放行的工具（spec §4.5）；只读工具按 ``ToolDefinition.read_only``
#: 判断，不写死名单。
ALWAYS_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"update_plan", "write_note", "read_artifact", "ask_user", "report"}
)
#: 工具结果 ``error.code`` 的字符串；不进 ``ErrorCode``（spec §9）。
PLAN_APPROVAL_REQUIRED = "PLAN_APPROVAL_REQUIRED"
#: 待批准计划的 ``human_action``；``ensure_report`` 读它代交 waiting 汇报。
PENDING_HUMAN_ACTION_KEY = "plan.pending_human_action"


def _app_state(ctx: RunContext) -> Any:
    state = getattr(getattr(ctx, "app", None), "state", None)
    if state is None:
        state = getattr(getattr(getattr(ctx, "request", None), "app", None), "state", None)
    return state


def _plan_service(ctx: RunContext) -> Any:
    return getattr(_app_state(ctx), "plan_service", None)


def _layout_service(ctx: RunContext) -> ContextLayout:
    """优先用进程内的 ``app.state.context_layout``，测试里退回本轮本地实例。"""
    service = getattr(_app_state(ctx), "context_layout", None)
    if isinstance(service, ContextLayout):
        return service
    local = ctx.state.get("plan.context_layout")
    if isinstance(local, ContextLayout):
        return local
    local = ContextLayout(TokenEstimator())
    ctx.state["plan.context_layout"] = local
    return local


class PlanMiddleware(Middleware):
    """Worker 专用的计划中间件；``plan_service`` 从 ``app.state`` 取。"""

    name = "plan"
    applies_to = frozenset({"worker"})

    def __init__(
        self,
        *,
        registry: Any = None,
        settings_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.registry = registry
        # 与 SpillMiddleware 同一口径：链路由 runtime 传入设置读取器；计划策略本身
        # 由 ``PlanService`` 在提交时现读（缺省 complex / 4）。
        self.settings_provider = settings_provider

    # ------------------------------------------------------------------
    # 模型调用前：尾部与待批准状态
    # ------------------------------------------------------------------
    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        if ctx.spec.name in LEGACY_SPEC_NAMES:
            return req
        plan = await self._active_plan(ctx)
        if plan is None:
            ctx.state.pop(PENDING_HUMAN_ACTION_KEY, None)
            return req
        if str(plan.status) == PlanStatus.AWAITING_APPROVAL.value:
            ctx.state[PENDING_HUMAN_ACTION_KEY] = plan_human_action(plan)
        else:
            ctx.state.pop(PENDING_HUMAN_ACTION_KEY, None)
        parts = req.meta.get("layout_parts")
        if parts is None:
            return req
        profile = req.profile
        if profile is None:
            model_state = ctx.state.get("model")
            if isinstance(model_state, Mapping):
                profile = model_state.get("profile")
        if profile is None:
            return req
        put_segment(parts.tail, Segment("plan", render_plan_text(plan)), TAIL_SEGMENT_ORDER)
        return _layout_service(ctx).apply(ctx, req, parts, profile=profile)

    # ------------------------------------------------------------------
    # 工具过滤：批准前只放行白名单
    # ------------------------------------------------------------------
    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        plan = await self._pending_plan(ctx)
        if plan is None or self._allowed(ctx, call):
            return await call_next(call)
        return ToolOutcome(
            status="error",
            error={
                "code": PLAN_APPROVAL_REQUIRED,
                "message": (
                    f"计划「{plan.title}」还在等待用户批准；批准前只能调用只读工具"
                    "与 update_plan、write_note、read_artifact、ask_user、report"
                ),
                "details": {
                    "plan_id": str(plan.id),
                    "session_id": ctx.session_id,
                    "tool": call.name,
                },
            },
        )

    # ------------------------------------------------------------------
    # 读取与白名单
    # ------------------------------------------------------------------
    async def _active_plan(self, ctx: RunContext) -> Any:
        service = _plan_service(ctx)
        if service is None or not ctx.session_id:
            return None
        try:
            return await service.active_for_session(ctx.session_id)
        except Exception:  # noqa: BLE001 - 计划读不到时按「没有计划」继续本轮
            logger.warning(
                "读取当前计划失败，本轮不插 plan 段：session=%s",
                ctx.session_id,
                exc_info=True,
            )
            return None

    async def _pending_plan(self, ctx: RunContext) -> Any:
        plan = await self._active_plan(ctx)
        if plan is None:
            return None
        if str(plan.status) != PlanStatus.AWAITING_APPROVAL.value:
            return None
        return plan

    def _allowed(self, ctx: RunContext, call: ToolCall) -> bool:
        """批准前允许的工具（spec §4.5）。未知工具交给内层报 ``TOOL_NOT_FOUND``。"""
        if call.name == "delegate":
            arguments = call.arguments if isinstance(call.arguments, Mapping) else {}
            return str(arguments.get("device") or "") != "exclusive"
        if call.name in ALWAYS_ALLOWED_TOOLS:
            return True
        definition = self._definition(ctx, call.name)
        if definition is None:
            return True
        return definition.read_only is True

    def _definition(self, ctx: RunContext, name: str) -> Any:
        registry = self.registry
        if registry is None:
            registry = getattr(
                getattr(_app_state(ctx), "confirmation_service", None),
                "registry",
                None,
            )
        if registry is None:
            return None
        try:
            return registry.get(name)
        except AppError:
            return None
