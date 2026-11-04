"""技能端点（M78 规格 §4.3）。

四个操作对应注册表的四个方法，路由里不做合并、校验或状态码判断；属于 REST 层的
判断只有请求体的形状与多余字段，约束写在 :class:`SkillWriteRequest` 里，违反时走
FastAPI 原生的 ``422 VALIDATION_ERROR``（``details.fields`` 逐字段列表）。

路径参数**刻意不加正则**（规格 §4.3）：``skill_id`` 不合法时按动作区分——
GET / DELETE 是 404 ``SKILL_NOT_FOUND``，PUT 是 400 ``SKILL_INVALID``；加了正则
会先被 FastAPI 拦成 422，违反契约。

写操作各记一条服务日志（技能 id 与动作）。技能不是 Agent 工具调用，不进 Agent
审计，与知识库同口径。库里的时间是不带时区的 UTC，出参在这里补成 ``...Z``。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict

from maa_api.agent.skills.registry import (
    SkillDetail,
    SkillSummary,
    SkillWrite,
)
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["SkillList", "SkillWriteRequest", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/skills", tags=["agent"])


class SkillWriteRequest(SkillWrite):
    """``PUT`` 的入参：完整的生效内容。

    继承服务层的 :class:`~maa_api.agent.skills.registry.SkillWrite`（字段名一致），
    这里只补 REST 层的 ``extra="forbid"``；长度与工具 / 代理的校验在服务层，
    失败统一报 400 ``SKILL_INVALID``。
    """

    model_config = ConfigDict(extra="forbid")


class SkillList(BaseModel):
    """列表响应：列表项不含正文，只给字数与来源。"""

    items: list[SkillSummary]


def _service(request: Request):
    registry = getattr(request.app.state, "skill_registry", None)
    if registry is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "技能功能还在启动中，请稍后重试"
        )
    return registry


def _utc(value: datetime | None) -> datetime | None:
    """库里的 naive UTC → 带时区的 UTC，Pydantic 会把它渲染成 ``...Z``。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _with_z(detail: SkillDetail) -> SkillDetail:
    detail.updated_at = _utc(detail.updated_at)
    return detail


@router.get(
    "",
    response_model=SkillList,
    summary="读取技能列表",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_skills(request: Request) -> SkillList:
    items = await _service(request).list_skills()
    for item in items:
        item.updated_at = _utc(item.updated_at)
    return SkillList(items=items)


@router.get(
    "/{skill_id}",
    response_model=SkillDetail,
    summary="读取技能详情",
    responses=error_responses("SKILL_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_skill(
    request: Request,
    skill_id: str = Path(description="技能 id"),
) -> SkillDetail:
    return _with_z(await _service(request).get_skill(skill_id))


@router.put(
    "/{skill_id}",
    response_model=SkillDetail,
    summary="写入技能",
    responses=error_responses("SKILL_INVALID", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def put_skill(
    payload: SkillWriteRequest,
    request: Request,
    skill_id: str = Path(description="技能 id"),
) -> SkillDetail:
    """写入完整的生效内容：新建自定义技能，或修改 / 停用内置技能。

    内容不合法（含 id 不符合模式、未知工具或代理）返回 400 ``SKILL_INVALID``，
    ``details`` 带 ``field`` 与 ``reason``，未知工具与代理另带 ``unknown``。
    """
    detail = _with_z(await _service(request).put_skill(skill_id, payload))
    logger.info(
        "技能写入：id=%s source=%s modified=%s enabled=%s",
        detail.id,
        detail.source,
        detail.modified,
        detail.enabled,
    )
    return detail


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除技能",
    responses=error_responses("SKILL_NOT_FOUND", "SKILL_BUILTIN_READONLY", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def delete_skill(
    request: Request,
    skill_id: str = Path(description="技能 id"),
) -> Response:
    """删除自定义技能，或撤销对内置技能的修改（恢复默认）。

    内置技能没有覆盖可删时返回 409 ``SKILL_BUILTIN_READONLY``；要停用它请写入
    ``enabled=false``。
    """
    await _service(request).delete_skill(skill_id)
    logger.info("技能删除：id=%s", skill_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
