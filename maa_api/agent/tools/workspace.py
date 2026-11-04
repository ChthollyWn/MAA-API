"""工作记忆工具：``read_artifact`` 续读、``write_note`` 笔记与 ``update_plan`` 计划
（M75 规格 §4.5）。

三个工具都只给 Worker（``read_artifact`` 也允许子代理读取自己的产物），分别从
``app.state.artifact_service`` 与 ``app.state.plan_service`` 取服务。工单范围只
约束模型：HTTP 端点由登录用户鉴权，不经过这里。

``write_note`` 的正文上限是 20 000 字。超过时不抛 HTTP 错误码（它不是 API 契约的
一部分），而是在工具结果的 ``error`` 字段里返回 ``note_too_long``。
``update_plan`` 的步骤列表不合法时同样返回工具错误 ``plan_invalid``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.context.durable import DurableState
from maa_api.agent.harness.context import RunContext, require_run
from maa_api.agent.harness.plan import PENDING_HUMAN_ACTION_KEY
from maa_api.agent.harness.spill import resolve_spill_threshold
from maa_api.agent.plans.service import PLAN_INVALID, PlanInvalid, plan_human_action
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools._services import v3_service
from maa_api.domain.enums import ArtifactKind, PlanStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "NOTE_MAX_CHARS",
    "NOTE_TOO_LONG",
    "PlanItemParams",
    "ReadArtifactParams",
    "UpdatePlanParams",
    "WriteNoteParams",
    "read_artifact",
    "register_tools",
    "update_plan",
    "write_note",
]

#: 单条笔记的正文上限（06 §7.3）。
NOTE_MAX_CHARS = 20_000
#: 笔记超长时工具结果 ``error.code`` 的字符串（不是 ``ErrorCode``）。
NOTE_TOO_LONG = "note_too_long"

READ_ARTIFACT_DESCRIPTION = "按产物 id 读取全文或从偏移续读；工具结果里的 spilled 指针带 artifact_id 与 next_offset。"
WRITE_NOTE_DESCRIPTION = "把中间结论写成笔记；传 artifact_id 时覆盖本工单的同一条笔记。"
UPDATE_PLAN_DESCRIPTION = "提交完整的多步计划（标题与全部步骤）。计划待批准时只能调用只读工具，不要提交任务或操作设备。"


class ReadArtifactParams(BaseModel):
    """``read_artifact`` 的参数（规格 §4.5）。"""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=36)
    offset: int = 0
    limit: int | None = None


class WriteNoteParams(BaseModel):
    """``write_note`` 的参数（规格 §4.5）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)
    artifact_id: str | None = Field(default=None, max_length=36)


class PlanItemParams(BaseModel):
    """计划里的一步；``id`` 可回传上一版结果里的 8 位十六进制 id。"""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=16)
    title: str = Field(min_length=1, max_length=120)
    status: Literal["pending", "in_progress", "done", "cancelled"] = "pending"
    note: str | None = Field(default=None, max_length=500)


