"""会话压缩与检查点端点（M74 规格 §4.3）。

两个端点都围绕 ``compaction_checkpoint``：``POST .../compact`` 是手动压缩入口，
``GET .../checkpoints`` 是检查点列表。会话是否存在、种类是否可压缩、有没有活动
运行、摘要是否可用，全部由启动时装配的 :class:`~maa_api.agent.context.compaction.Compactor`
判定并抛 :class:`~maa_api.domain.errors.AppError`（404 / 400 / 409 / 429 / 502），
路由只把结果映射成响应模型，不复制一套判定。

检查点列表按 ``created_at`` 降序；``degraded`` 取自 ``summary_json``（机械摘要与
摘要模型失败时由压缩器写入），时间字段一律 ``...Z``。SSE 的 ``compaction`` 与
WebSocket 的 ``compaction_created`` 由压缩器在压缩事务提交后发出，这里不重复发。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import CompactionCheckpoint
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from maa_api.domain.enums import CheckpointStatus, CompactionTrigger
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["CheckpointList", "CheckpointView", "CompactResultView", "router"]

router = APIRouter(prefix="/api/agent", tags=["agent"])


class CompactResultView(BaseModel):
    """手动压缩的结果；``skipped`` 为真时没有新检查点，前后估算相同。"""

    checkpoint_id: str | None
    skipped: bool
    tokens_before: int
    tokens_after: int


class CheckpointView(BaseModel):
    """一个压缩检查点的对外形状。

    ``summary`` 是读历史时原样塞回第一条 user 消息的正文；``degraded`` 为真表示
    摘要模型不可用、内容由规则生成；``model`` 为空同样表示机械摘要。
    """

    id: str
    session_id: str
    run_id: str | None
    upto_seq: int
    trigger: CompactionTrigger
    status: CheckpointStatus
    tokens_before: int
    tokens_after: int
    model: str | None
    degraded: bool
    summary: str
    created_at: str


class CheckpointList(BaseModel):
    """检查点列表；按 ``created_at`` 降序，最新的在最前。"""

    items: list[CheckpointView]


def _utc_z(value: datetime) -> str:
    """把库里的 naive UTC 时间渲染成 ``...Z``（与 ``RunSummary`` 同口径）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _checkpoint_view(checkpoint: CompactionCheckpoint) -> CheckpointView:
    summary_json: dict[str, Any] = checkpoint.summary_json or {}
    return CheckpointView(
        id=str(checkpoint.id),
        session_id=str(checkpoint.session_id),
        run_id=checkpoint.run_id,
        upto_seq=int(checkpoint.upto_seq),
        trigger=CompactionTrigger(checkpoint.trigger),
        status=CheckpointStatus(checkpoint.status),
        tokens_before=int(checkpoint.tokens_before),
        tokens_after=int(checkpoint.tokens_after),
        model=checkpoint.model,
        degraded=bool(summary_json.get("degraded", False)),
        summary=checkpoint.summary,
        created_at=_utc_z(checkpoint.created_at),
    )


@router.post(
    "/sessions/{session_id}/compact",
    response_model=CompactResultView,
    summary="手动压缩会话的较早消息",
    responses=error_responses(
        "AGENT_SESSION_NOT_FOUND",
        "INVALID_PARAMETER",
        "RUN_ALREADY_ACTIVE",
        "BUDGET_EXCEEDED",
        "COMPACTION_FAILED",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def compact_session(
    request: Request,
    session_id: str = Path(..., description="会话 id"),
) -> CompactResultView:
    """压缩一次会话；摘要不可用时 502 ``COMPACTION_FAILED``，不写检查点。"""
    result = await request.app.state.compactor.compact_session(session_id)
    return CompactResultView(
        checkpoint_id=result.checkpoint_id,
        skipped=result.skipped,
        tokens_before=result.tokens_before,
        tokens_after=result.tokens_after,
    )


@router.get(
    "/sessions/{session_id}/checkpoints",
    response_model=CheckpointList,
    summary="列出会话的压缩检查点",
    responses=error_responses("AGENT_SESSION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_checkpoints(
    session_id: str = Path(..., description="会话 id"),
    session: AsyncSession = Depends(get_session),
) -> CheckpointList:
    """按 ``created_at`` 降序返回会话的全部检查点（含已被取代的）。"""
    if await AgentSessionRepository(session).get(session_id) is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
    rows = await CompactionCheckpointRepository(session).list_for_session(session_id)
    return CheckpointList(items=[_checkpoint_view(row) for row in rows])
