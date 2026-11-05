"""消耗护栏：计划量、日界、用量、上限判定与账本写入（M84 spec §4.2、§4.5）。

这个模块是 ``spend_ledger`` 的**唯一写入者**与上限判定的唯一来源；调用方
（队列、运行中改参、预检）只通过 :class:`SpendGuard` 的三个入口与它交互：

- :meth:`SpendGuard.precheck_tool_call`：确认之前的**只读**预检。任何解析失败、
  任务行不存在或读库异常都直接放行——入队/改参时的预留才是最终裁决（spec §4.5.6）。
- :meth:`SpendGuard.reserve_submission` / :meth:`SpendGuard.reserve_patch`：
  在调用方的**同一事务**里判定并写账本；调用方在 ``async with`` 块内提交，
  提交后才放锁。超额时只抛错、不写行，随调用方会话一起回滚。

守卫锁是 :class:`SpendGuard` 自己的 ``asyncio.Lock``，**永远是最内层**：队列
路径先持 ``QueueService._admission_lock``、改参路径先持
``PipelineRunner.operation_lock``，然后才拿守卫锁；锁内不再拿别的锁
（spec §4.2、§8 的死锁风险对策）。

口径（spec §4.5、裁决 R1–R6）：

- 计划量按任务参数里的上限计，不从内核回调回填 ``actual``（R3）：Fight 只读
  ``medicine`` / ``stone``（不读 ``expiring_medicine``，R2），Recruit 只在
  ``expedite is True`` 时读 ``times``（不读 ``expedite_times``）。
- 消耗日从北京时间 04:00 起算（R4）；库里的 ``created_at`` 一律是 naive UTC。
- 用量 = ``actual``（有值）→ 「任务从未开始且流水线已终态」为 0 → 否则
  ``planned``；所有来源计入，只有 ``agent`` / ``scheduled`` 受限（R1）。
- 上限 ``-1`` 不限、``0`` 禁止；任一超额整条拒绝（不部分入队）。
- 运行中改参只看调高的差额，调低不减账（R5）；入队时预留额度，领取时不再
  检查（R6）。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import (
    AgentAudit,
    AgentSession,
    Pipeline,
    SpendLedger,
    Task,
    utcnow,
)
from maa_api.db.repositories.agent import AgentRunRepository
from maa_api.db.repositories.audit import AuditRepository
from maa_api.domain.enums import (
    AuditStatus,
    CallerType,
    PipelineSource,
    PipelineStatus,
    RiskLevel,
    SpendKind,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import (
    TASK_MODELS,
    ChannelDefaults,
    PipelineCreate,
    normalize,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "BLOCK_HISTORY_LIMIT",
    "CAPPED_SOURCES",
    "SPEND_DAY_RESET_HOUR",
    "SPEND_LABELS",
    "SPEND_TIMEZONE",
    "SPEND_TOOLS",
    "SpendGuard",
    "SpendViolation",
    "merged_task_params",
    "planned_spend",
    "record_spend_rejection",
    "spend_day_of",
    "spend_day_window",
]

logger = logging.getLogger(__name__)

#: 消耗日按国服每日刷新时刻切分（裁决 R4；spec §4.5.2）。
SPEND_TIMEZONE = "Asia/Shanghai"
SPEND_DAY_RESET_HOUR = 4

#: 只有代理与定时任务来源受限；manual 计入用量但不拦（裁决 R1）。
CAPPED_SOURCES = frozenset({PipelineSource.AGENT, PipelineSource.SCHEDULED})

#: 会消耗资源、需要确认前预检的工具（spec §4.5.6）。
SPEND_TOOLS = frozenset({"submit_pipeline", "set_task_params"})

#: 内存里保留的拦截记录条数（``blocked_today`` 的容量，spec §4.5.4）。
BLOCK_HISTORY_LIMIT = 50

SPEND_LABELS = {
    SpendKind.MEDICINE: "理智药",
    SpendKind.STONE: "源石",
    SpendKind.EXPEDITE: "加急许可",
}

#: 判定顺序固定：界面与错误 message 的第一种就是它（spec §4.3）。
_SPEND_KIND_ORDER: tuple[SpendKind, ...] = (
    SpendKind.MEDICINE,
    SpendKind.STONE,
    SpendKind.EXPEDITE,
)

_TZ = ZoneInfo(SPEND_TIMEZONE)
_TERMINAL_PIPELINE_STATUSES = (
    PipelineStatus.COMPLETED,
    PipelineStatus.FAILED,
    PipelineStatus.CANCELLED,
)


def _effective_amount() -> Any:
    """一行账本的有效量（spec §4.5.3）：actual → 未开始且终态为 0 → planned。

    两条查询（``usage`` 与 ``summary``）共用这一份 ``CASE``，避免两处口径漂移。
    """
    return case(
        (SpendLedger.actual.is_not(None), SpendLedger.actual),
        (
            and_(
                Task.id.is_not(None),
                Task.started_at.is_(None),
                Pipeline.status.in_(
                    [status.value for status in _TERMINAL_PIPELINE_STATUSES]
                ),
            ),
            0,
        ),
        else_=SpendLedger.planned,
    )


# ---------------------------------------------------------------------------
# 计划量
# ---------------------------------------------------------------------------
def _count(value: Any) -> int:
    """把一个参数值折成非负整数计数；布尔、负数、非整数一律 0（spec §4.5.1）。"""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def planned_spend(type_name: str, params: Mapping[str, Any]) -> dict[SpendKind, int]:
    """内核会收到的完整参数 → 计划消耗，只返回大于 0 的项（§4.5.1）。

    - ``Fight``：``medicine`` 与 ``stone``；**不读** ``expiring_medicine``
      （裁决 R2：48 小时内过期的药不计入理智药上限）。
    - ``Recruit``：只有 ``expedite is True`` 时按 ``times`` 计加急许可；
      ``expedite_times`` 不再被内核读取，因此也不读它。
    """
    out: dict[SpendKind, int] = {}
    if type_name == "Fight":
        out[SpendKind.MEDICINE] = _count(params.get("medicine"))
        out[SpendKind.STONE] = _count(params.get("stone"))
    elif type_name == "Recruit" and params.get("expedite") is True:
        out[SpendKind.EXPEDITE] = _count(params.get("times"))
    return {kind: value for kind, value in out.items() if value > 0}


def _field_aliases(model: type, name: str) -> set[str]:
    """一个模型字段可接受的键：Python 名、alias、序列化别名、校验别名。"""
    field = model.model_fields[name]
    aliases = {name}
    for alias in (field.alias, field.serialization_alias, field.validation_alias):
        if isinstance(alias, str):
            aliases.add(alias)
        else:
            choices = getattr(alias, "choices", None)
            if choices is not None:
                aliases.update(choice for choice in choices if isinstance(choice, str))
    return aliases


def _canonical_params(
    model: type, params: Mapping[str, Any]
) -> dict[str, Any]:
    """把按别名给的参数映回 Python 字段名，供 ``model_validate`` 使用。"""
    canonical: dict[str, Any] = {}
    for key, value in params.items():
        name = key
        for field_name in model.model_fields:
            if key in _field_aliases(model, field_name):
                name = field_name
                break
        canonical[name] = value
    return canonical


def merged_task_params(
    type_name: str, existing: Mapping[str, Any], patch: Mapping[str, Any]
) -> dict[str, Any]:
    """已有参数叠加补丁：用 TASK_MODELS 校验后按别名导出；校验失败退回字典合并。

    运行中改参的补丁可能用 Python 字段名也可能用 MAA 原名，已有行则是内核参数
    （别名）。校验成功时返回**完整**的别名参数（默认值补齐），供计划量直接读取；
    校验失败（``ValidationError`` / ``AppError``）时退回 ``{**existing, **patch}``，
    让预检按「参数不合法就放行、交给入队/改参裁决」的口径处理（spec §4.5.6）。
    """
    merged = {**dict(existing or {}), **dict(patch or {})}
    model = TASK_MODELS.get(type_name)
    if model is None:
        return merged
    try:
        validated = model.model_validate(
            {
                **_canonical_params(model, merged),
                "name": type_name,
            }
        )
    except (ValidationError, AppError):
        return merged
    return validated.model_dump(
        mode="python", by_alias=True, exclude_unset=False, exclude={"name"}
    )


# ---------------------------------------------------------------------------
# 日界与判定
# ---------------------------------------------------------------------------
def _as_naive_utc(moment: datetime) -> datetime:
    """带上时区的时间换算成 naive UTC；naive 原样返回（库内的统一表示）。"""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _day_start(day: date) -> datetime:
    """某个消耗日在北京时间 04:00 的 naive UTC 时刻。"""
    local = datetime.combine(day, time(SPEND_DAY_RESET_HOUR), tzinfo=_TZ)
    return local.astimezone(UTC).replace(tzinfo=None)


def spend_day_window(
    now: datetime | None = None,
) -> tuple[datetime, datetime, date]:
    """当前消耗日的 ``[start, end)``（naive UTC）与日期标签（§4.5.2）。

    ``now`` 是 naive UTC；先换成北京时间，减掉 4 小时后取日期 ``d``，窗口就是
    北京时间 ``d`` 04:00 到次日 04:00。因此 ``19:59Z``（北京 03:59）属于前一天，
    ``20:00Z``（北京 04:00）属于当天。
    """
    moment = _as_naive_utc(now if now is not None else utcnow())
    local = moment.replace(tzinfo=UTC).astimezone(_TZ)
    day = (local - timedelta(hours=SPEND_DAY_RESET_HOUR)).date()
    start = _day_start(day)
    return start, start + timedelta(days=1), day


def spend_day_of(created_at: datetime) -> date:
    """一条 naive UTC 时间属于哪个消耗日。"""
    return spend_day_window(created_at)[2]


def _iso_z(moment: datetime) -> str:
    """DTO 的 ISO 8601 表示：naive UTC 加 ``Z`` 后缀（spec §4.3）。"""
    return _as_naive_utc(moment).isoformat() + "Z"


@dataclass(frozen=True, slots=True)
class SpendViolation:
    """一种资源超额：``used + requested > limit``。"""

    kind: SpendKind
    limit: int
    used: int
    requested: int


def _violations(
    used: Mapping[SpendKind, int],
    requested: Mapping[SpendKind, int],
    limits: Mapping[SpendKind, int],
    source: PipelineSource,
) -> list[SpendViolation]:
    """按 §4.5.4 判定；非受限来源永远返回空列表。"""
    if source not in CAPPED_SOURCES:
        return []
    out: list[SpendViolation] = []
    for kind in _SPEND_KIND_ORDER:
        need = int(requested.get(kind, 0))
        if need <= 0:
            continue
        limit = int(limits.get(kind, -1))
        if limit == -1:
            continue
        current = int(used.get(kind, 0))
        if current + need > limit:
            out.append(SpendViolation(kind, limit, current, need))
    return out


def _violation_message(violation: SpendViolation) -> str:
    """两种逐字文案：一般超额与上限为 0（spec §4.3）。"""
    label = SPEND_LABELS[violation.kind]
    if violation.limit == 0:
        return f"今日禁止使用{label}（上限为 0）。可以在设置里调整"
    return (
        f"超过今日消耗上限：{label}今天已计 {violation.used}，"
        f"上限 {violation.limit}，本次需要 {violation.requested}。"
        "可以在设置里调高上限，或改为手动提交"
    )


def _violation_details(
    violations: Sequence[SpendViolation],
    *,
    source: PipelineSource,
    day_start: datetime,
) -> dict[str, Any]:
    """``SPEND_CAP_EXCEEDED`` 的 details：第一种 + 全量 ``violations``。"""
    first = violations[0]
    return {
        "kind": first.kind.value,
        "limit": first.limit,
        "used": first.used,
        "requested": first.requested,
        "source": source.value,
        "violations": [
            {
                "kind": item.kind.value,
                "limit": item.limit,
                "used": item.used,
                "requested": item.requested,
            }
            for item in violations
        ],
        "day_start": _iso_z(day_start),
    }


def _spend_error(
    violations: Sequence[SpendViolation],
    *,
    source: PipelineSource,
    day_start: datetime,
) -> AppError:
    """超额整条拒绝的错误：message 只写第一种，details 全列。"""
    return AppError(
        ErrorCode.SPEND_CAP_EXCEEDED,
        _violation_message(violations[0]),
        _violation_details(violations, source=source, day_start=day_start),
    )


@dataclass(frozen=True, slots=True)
class _BlockedSubmission:
    """内存里的一条拦截记录（字段同 ``SpendBlock`` DTO，时间先存 datetime）。"""

    at: datetime
    source: PipelineSource
    kind: SpendKind
    requested: int
    used: int
    limit: int
    ref: str | None


# ---------------------------------------------------------------------------
# 拦截审计
# ---------------------------------------------------------------------------
def _rejection_summary(details: Mapping[str, Any] | None) -> str:
    """从 details 造一句审计摘要；不依赖调用方再传 message。"""
    if not details:
        return "超过今日消耗上限"
    try:
        kind = SpendKind(str(details.get("kind")))
    except ValueError:
        return "超过今日消耗上限"
    label = SPEND_LABELS[kind]
    limit = details.get("limit")
    if limit == 0:
        return f"今日禁止使用{label}（上限为 0）"
    return (
        f"超过今日消耗上限：{label}今天已计 {details.get('used')}，"
        f"上限 {limit}，本次需要 {details.get('requested')}"
    )


async def record_spend_rejection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    caller: CallerType | str,
    tool_name: str,
    arguments: Mapping[str, Any],
    session_id: str | None,
    request_id: str | None,
    details: Mapping[str, Any] | None,
) -> int:
    """写一条 ``status=rejected`` 的消耗拦截审计（spec §4.5.6、§4.2）。

    独立事务：预检是只读路径，审计不能依赖调用方会话是否提交。``run_id`` /
    ``ticket_id`` / ``agent_name`` 从会话的活动运行反查（REST 调用没有会话，
    三列保持 NULL）；``arguments`` 由 ``AuditRepository.create`` 自动裁剪。
    返回审计 id，供调用方放进 ``ToolOutcome.audit_id``。
    """
    async with session_factory() as session:
        ticket_id: str | None = None
        run_id: str | None = None
        agent_name: str | None = None
        if session_id:
            try:
                agent_session = await session.get(AgentSession, session_id)
                if agent_session is not None:
                    ticket_id = agent_session.ticket_id
                active = await AgentRunRepository(session).get_active_for_session(
                    session_id
                )
                if active is not None:
                    run_id = active.id
                    agent_name = active.agent_name
            except Exception:  # noqa: BLE001 - 审计的上下文缺失不该丢掉拒绝记录
                logger.debug("消耗拦截审计：反查会话/运行失败", exc_info=True)
                ticket_id = run_id = agent_name = None
        row = await AuditRepository(session).create(
            AgentAudit(
                caller=caller.value if isinstance(caller, CallerType) else str(caller),
                tool_name=tool_name,
                request_id=request_id,
                arguments=dict(arguments or {}),
                status=AuditStatus.REJECTED,
                error_code=ErrorCode.SPEND_CAP_EXCEEDED.value,
                risk_level=RiskLevel.CONSUME,
                result_summary=_rejection_summary(details),
                result_ref=dict(details) if details is not None else None,
                session_id=session_id,
                run_id=run_id,
                ticket_id=ticket_id,
                agent_name=agent_name,
            )
        )
        audit_id = int(row.id)
        await session.commit()
        return audit_id


# ---------------------------------------------------------------------------
# 守卫
# ---------------------------------------------------------------------------
class _PrecheckSkip(Exception):
    """预检无法做出判断，按 spec 直接放行（不是错误）。"""


class SpendGuard:
    """消耗账本的唯一写入者与上限判定者（docs/v3/02 §2.2）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._clock = clock
        #: 守卫锁永远最内层（spec §4.2）；锁内不调用会拿别的锁的方法。
        self._lock = asyncio.Lock()
        self._blocked: deque[_BlockedSubmission] = deque(
            maxlen=BLOCK_HISTORY_LIMIT
        )

    # -- 读侧 ---------------------------------------------------------------
    def limits(self) -> dict[SpendKind, int]:
        """每次现读 ``spend.daily_*_limit``（改设置立即生效）。"""
        spend = self._settings_provider().spend
        return {
            SpendKind.MEDICINE: spend.daily_medicine_limit,
            SpendKind.STONE: spend.daily_stone_limit,
            SpendKind.EXPEDITE: spend.daily_expedite_limit,
        }

    async def usage(
        self, session: AsyncSession, *, start: datetime, end: datetime
    ) -> dict[SpendKind, int]:
        """窗口内各种类的已计用量（§4.5.3），三种都有键。"""
        statement = (
            select(SpendLedger.kind, func.sum(_effective_amount()))
            .select_from(SpendLedger)
            .outerjoin(Task, Task.id == SpendLedger.task_id)
            .outerjoin(Pipeline, Pipeline.id == SpendLedger.pipeline_id)
            .where(
                SpendLedger.kind.in_([kind.value for kind in SpendKind]),
                SpendLedger.created_at >= start,
                SpendLedger.created_at < end,
            )
            .group_by(SpendLedger.kind)
        )
        rows = (await session.execute(statement)).all()
        used: dict[SpendKind, int] = {kind: 0 for kind in SpendKind}
        for raw_kind, total in rows:
            try:
                kind = SpendKind(raw_kind)
            except ValueError:
                continue
            used[kind] = int(total or 0)
        return used

    def _blocked_payload(self, block: _BlockedSubmission) -> dict[str, Any]:
        return {
            "at": _iso_z(block.at),
            "source": block.source.value,
            "kind": block.kind.value,
            "requested": block.requested,
            "used": block.used,
            "limit": block.limit,
            "ref": block.ref,
        }

    def blocked_today(self) -> list[dict[str, Any]]:
        """今天（本进程启动以来）被拦下的提交，最新的在前。"""
        today = spend_day_window(self._clock())[2]
        return [
            self._blocked_payload(block)
            for block in self._blocked
            if spend_day_of(block.at) == today
        ]

    # -- 判定与记录 ---------------------------------------------------------
    def _remember_blocks(
        self,
        violations: Sequence[SpendViolation],
        *,
        source: PipelineSource,
        ref: str | None,
        now: datetime,
    ) -> None:
        """把本次拒绝写进内存 deque（每种资源一条），并写一行 logger.warning。"""
        # 倒序 appendleft：同一批 violation 在列表里仍按 medicine→stone→expedite
        # 排列，而新的提交整体排在旧提交之前（最新的在前）。
        for violation in reversed(violations):
            self._blocked.appendleft(
                _BlockedSubmission(
                    at=now,
                    source=source,
                    kind=violation.kind,
                    requested=violation.requested,
                    used=violation.used,
                    limit=violation.limit,
                    ref=ref,
                )
            )
        logger.warning(
            "消耗上限拦截：source=%s ref=%s %s",
            source.value,
            ref,
            "；".join(
                f"{SPEND_LABELS[item.kind]} 已计 {item.used}，"
                f"上限 {item.limit}，本次需要 {item.requested}"
                for item in violations
            ),
        )

    async def _ticket_for_session(
        self, session: AsyncSession, agent_session_id: str | None
    ) -> str | None:
        """从 ``agent_session`` 取工单；读不到就当没有（不拦正常提交）。"""
        if not agent_session_id:
            return None
        try:
            agent_session = await session.get(AgentSession, agent_session_id)
        except Exception:  # noqa: BLE001 - 工单只是账本/拦截记录的附加信息
            logger.debug("消耗预留：反查会话工单失败", exc_info=True)
            return None
        if agent_session is None:
            return None
        return agent_session.ticket_id

    # -- 预检 ---------------------------------------------------------------
    async def precheck_tool_call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        agent_session_id: str | None,
    ) -> None:
        """确认之前的只读预检，超额抛 AppError 并记一条拦截（spec §4.5.6）。

        参数解析失败、任务行不存在、读库异常一律直接返回：预检不是最终裁决，
        入队/改参时的预留会再查一次。
        """
        if name not in SPEND_TOOLS:
            return
        start, _, _ = spend_day_window(self._clock())
        try:
            async with self._session_factory() as session:
                requested = await self._precheck_requested(session, name, arguments)
                if not requested:
                    return
                ticket_id = await self._ticket_for_session(session, agent_session_id)
                end = start + timedelta(days=1)
                used = await self.usage(session, start=start, end=end)
        except _PrecheckSkip:
            return
        except (ValidationError, AppError):
            return
        except Exception:  # noqa: BLE001 - 预检失败放行，入队检查是最终裁决
            logger.warning("消耗预检读库失败，本次直接放行", exc_info=True)
            return

        violations = _violations(
            used, requested, self.limits(), PipelineSource.AGENT
        )
        if not violations:
            return

        ref = f"ticket:{ticket_id}" if ticket_id else f"tool:{name}"
        self._remember_blocks(
            violations, source=PipelineSource.AGENT, ref=ref, now=self._clock()
        )
        details = _violation_details(
            violations, source=PipelineSource.AGENT, day_start=start
        )
        try:
            await record_spend_rejection(
                self._session_factory,
                caller=(
                    CallerType.INTERNAL if agent_session_id else CallerType.REST
                ),
                tool_name=name,
                arguments=arguments,
                session_id=agent_session_id,
                request_id=(
                    f"session:{agent_session_id}" if agent_session_id else None
                ),
                details=details,
            )
        except Exception:  # noqa: BLE001 - 审计失败不能把拦截变成放行
            logger.warning("消耗拦截审计写入失败", exc_info=True)
        raise _spend_error(
            violations, source=PipelineSource.AGENT, day_start=start
        )

    async def _precheck_requested(
        self,
        session: AsyncSession,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[SpendKind, int]:
        """预检要追加的计划量；无法判断时抛 :class:`_PrecheckSkip`。"""
        if name == "submit_pipeline":
            try:
                payload = PipelineCreate.model_validate(dict(arguments))
            except (ValidationError, AppError) as exc:
                raise _PrecheckSkip from exc
            requested: dict[SpendKind, int] = {}
            for item in payload.tasks:
                normalized = normalize(item, ChannelDefaults())
                for kind, amount in planned_spend(
                    normalized.type_name, normalized.params
                ).items():
                    requested[kind] = requested.get(kind, 0) + amount
            return {
                kind: requested[kind]
                for kind in _SPEND_KIND_ORDER
                if requested.get(kind, 0) > 0
            }

        if name == "set_task_params":
            task_id = arguments.get("task_id")
            patch = arguments.get("params")
            if not isinstance(task_id, str) or not isinstance(patch, Mapping):
                raise _PrecheckSkip
            task = await session.get(Task, task_id)
            if task is None:
                raise _PrecheckSkip
            new_spend = planned_spend(
                task.type_name,
                merged_task_params(task.type_name, task.params or {}, patch),
            )
            old_spend = await self._task_planned(session, task.id)
            increases: dict[SpendKind, int] = {}
            for kind in _SPEND_KIND_ORDER:
                difference = new_spend.get(kind, 0) - old_spend.get(kind, 0)
                if difference > 0:
                    increases[kind] = difference
            return increases

        return {}

    async def _task_planned(
        self, session: AsyncSession, task_id: str | None
    ) -> dict[SpendKind, int]:
        """一个任务现有账本行的 planned 合计（没有行为 0）。"""
        planned: dict[SpendKind, int] = {kind: 0 for kind in SpendKind}
        if not task_id:
            return planned
        rows = (
            await session.execute(
                select(SpendLedger.kind, func.sum(SpendLedger.planned))
                .where(SpendLedger.task_id == task_id)
                .group_by(SpendLedger.kind)
            )
        ).all()
        for raw_kind, total in rows:
            try:
                kind = SpendKind(raw_kind)
            except ValueError:
                continue
            planned[kind] = int(total or 0)
        return planned

    # -- 预留 ---------------------------------------------------------------
    @asynccontextmanager
    async def reserve_submission(
        self,
        session: AsyncSession,
        *,
        source: PipelineSource,
        tasks: Sequence[Task],
        agent_session_id: str | None,
        ref: str | None = None,
    ) -> AsyncIterator[None]:
        """入队预留：持守卫锁判定，通过后把账本行加进 ``session`` 并 flush。

        调用方在 ``async with`` 块内提交，提交后才放锁（spec §4.2）。受限来源
        超额时抛错且**不写任何行**；manual 来源不判定但仍写账本行（R1）。
        """
        async with self._lock:
            now = self._clock()
            start, _, _ = spend_day_window(now)
            requested = _sum_requested(tasks)
            if not requested:
                # 只有 Copilot 等零消耗任务：不查账本、不写行，也不占额度。
                yield
                return
            ticket_id = await self._ticket_for_session(session, agent_session_id)
            if source in CAPPED_SOURCES:
                used = await self.usage(
                    session, start=start, end=start + timedelta(days=1)
                )
                violations = _violations(
                    used, requested, self.limits(), source
                )
                if violations:
                    block_ref = ref or (
                        f"ticket:{ticket_id}" if ticket_id else None
                    )
                    self._remember_blocks(
                        violations, source=source, ref=block_ref, now=now
                    )
                    raise _spend_error(
                        violations, source=source, day_start=start
                    )
            for task in tasks:
                for kind, amount in planned_spend(
                    task.type_name, task.params or {}
                ).items():
                    session.add(
                        SpendLedger(
                            pipeline_id=task.pipeline_id,
                            task_id=task.id,
                            kind=kind.value,
                            planned=amount,
                            source=source.value,
                            ticket_id=ticket_id,
                            created_at=now,
                        )
                    )
            await session.flush()
            yield

    @asynccontextmanager
    async def reserve_patch(
        self,
        session: AsyncSession,
        *,
        task: Task,
        merged_params: Mapping[str, Any],
        source: PipelineSource,
    ) -> AsyncIterator[None]:
        """运行中改参：按调高的差额判定，调高就改写或补账本行，调低不减（R5）。"""
        async with self._lock:
            now = self._clock()
            start, _, _ = spend_day_window(now)
            new_spend = planned_spend(task.type_name, merged_params)
            rows = list(
                (
                    await session.execute(
                        select(SpendLedger)
                        .where(SpendLedger.task_id == task.id)
                        .order_by(SpendLedger.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            old_spend: dict[SpendKind, int] = {kind: 0 for kind in SpendKind}
            by_kind: dict[SpendKind, SpendLedger] = {}
            for row in rows:
                try:
                    kind = SpendKind(row.kind)
                except ValueError:
                    continue
                old_spend[kind] += int(row.planned or 0)
                by_kind.setdefault(kind, row)

            increases = {
                kind: new_spend.get(kind, 0) - old_spend.get(kind, 0)
                for kind in _SPEND_KIND_ORDER
                if new_spend.get(kind, 0) > old_spend.get(kind, 0)
            }
            if increases and source in CAPPED_SOURCES:
                used = await self.usage(
                    session, start=start, end=start + timedelta(days=1)
                )
                violations = _violations(
                    used, increases, self.limits(), source
                )
                if violations:
                    self._remember_blocks(
                        violations, source=source, ref=None, now=now
                    )
                    raise _spend_error(
                        violations, source=source, day_start=start
                    )

            for kind in _SPEND_KIND_ORDER:
                amount = new_spend.get(kind, 0)
                if amount <= old_spend.get(kind, 0):
                    continue
                row = by_kind.get(kind)
                if row is not None:
                    row.planned = amount
                    session.add(row)
                else:
                    session.add(
                        SpendLedger(
                            pipeline_id=task.pipeline_id,
                            task_id=task.id,
                            kind=kind.value,
                            planned=amount,
                            source=source.value,
                            ticket_id=None,
                            created_at=now,
                        )
                    )
            if increases:
                await session.flush()
            yield

    # -- 汇总 ---------------------------------------------------------------
    async def summary(self, *, since: date | None = None) -> dict[str, Any]:
        """``GET /api/agent/spend`` 的响应体（§4.3）。

        一次查询取 ``since``（缺省今天往前 6 天）到今天窗口内的账本行，在
        Python 里按 :func:`spend_day_of` 分桶；``days`` 连续升序，缺数据的日期补 0。
        """
        now = self._clock()
        start, end, today = spend_day_window(now)
        first = since if since is not None else today - timedelta(days=6)
        buckets: dict[date, dict[SpendKind, int]] = {}
        if first <= today:
            statement = (
                select(
                    SpendLedger.kind, SpendLedger.created_at, _effective_amount()
                )
                .select_from(SpendLedger)
                .outerjoin(Task, Task.id == SpendLedger.task_id)
                .outerjoin(Pipeline, Pipeline.id == SpendLedger.pipeline_id)
                .where(
                    SpendLedger.kind.in_(
                        [kind.value for kind in SpendKind]
                    ),
                    SpendLedger.created_at >= _day_start(first),
                    SpendLedger.created_at < end,
                )
                .order_by(SpendLedger.created_at.asc())
            )
            async with self._session_factory() as session:
                rows = list((await session.execute(statement)).all())
            for raw_kind, created_at, amount in rows:
                try:
                    kind = SpendKind(raw_kind)
                except ValueError:
                    continue
                day = spend_day_of(created_at)
                bucket = buckets.setdefault(
                    day, {item: 0 for item in SpendKind}
                )
                bucket[kind] += int(amount or 0)

        limits = self.limits()
        today_used = buckets.get(today, {item: 0 for item in SpendKind})
        today_rows = [
            {
                "kind": kind.value,
                "label": SPEND_LABELS[kind],
                "used": today_used.get(kind, 0),
                "limit": limits[kind],
                "remaining": (
                    None
                    if limits[kind] == -1
                    else max(limits[kind] - today_used.get(kind, 0), 0)
                ),
            }
            for kind in _SPEND_KIND_ORDER
        ]

        day_rows: list[dict[str, Any]] = []
        cursor = first
        while cursor <= today:
            bucket = buckets.get(cursor, {item: 0 for item in SpendKind})
            day_rows.append(
                {
                    "date": cursor.isoformat(),
                    "medicine": bucket[SpendKind.MEDICINE],
                    "stone": bucket[SpendKind.STONE],
                    "expedite": bucket[SpendKind.EXPEDITE],
                }
            )
            cursor += timedelta(days=1)

        return {
            "date": today.isoformat(),
            "day_start": _iso_z(start),
            "day_end": _iso_z(end),
            "today": today_rows,
            "days": day_rows,
            "blocked_today": self.blocked_today(),
            "capped_sources": [
                PipelineSource.AGENT.value,
                PipelineSource.SCHEDULED.value,
            ],
        }


def _sum_requested(tasks: Sequence[Task]) -> dict[SpendKind, int]:
    """一条流水线的全部任务计划量，按种类求和；只保留大于 0 的项。"""
    totals: dict[SpendKind, int] = {}
    for task in tasks:
        for kind, amount in planned_spend(
            task.type_name, task.params or {}
        ).items():
            totals[kind] = totals.get(kind, 0) + amount
    return {
        kind: totals[kind]
        for kind in _SPEND_KIND_ORDER
        if totals.get(kind, 0) > 0
    }
