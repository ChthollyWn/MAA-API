"""Agent 的作业尝试工具（M29 规格 §9.7、docs/11 §3.2）：执行一次尝试、模型补判结果。

组 ``resource``（docs/11 §3.2 的「自定义资源组」：作业资源与草稿工具都在这一组，
作业尝试操作的是同一批 ``kind=copilot`` 资源），两条都声明 ``SAFE``：

- ``run_copilot_attempt``：提交作业任务、跑结果识别、退出结算、返回尝试编号与剩余次数。
  **授权由策略层判定**（主 agent 在 ``agent/policy.py`` 里加分支：只在 INTERNAL 调用方、
  且该会话有该关卡的有效授权时放行；没有授权就返回
  ``confirmation_action="grant_copilot_attempts"``）。handler 这一层遇到缺授权仍会把
  ``CONFIRMATION_REQUIRED`` / ``COPILOT_GRANT_STAGE_MISMATCH`` /
  ``COPILOT_GRANT_EXHAUSTED`` 原样抛出去，policy 与运行时按错误码展示。
  ``grant_attempts`` 与 ``success`` **只在申请授权时用于预填**（规格 §9.7 的参数说明）。
- ``record_copilot_result``：只允许在 ``outcome=unknown`` 的尝试上调用，判定方记
  ``model``（规格 §9.7）。``evidence`` ≤ 200 字。

服务的取法与既有工具一致（见 ``agent/tools/account.py`` 的 ``_service``）：
``app.state.copilot_attempt_service`` 优先，缺省按 ``app.state.session_factory`` 与
``app.state.pipeline_runner`` 现建。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot.attempt import MAX_EVIDENCE_CHARS

__all__ = [
    "RecordCopilotResultParams",
    "RunCopilotAttemptParams",
    "record_copilot_result",
    "register_tools",
    "run_copilot_attempt",
]


class RunCopilotAttemptParams(BaseModel):
    """``run_copilot_attempt`` 的参数（规格 §9.7 的参数表）。"""

    model_config = ConfigDict(extra="forbid")

    copilot_id: str = Field(
        min_length=1,
        max_length=36,
        description="要执行的作业资源 id（save_copilot_draft 或作业页里的 id）。",
    )
    support: Literal["fill_one", "none"] | None = Field(
        default=None,
        description=(
            "是否允许助战补位：fill_one 缺一名时借助战 / none 不用助战；"
            "不填按授权里的设置。"
        ),
    )
    grant_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="需要申请授权时的尝试次数预填（1–10，默认 3），批准后以授权为准。",
    )
    success: Literal["three_star", "clear"] = Field(
        default="three_star",
        description=(
            "需要申请授权时的成功标准预填：three_star 三星才行 / clear 通关即可。"
        ),
    )


class RecordCopilotResultParams(BaseModel):
    """``record_copilot_result`` 的参数（规格 §9.7）。"""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(
        min_length=1,
        max_length=36,
        description="run_copilot_attempt 返回的 attempt_id（只能是 outcome=unknown 的那次）。",
    )
    outcome: Literal["three_star", "two_star", "clear", "failed"] = Field(
        description="你从结算截图里看到的结果：三星 / 二星 / 结算出现但星级不明 / 行动失败。",
    )
    evidence: str = Field(
        default="",
        max_length=MAX_EVIDENCE_CHARS,
        description=f"判定依据，最多 {MAX_EVIDENCE_CHARS} 字（会显示在尝试记录里）。",
    )


def register_tools(registry: ToolRegistry) -> None:
    """注册作业尝试的两条工具（由 `agent/tools/__init__.py` 的装配任务接入）。"""
    definitions = (
        ToolDefinition(
            name="run_copilot_attempt",
            group="resource",
            risk=ToolRisk.SAFE,
            description=(
                "执行一次自动战斗作业尝试：提交作业任务、识别结算结果、退出结算回到「开始行动」。"
                "需要先拿到作业授权（没有授权时先申请 grant_copilot_attempts）；"
                "一次尝试最多阻塞 20 分钟。返回尝试编号、结果、判定方、截图 id、剩余次数与动作日志摘要。"
            ),
            params_model=RunCopilotAttemptParams,
            handler=run_copilot_attempt,
        ),
        ToolDefinition(
            name="record_copilot_result",
            group="resource",
            risk=ToolRisk.SAFE,
            description=(
                "记录一次「内核没能识别出结算结果」（outcome=unknown）的尝试结果，判定方记为模型。"
                "先看 run_copilot_attempt 返回的截图或 get_screenshot，再在三/二星、通关、失败之间选一个。"
            ),
            params_model=RecordCopilotResultParams,
            handler=record_copilot_result,
        ),
    )
    for definition in definitions:
        registry.register(definition)


async def run_copilot_attempt(
    params: RunCopilotAttemptParams, context: ToolContext
) -> dict[str, Any]:
    service = _service(context)
    return await service.run(
        copilot_id=params.copilot_id,
        session_id=context.session_id,
        support=params.support,
        grant_attempts=params.grant_attempts,
        success=params.success,
    )


async def record_copilot_result(
    params: RecordCopilotResultParams, context: ToolContext
) -> dict[str, Any]:
    service = _service(context)
    return await service.record(params.attempt_id, params.outcome, params.evidence)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _service(context: ToolContext) -> Any:
    """装配好的作业尝试服务优先；否则按 app.state 现建一个。

    装配好的那一个可能是 ``main.py`` 早于资源服务构造的（它只拿到 ``session_factory``），
    所以这里补一次后置绑定：识别任务要写进 custom 层，没有 ``resource_service`` 就写不出去。
    """
    service = _state(context, "copilot_attempt_service")
    if service is not None:
        _bind_resource_service(service, _state(context, "resource_service"))
        return service
    factory = _session_factory(context)
    from maa_api.services.copilot.attempt import CopilotAttemptService
    from maa_api.services.copilot.grant import CopilotGrantService
    from maa_api.services.copilot.result import CopilotResultService

    pipeline_service = _state(context, "pipeline_runner")
    if pipeline_service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "作业尝试服务尚未装配（缺少 pipeline_runner）",
        )
    return CopilotAttemptService(
        factory,
        pipeline_service=pipeline_service,
        grant_service=CopilotGrantService(factory),
        result_service=CopilotResultService(
            factory, resource_service=_state(context, "resource_service")
        ),
        draft_service=_state(context, "copilot_draft_service"),
        box=_state(context, "box_service"),
    )


def _bind_resource_service(attempt_service: Any, resource_service: Any) -> None:
    """把 ``resource_service`` 补给它下面的结果识别服务（已绑过就不动）。"""
    if resource_service is None:
        return
    result_service = getattr(attempt_service, "_result_service", None) or getattr(
        attempt_service, "result_service", None
    )
    binder = getattr(result_service, "bind_resource_service", None)
    if callable(binder):
        binder(resource_service)


def _state(context: ToolContext, name: str) -> Any:
    state = getattr(getattr(context.request, "app", None), "state", None)
    return getattr(state, name, None)


def _session_factory(context: ToolContext) -> Any:
    factory = _state(context, "session_factory")
    if factory is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "作业尝试服务尚未装配（缺少 session_factory）",
        )
    return factory