class UpdatePlanParams(BaseModel):
    """``update_plan`` 的参数（规格 §4.5）；每次提交完整列表。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    items: list[PlanItemParams] = Field(min_length=1, max_length=30)


def register_tools(registry: ToolRegistry) -> None:
    """注册三个 ``workspace`` 工具；都不进 REST 目录。"""
    registry.register(
        ToolDefinition(
            name="read_artifact",
            group="workspace",
            risk=ToolRisk.SAFE,
            description=READ_ARTIFACT_DESCRIPTION,
            params_model=ReadArtifactParams,
            handler=read_artifact,
            read_only=True,
            refetchable=False,
            rest_visible=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="write_note",
            group="workspace",
            risk=ToolRisk.SAFE,
            description=WRITE_NOTE_DESCRIPTION,
            params_model=WriteNoteParams,
            handler=write_note,
            read_only=False,
            refetchable=False,
            rest_visible=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="update_plan",
            group="workspace",
            risk=ToolRisk.SAFE,
            description=UPDATE_PLAN_DESCRIPTION,
            params_model=UpdatePlanParams,
            handler=update_plan,
            read_only=False,
            refetchable=False,
            rest_visible=False,
        )
    )


def _service(ctx: ToolContext) -> Any:
    run = require_run(
        kinds={"worker", "subagent"}, message="工作记忆工具只能在 Worker 或子代理里调用"
    )
    return run, v3_service(run, ctx, "artifact_service", "产物服务")


def _not_found() -> AppError:
    return AppError(ErrorCode.ARTIFACT_NOT_FOUND, "产物不存在")


def _ensure_visible(run: RunContext, artifact: Any) -> None:
    """工单/会话范围校验；读不到与不存在一样报 404（规格 §4.5）。"""
    if run.spec.kind == "orchestrator":
        return
    artifact_ticket = getattr(artifact, "ticket_id", None)
    if artifact_ticket and run.ticket_id and artifact_ticket != run.ticket_id:
        raise _not_found()
    if getattr(artifact, "session_id", None) != run.session_id:
        raise _not_found()


async def read_artifact(
    params: ReadArtifactParams, ctx: ToolContext
) -> dict[str, Any]:
    """读产物的一段；``limit`` 缺省与上限都是外置阈值，末尾的 ``next_offset`` 为 null。"""
    run, service = _service(ctx)
    detail = await service.get(params.artifact_id)
    _ensure_visible(run, detail.artifact)
    artifact = detail.artifact
    text = detail.content
    total_chars = len(text)
    threshold = resolve_spill_threshold()
    offset = max(int(params.offset), 0)
    limit = threshold if params.limit is None else max(1, min(int(params.limit), threshold))
    content = text[offset : offset + limit]
    next_offset = offset + len(content)
    if next_offset >= total_chars:
        next_offset = None
    return {
        "artifact_id": artifact.id,
        "title": artifact.title,
        "kind": artifact.kind,
        "mime": artifact.mime,
        "total_chars": total_chars,
        "offset": offset,
        "content": content,
        "next_offset": next_offset,
    }


async def write_note(
    params: WriteNoteParams, ctx: ToolContext
) -> dict[str, Any]:
    """新建或覆盖一条笔记，并把 ``{id, title}`` 写进本轮的 ``durable.notes``。"""
    run, service = _service(ctx)
    if len(params.content) > NOTE_MAX_CHARS:
        return {
            "error": {
                "code": NOTE_TOO_LONG,
                "message": f"笔记正文不能超过 {NOTE_MAX_CHARS} 字（当前 {len(params.content)} 字）",
                "details": {"limit": NOTE_MAX_CHARS, "chars": len(params.content)},
            }
        }
    updated = params.artifact_id is not None
    if params.artifact_id is not None:
        detail = await service.get(params.artifact_id)
        _ensure_visible(run, detail.artifact)
        if str(detail.artifact.kind) != ArtifactKind.NOTE.value:
            raise _not_found()
        artifact = await service.update_note(
            params.artifact_id, content=params.content, title=params.title
        )
    else:
        artifact = await service.put(
            kind=ArtifactKind.NOTE,
            title=params.title,
            body=params.content.encode("utf-8"),
            mime="text/markdown",
            session_id=run.session_id,
            ticket_id=run.ticket_id,
            run_id=run.run_id,
        )
    _remember_note(run, artifact_id=str(artifact.id), title=artifact.title)
    return {
        "artifact_id": artifact.id,
        "title": artifact.title,
        "kind": artifact.kind,
        "mime": artifact.mime,
        "size_bytes": artifact.size_bytes,
        "updated": updated,
    }


def _remember_note(run: RunContext, *, artifact_id: str, title: str) -> None:
    """把笔记 id 与标题写进 ``ctx.state["durable"].notes``；同一 id 覆盖标题。"""
    durable = run.state.get("durable")
    if not isinstance(durable, DurableState):
        durable = DurableState(captured_at=datetime.now().astimezone().isoformat())
        run.state["durable"] = durable
    notes = [
        dict(item)
        for item in durable.notes
        if isinstance(item, dict) and item.get("id") != artifact_id
    ]
    notes.append({"id": artifact_id, "title": title})
    durable.notes = notes


async def update_plan(
    params: UpdatePlanParams, ctx: ToolContext
) -> dict[str, Any]:
    """提交一版完整计划；待批准时把 ``human_action`` 交给本轮收尾。"""
    run = require_run(kinds={"worker"}, message="update_plan 只能在 Worker 里调用")
    service = v3_service(run, ctx, "plan_service", "计划服务")
    try:
        plan = await service.create_or_update(
            session_id=run.session_id,
            ticket_id=run.ticket_id,
            title=params.title,
            items=[item.model_dump() for item in params.items],
        )
    except PlanInvalid as exc:
        # 不落库：状态机校验在服务的写事务之前失败（spec §4.5）。
        return {
            "error": {
                "code": PLAN_INVALID,
                "message": exc.message,
                "details": dict(exc.details or {}),
            }
        }
    result: dict[str, Any] = {
        "plan_id": str(plan.id),
        "status": str(plan.status),
        "items": [dict(item) for item in plan.items],
    }
    if str(plan.status) == PlanStatus.AWAITING_APPROVAL.value:
        action = plan_human_action(plan)
        run.state[PENDING_HUMAN_ACTION_KEY] = action
        result["human_action"] = action
    else:
        run.state.pop(PENDING_HUMAN_ACTION_KEY, None)
    return result
