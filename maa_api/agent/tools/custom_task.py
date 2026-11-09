"""自定义任务起草与校验工具（M89 spec §4.7、§5 T5）。

两个工具都只读：

- ``draft_custom_task`` 把 1–31 步点击、滑动、长按交给
  :func:`~maa_api.agent.demonstrations.compiler.compile_steps`，返回
  ``entry`` / ``tasks`` / ``chain`` / ``warnings``；只计算，不写库、不写文件。
- ``validate_custom_task`` 先逐节点走 v2 的结构校验包装
  （``ResourceService.validate_custom_task_content``），再做链级校验，返回
  ``valid`` / ``errors`` / ``chain_errors`` / ``chain``；校验失败不抛 ``AppError``。

注册仍用 v2 的 ``register_custom_task``（``resource`` 组）：M89 给它加可选
``tasks`` 映射（工具层 1–32 个节点，与 ``content`` 二选一）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.custom_task.rules import MAX_BUNDLE_TASKS, check_bundle
from maa_api.agent.demonstrations.compiler import CompileError, Step, compile_steps
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools.resource import _service
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "DraftCustomTaskParams",
    "DraftStep",
    "ValidateCustomTaskParams",
    "draft_custom_task",
    "register_tools",
    "validate_custom_task",
]

#: 起草步骤上限：加链尾 ``Done`` 后不超过 32 个节点（spec §4.7）。
MAX_DRAFT_STEPS = 31
#: 长按编译成起终点相同的滑动时给模型看的提示（spec §4.5 第 5 条）。
LONG_PRESS_WARNING = "长按编译成起终点相同的滑动"


class DraftStep(BaseModel):
    """一条待编译的示教步骤（``draft_custom_task.steps`` 的元素）。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["click", "swipe", "long_press"]
    x: int | None = None
    y: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=60_000)
    delay_ms: int = Field(default=0, ge=0, le=10_000)
    #: 录制时的设备像素分辨率 ``[width, height]``；编译器按它换算到 1280×720。
    screen: list[int] = Field(min_length=2, max_length=2)


class DraftCustomTaskParams(BaseModel):
    """``draft_custom_task`` 的参数（M89 spec §4.7）。"""

    model_config = ConfigDict(extra="forbid")

    stage_code: str = Field(min_length=1, max_length=64)
    steps: list[DraftStep] = Field(min_length=1, max_length=MAX_DRAFT_STEPS)
    name: str | None = Field(default=None, min_length=1, max_length=64)


class ValidateCustomTaskParams(BaseModel):
    """``validate_custom_task`` 的参数（M89 spec §4.7）。"""

    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1, max_length=64)
    tasks: dict[str, dict[str, Any]] = Field(
        min_length=1, max_length=MAX_BUNDLE_TASKS
    )


def register_tools(registry: ToolRegistry) -> None:
    """注册两个 ``custom_task`` 组工具（SAFE、只读、可重取、REST 可见）。"""
    registry.register(
        ToolDefinition(
            name="draft_custom_task",
            group="custom_task",
            risk=ToolRisk.SAFE,
            description=(
                "把一段点击、滑动、长按步骤编译成自定义任务链，返回入口名、全部节点、"
                "链路径与警告。只计算，不写库、不写资源。新活动没有现成作业时先用它起草，"
                "返回的 tasks 交给 validate_custom_task 校验，再用 register_custom_task "
                "一次注册整条链。"
            ),
            params_model=DraftCustomTaskParams,
            handler=draft_custom_task,
            device="none",
            read_only=True,
            refetchable=True,
            rest_visible=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="validate_custom_task",
            group="custom_task",
            risk=ToolRisk.SAFE,
            description=(
                "校验一条自定义任务链：逐节点检查结构，再做链级检查（入口、环、Stop 出口、"
                "矩形边界），返回 valid、errors、chain_errors 与 chain。不抛错；"
                "valid=false 时按 errors 与 chain_errors 改步骤重编。"
            ),
            params_model=ValidateCustomTaskParams,
            handler=validate_custom_task,
            device="none",
            read_only=True,
            refetchable=True,
            rest_visible=True,
        )
    )


async def draft_custom_task(
    params: DraftCustomTaskParams, _context: ToolContext
) -> dict[str, Any]:
    """只调编译器：返回 ``{entry, tasks, chain, warnings}``，不碰服务与文件。"""
    prefix = params.name or f"Custom_{params.stage_code}"
    steps = [
        Step(
            action=step.action,
            x=step.x,
            y=step.y,
            x1=step.x1,
            y1=step.y1,
            x2=step.x2,
            y2=step.y2,
            duration_ms=step.duration_ms,
            delay_ms=step.delay_ms,
            screen=(int(step.screen[0]), int(step.screen[1])),
        )
        for step in params.steps
    ]
    try:
        compiled = compile_steps(prefix=prefix, steps=steps)
    except CompileError as exc:
        raise AppError(
            ErrorCode.CUSTOM_TASK_INVALID,
            "步骤无法编译成自定义任务，请按 errors 修正",
            {"errors": exc.errors},
        ) from exc
    warnings = (
        [LONG_PRESS_WARNING]
        if any(step.action == "long_press" for step in params.steps)
        else []
    )
    return {
        "entry": compiled.entry,
        "tasks": compiled.tasks,
        "chain": list(compiled.chain),
        "warnings": warnings,
    }


async def validate_custom_task(
    params: ValidateCustomTaskParams, context: ToolContext
) -> dict[str, Any]:
    """逐节点结构校验 + 链级校验；不抛校验错误，只返回四个键。"""
    service = _service(context)
    errors: list[dict[str, str]] = []
    for task_name, node in params.tasks.items():
        for issue in service.validate_custom_task_content(node):
            errors.append(
                {
                    "task": task_name,
                    "field": str(issue.get("field") or "content"),
                    "reason": str(issue.get("reason") or "结构校验失败"),
                }
            )
    bundle_errors, chain_errors, chain = check_bundle(params.tasks, params.entry)
    errors.extend(issue.as_dict() for issue in bundle_errors)
    return {
        "valid": not errors and not chain_errors,
        "errors": errors,
        "chain_errors": [issue.as_dict() for issue in chain_errors],
        "chain": list(chain),
    }
