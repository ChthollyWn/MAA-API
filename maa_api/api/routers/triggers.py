"""事件与事件规则端点（M81 规格 §4.3）。

六个操作与 §4.3 的表一一对应：事件记录分页、规则列表、规则写入 / 删除、观察记录
分页与审阅。规则合并、校验与统计在 :class:`~maa_api.agent.events.rules.TriggerRuleService`，
事件入表与路由在 :class:`~maa_api.agent.events.bus.TriggerService`；路由层只做分页
范围、请求体形状与三个业务错误码的出口，响应模型写在本文件里（``RuleStats`` 与
``TriggerRuleOut`` 复用 G2 已定义的模型，不搬家）。

服务未装配（lifespan 还在启动）时统一回 503 ``SERVICE_UNAVAILABLE``，与其它路由同款。
库里的时间是不带时区的 UTC，出参在这里补成 ``...Z``；``next_fire_at`` 由 APScheduler
给出带时区的时间，同样折算成 UTC。观察记录写库时的 ``observation_created`` 广播在
``TriggerService.write_observation`` 里，本层不重复广播。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from maa_api.agent.events.kinds import summarize
from maa_api.agent.events.rules import (
    RULE_ID_PATTERN,
    TriggerRuleOut,
    TriggerRuleWrite,
)
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import AgentTrigger, TriggerObservation, utcnow
from maa_api.db.repositories.triggers import (
    TriggerObservationRepository,
    TriggerRepository,
)
from maa_api.domain.enums import (
    TriggerAdmission,
    TriggerEvalStatus,
    TriggerRoute,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "ObservationOut",
    "ObservationPage",
    "ObservationVerdictIn",
    "TriggerOut",
    "TriggerPage",
    "TriggerRuleList",
    "router",
]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 列表分页上限；与 ``GET /api/agent/memories`` 同口径（§4.3）。
MAX_PAGE_SIZE = 200


class TriggerOut(BaseModel):
    """事件记录的一条（§4.3）。"""

    id: int
    source: str
    kind: str
    thread_key: str
    sender: str | None
    summary: str
    payload: dict[str, Any]
    dedupe_key: str | None
    admission: TriggerAdmission
    rule_id: str | None
    eval_status: TriggerEvalStatus
    eval_reason: str | None
    eval_run_id: str | None
    ticket_id: str | None
    routed_to: TriggerRoute | None
    created_at: datetime
    processed_at: datetime | None


class TriggerPage(BaseModel):
    """事件记录的分页信封（§4.3）。"""

    items: list[TriggerOut]
    total: int
    page: int
    size: int


class TriggerRuleList(BaseModel):
    """规则列表：全部生效规则 + 规则集版本哈希（§4.3）。"""

    items: list[TriggerRuleOut]
    version: str


class ObservationOut(BaseModel):
    """观察记录的一条（§4.3）；``trigger_*`` 来自关联事件。"""

    id: int
    trigger_id: int
    rule_id: str
    decision: dict[str, Any]
    verdict: Literal["good", "bad"] | None
    reviewed_at: datetime | None
    created_at: datetime
    trigger_kind: str
    trigger_summary: str
    thread_key: str


class ObservationPage(BaseModel):
    """观察记录的分页信封（§4.3）。"""

    items: list[ObservationOut]
    total: int
    page: int
    size: int


class ObservationVerdictIn(BaseModel):
    """``POST .../verdict`` 的入参：只能标 good / bad，可改判。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["good", "bad"]


def _utc(value: datetime | None) -> datetime | None:
    """库里的 naive UTC → 带时区的 UTC，Pydantic 会把它渲染成 ``...Z``。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _trigger_service(request: Request) -> Any:
    service = getattr(request.app.state, "trigger_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "事件功能还在启动中，请稍后重试"
        )
    return service


def _rule_service(request: Request) -> Any:
    service = getattr(request.app.state, "trigger_rule_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "事件功能还在启动中，请稍后重试"
        )
    return service


def _session_factory(request: Request) -> Any:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "事件功能还在启动中，请稍后重试"
        )
    return factory


def _validate_pagination(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.INVALID_PAGINATION,
            f"page 不能小于 1，size 需在 1–{MAX_PAGE_SIZE} 之间",
        )


def _trigger_out(row: AgentTrigger) -> TriggerOut:
    return TriggerOut(
        id=int(row.id),
        source=row.source,
        kind=row.kind,
        thread_key=row.thread_key,
        sender=row.sender,
        summary=summarize(row.kind, row.payload or {}),
        payload=dict(row.payload or {}),
        dedupe_key=row.dedupe_key,
        admission=TriggerAdmission(row.admission),
        rule_id=row.rule_id,
        eval_status=TriggerEvalStatus(row.eval_status),
        eval_reason=row.eval_reason,
        eval_run_id=row.eval_run_id,
        ticket_id=row.ticket_id,
        routed_to=TriggerRoute(row.routed_to) if row.routed_to else None,
        created_at=_utc(row.created_at),
        processed_at=_utc(row.processed_at),
    )


def _observation_out(
    observation: TriggerObservation, trigger: AgentTrigger | None
) -> ObservationOut:
    return ObservationOut(
        id=int(observation.id),
        trigger_id=int(observation.trigger_id),
        rule_id=observation.rule_id,
        decision=dict(observation.decision or {}),
        verdict=observation.verdict,
        reviewed_at=_utc(observation.reviewed_at),
        created_at=_utc(observation.created_at),
        trigger_kind=trigger.kind if trigger is not None else "",
        trigger_summary=(
            summarize(trigger.kind, trigger.payload or {})
            if trigger is not None
            else ""
        ),
        thread_key=trigger.thread_key if trigger is not None else "",
    )


def _with_utc(rule: TriggerRuleOut) -> TriggerRuleOut:
    rule.next_fire_at = _utc(rule.next_fire_at)
    return rule


# ---------------------------------------------------------------------------
# 事件记录
# ---------------------------------------------------------------------------
@router.get(
    "/triggers",
    response_model=TriggerPage,
    summary="分页读取事件记录",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_triggers(
    request: Request,
    source: str | None = Query(default=None, description="按来源过滤"),
    kind: str | None = Query(default=None, description="按事件种类过滤"),
    rule_id: str | None = Query(default=None, description="按命中的规则 id 过滤"),
    page: int = Query(default=1, description="页码，从 1 开始"),
    size: int = Query(default=20, description="每页条数，1–200"),
) -> TriggerPage:
    """按来源 / 种类 / 规则过滤，``id`` 倒序分页（§4.3）。"""
    _validate_pagination(page, size)
    _trigger_service(request)
    async with _session_factory(request)() as session:
        result = await TriggerRepository(session).page(
            source=source,
            kind=kind,
            rule_id=rule_id,
            page=page,
            size=size,
        )
    return TriggerPage(
        items=[_trigger_out(row) for row in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------
@router.get(
    "/trigger-rules",
    response_model=TriggerRuleList,
    summary="读取事件规则列表",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_trigger_rules(request: Request) -> TriggerRuleList:
    """全部规则（内置 + 覆盖 + 用户），附 30 天统计与升降档建议（§4.5.6）。"""
    service = _rule_service(request)
    items = [_with_utc(rule) for rule in await service.list_rules()]
    rule_set = await service.rule_set()
    return TriggerRuleList(items=items, version=rule_set.version)


@router.put(
    "/trigger-rules/{rule_id}",
    response_model=TriggerRuleOut,
    summary="写入事件规则",
    responses=error_responses(
        "TRIGGER_RULE_INVALID", "VALIDATION_ERROR", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def put_trigger_rule(
    payload: TriggerRuleWrite,
    request: Request,
    rule_id: str = Path(
        pattern=RULE_ID_PATTERN.pattern, description="规则 id（2–48 位小写字母、数字与连字符）"
    ),
) -> TriggerRuleOut:
    """写入完整的生效内容：覆盖内置规则，或新建 / 修改用户规则（§4.3）。"""
    return _with_utc(await _rule_service(request).put_rule(rule_id, payload))


@router.delete(
    "/trigger-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除事件规则",
    responses=error_responses(
        "TRIGGER_RULE_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def delete_trigger_rule(
    request: Request,
    rule_id: str = Path(
        pattern=RULE_ID_PATTERN.pattern, description="规则 id（2–48 位小写字母、数字与连字符）"
    ),
) -> Response:
    """删除用户规则；内置规则只删覆盖行（幂等），其余 404（§4.3）。"""
    await _rule_service(request).delete_rule(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 观察记录
# ---------------------------------------------------------------------------
@router.get(
    "/observations",
    response_model=ObservationPage,
    summary="分页读取观察记录",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_observations(
    request: Request,
    rule_id: str | None = Query(default=None, description="按规则 id 过滤"),
    reviewed: bool | None = Query(default=None, description="是否已审阅"),
    page: int = Query(default=1, description="页码，从 1 开始"),
    size: int = Query(default=20, description="每页条数，1–200"),
) -> ObservationPage:
    """按规则 / 是否已审阅过滤，``created_at`` 倒序分页（§4.3）。"""
    _validate_pagination(page, size)
    _trigger_service(request)
    async with _session_factory(request)() as session:
        result = await TriggerObservationRepository(session).page(
            rule_id=rule_id, reviewed=reviewed, page=page, size=size
        )
        items = []
        for observation in result.items:
            trigger = await session.get(AgentTrigger, observation.trigger_id)
            items.append(_observation_out(observation, trigger))
    return ObservationPage(
        items=items,
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.post(
    "/observations/{observation_id}/verdict",
    response_model=ObservationOut,
    summary="审阅一条观察记录",
    responses=error_responses(
        "OBSERVATION_NOT_FOUND", "VALIDATION_ERROR", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def review_observation(
    payload: ObservationVerdictIn,
    request: Request,
    observation_id: int = Path(description="观察记录 id"),
) -> ObservationOut:
    """标记「判断对了 / 不该处理」；再次提交即改判（§4.3）。"""
    _trigger_service(request)
    async with _session_factory(request)() as session:
        repository = TriggerObservationRepository(session)
        observation = await repository.get(observation_id)
        if observation is None:
            raise AppError(
                ErrorCode.OBSERVATION_NOT_FOUND,
                "没有这条观察记录",
                {"observation_id": observation_id},
            )
        trigger = await session.get(AgentTrigger, observation.trigger_id)
        observation.verdict = payload.verdict
        observation.reviewed_at = utcnow()
        await session.commit()
        return _observation_out(observation, trigger)
