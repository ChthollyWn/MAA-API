"""目标服务：建目标、规划/复盘结果落库、复盘 job 与状态机（M82 规格 §4.2、§4.5）。

约定：

- **事务边界**：本服务自己开会话、自己提交。调用外部服务（工单、话题、定时任务、
  养成计划）时先结束自己的读写会话，避免在 SQLite 上拿着读事务去等写事务。
- **业务判断都在这里**：仓储只回答「有哪些行」，里程碑状态、暂停时停用哪些定时
  任务、是否完成由本服务决定（规格 §4.5.2–§4.5.5）。
- **不挂汇报监听器**：``on_ticket_report`` 是给 G3 装配监听器用的入口，本任务只实现
  与测试它（spec §4.2）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select

from maa_api.agent.events.rules import RULE_CRON_TIMEZONE
from maa_api.agent.goals.models import (
    GoalCreate,
    GoalDetail,
    GoalPage,
    GoalPlanDetails,
    GoalReviewDetails,
    GoalSummary,
    GoalUpdate,
    goal_detail,
    goal_summary,
)
from maa_api.agent.policy import _consumption_reasons
from maa_api.agent.registry import ToolContext
from maa_api.db.models import (
    AgentSession,
    Goal,
    GoalMilestone,
    UpgradePlan,
    utcnow,
)
from maa_api.db.repositories.goals import GoalMilestoneRepository, GoalRepository
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import (
    AgentSessionKind,
    EventSource,
    GoalStatus,
    MandateStrength,
    MilestoneStatus,
    ReportStatus,
    RiskLevel,
    TicketOrigin,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.schedule_service import cron_trigger

__all__ = [
    "DEFAULT_REVIEW_CRON",
    "GOAL_REVIEW_JOB_PREFIX",
    "GoalService",
    "planning_brief",
    "planning_scope",
]

logger = logging.getLogger(__name__)

#: 复盘 job 的 id 前缀；完整 id 是 ``maa-goal-review-<goal_id>``（spec §4.5.4）。
GOAL_REVIEW_JOB_PREFIX = "maa-goal-review-"
#: 设置里的默认 cron 也解析不了时的兜底（spec §4.4）。
DEFAULT_REVIEW_CRON = "0 22 * * *"
#: 复盘 job 的 misfire 宽限（秒，spec §4.5.4）。
GOAL_REVIEW_MISFIRE_GRACE_SECONDS = 300
#: ``progress.notes`` 最多保留的条数（spec §4.1）。
MAX_PROGRESS_NOTES = 10
#: 单条处理说明的字符上限（避免把模型的长报错误整段塞进 JSON）。
_MAX_NOTE_CHARS = 200

_ENDED_STATUSES = frozenset(
    {GoalStatus.DONE.value, GoalStatus.FAILED.value, GoalStatus.CANCELLED.value}
)
_ACTIVE_GOAL_STATUSES = (GoalStatus.ACTIVE.value, GoalStatus.PAUSED.value)
_INACTIVE_MILESTONE_STATUSES = frozenset(
    {MilestoneStatus.DONE.value, MilestoneStatus.SKIPPED.value}
)
_OVERDUE_MILESTONE_STATUSES = frozenset(
    {MilestoneStatus.PENDING.value, MilestoneStatus.ACTIVE.value}
)
_GOAL_REVIEW_KIND = "goal.review_due"
_GOAL_COMPLETED_KIND = "goal.completed"
_GOAL_DEADLINE_KIND = "goal.deadline_passed"


# ---------------------------------------------------------------------------
# 时间与文案
# ---------------------------------------------------------------------------
def _naive_utc(value: datetime) -> datetime:
    """带时区时间 → naive UTC；按库内口径，naive 输入原样返回。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _iso_z(value: datetime | None) -> str | None:
    """naive UTC → ``...Z`` 的 ISO 字符串（事件 payload 与快照共用）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _beijing_text(value: datetime) -> str:
    """naive UTC 的截止时间渲染成北京时间的 ``YYYY-MM-DD HH:MM``。"""
    moment = value.replace(tzinfo=UTC).astimezone(ZoneInfo(RULE_CRON_TIMEZONE))
    return moment.strftime("%Y-%m-%d %H:%M")


def _review_time_text(cron: str) -> str:
    """从五段 cron 取 ``HH:MM``；形状不认识时原样返回 cron。"""
    fields = cron.split()
    if len(fields) == 5 and fields[0].isdigit() and fields[1].isdigit():
        return f"{int(fields[1]):02d}:{int(fields[0]):02d}"
    return cron


def planning_brief(goal: Any) -> str:
    """规划工单的任务说明（spec §4.7，逐字）。"""
    target = goal.target if isinstance(goal.target, Mapping) else {}
    operators = target.get("operators") or []
    description = goal.description or "没有"
    deadline = _beijing_text(goal.deadline) if goal.deadline else "没有截止"
    review_time = _review_time_text(str(goal.review_cron))
    operator_text = (
        json.dumps(operators, ensure_ascii=False) if operators else "没有"
    )
    return (
        f"目标「{goal.title}」刚建立，由你做规划。\n"
        f"目标：{target.get('text') or goal.title}\n"
        f"补充说明：{description}\n"
        f"练度目标：{operator_text}\n"
        f"截止：{deadline}；每天 {review_time} 复盘。\n"
        "\n"
        "要做的事：\n"
        "1. 弄清现状：练度（get_operator_box）、仓库（get_depot）、相关关卡与活动。\n"
        "2. 把目标拆成 1–12 个里程碑，每个写标题、预计完成时间与怎么判断完成。\n"
        "3. 要按计划长期刷材料就用 start_upgrade_plan 建养成计划，它会请用户确认一次；"
        "其他需要定时做的事用 create_schedule。\n"
        "4. 不要用理智药和源石，除非补充说明里写明可以用。\n"
        "5. 只改动为这个目标新建的养成计划与定时任务。\n"
        "\n"
        "完成后 report 汇报 ready，details 写：\n"
        '{"goal_plan": {"milestones": [{"title": "…", "due_at": '
        '"2026-11-25T22:00:00+08:00", "metrics": {"how": "…"}}], '
        '"schedule_ids": ["…"], "summary": "…"}}\n'
        "养成计划不用写进 details，系统会按这张工单找到它。"
    )


def planning_scope(goal: Any) -> str:
    """规划工单的委托范围（spec §4.7，逐字）。"""
    return (
        f"为目标「{goal.title}」做规划：拆里程碑，按需建养成计划与定时任务；"
        "只改动为这个目标新建的计划与定时任务，消耗类操作逐次请用户确认。"
    )


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _status_text(value: Any) -> str:
    """枚举或字符串统一成字符串值。"""
    if isinstance(value, (ReportStatus, GoalStatus, MilestoneStatus, TicketStatus)):
        return value.value
    return str(value)


def _note(progress: Mapping[str, Any], text: str) -> dict[str, Any]:
    """返回一份追加了处理说明的新 ``progress``（最近 10 条）。"""
    updated = dict(progress or {})
    notes = [str(item) for item in (updated.get("notes") or [])]
    notes.append(text[:_MAX_NOTE_CHARS])
    updated["notes"] = notes[-MAX_PROGRESS_NOTES:]
    return updated


def _cron_or_default(value: Any) -> str:
    """字符串 cron；空值或非字符串回落到默认。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_REVIEW_CRON


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
class GoalService:
    """目标的建立、列表、详情、修改与复盘（spec §4.2）。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        app: Any,
        ticket_service: Any,
        thread_router: Any,
        trigger_service: Any,
        upgrade_plan_service: Any,
        get_schedule_service: Callable[[], Any],
        settings_provider: Callable[[], Any],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._app = app
        self._ticket_service = ticket_service
        self._thread_router = thread_router
        self._trigger_service = trigger_service
        self._upgrade_plan_service = upgrade_plan_service
        self._get_schedule_service = get_schedule_service
        self._settings_provider = settings_provider
        self._clock = clock
        #: 复盘 job 的调度器；``start_jobs`` 传进来，未装配时为 ``None``。
        self._scheduler: Any = None
        self._owned_job_ids: set[str] = set()
        #: ``start_jobs`` 首次按库里的目标注册 job；测试可 await 它以消除竞态。
        self._jobs_ready: asyncio.Task[Any] | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # 读设置
    # ------------------------------------------------------------------
    async def _settings(self) -> Any:
        value = self._settings_provider()
        if inspect.isawaitable(value):
            value = await value
        return value

    @property
    def scheduler(self) -> Any:
        """当前调度器（测试与 ``next_review_at`` 用）。"""
        return self._scheduler

    # ------------------------------------------------------------------
    # 建立
    # ------------------------------------------------------------------
    async def create(
        self,
        payload: GoalCreate,
        *,
        source: EventSource = EventSource.WEB,
        sender: str = "web",
    ) -> GoalDetail:
        """建目标、规划工单与复盘 job（spec §4.5.1）。"""
        settings = await self._settings()
        goals_settings = getattr(settings, "goals", None)
        if goals_settings is None or not bool(
            getattr(goals_settings, "enabled", True)
        ):
            raise AppError(
                ErrorCode.AGENT_DISABLED,
                "目标功能已在设置里关闭，打开后再建目标",
            )

        cron = payload.review_cron or getattr(
            goals_settings, "default_review_cron", DEFAULT_REVIEW_CRON
        )
        cron = _cron_or_default(cron)
        try:
            cron_trigger(cron, RULE_CRON_TIMEZONE)
        except (ValueError, TypeError):
            if payload.review_cron:
                raise AppError(
                    ErrorCode.SCHEDULE_CRON_INVALID,
                    f"复盘时间不合法：{payload.review_cron}",
                    {"review_cron": payload.review_cron},
                ) from None
            logger.warning(
                "目标默认复盘时间不是合法 cron，回退到 %s：%s",
                DEFAULT_REVIEW_CRON,
                cron,
            )
            cron = DEFAULT_REVIEW_CRON
            cron_trigger(cron, RULE_CRON_TIMEZONE)

        now = self._clock()
        goal = Goal(
            title=payload.title,
            description=payload.description,
            target=payload.target.model_dump(mode="json"),
            deadline=self._deadline_value(payload.deadline),
            status=GoalStatus.ACTIVE.value,
            review_cron=cron,
            progress={"planning": "pending"},
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            stored = await GoalRepository(session).create(goal)
            await session.commit()
        goal_id = str(stored.id)

        try:
            ticket, _mandate = await self._ticket_service.create(
                kind="planner",
                title=f"规划：{payload.title}"[:64],
                brief=planning_brief(stored),
                scope=planning_scope(stored),
                origin=TicketOrigin.GOAL,
                strength=MandateStrength.EXPLICIT,
                # ``ORIGIN_SOURCES[goal]`` 只接受 ``EventSource.GOAL``；用户从
                # web 建目标时，规划工单仍由目标系统发起（见 T3 报告）。
                source=EventSource.GOAL,
                sender=sender,
                rule_id=None,
                risk_ceiling=RiskLevel.CONSUME,
                goal_id=goal_id,
            )
            await self._thread_router.subscribe(ticket.id, f"goal:{goal_id}")
        except Exception as exc:  # noqa: BLE001 - 建单失败目标仍保留
            logger.warning("目标 %s 的规划工单没建起来：%s", goal_id, exc)
            await self._mark_planning(goal_id, "failed", f"规划没开始：{exc}")
            return await self.get(goal_id)

        async with self._session_factory() as session:
            goals = GoalRepository(session)
            stored = await goals.get(goal_id)
            if stored is not None:
                stored.planning_ticket_id = ticket.id
                stored.updated_at = self._clock()
                await goals.update(stored)
                await session.commit()

        if stored is not None:
            self._register_review_job(stored)
        return await self.get(goal_id)

    async def _mark_planning(
        self, goal_id: str, planning: str, note: str | None = None
    ) -> None:
        async with self._session_factory() as session:
            goals = GoalRepository(session)
            goal = await goals.get(goal_id)
            if goal is None:
                return
            progress = dict(goal.progress or {})
            progress["planning"] = planning
            if note:
                progress = _note(progress, note)
            goal.progress = progress
            goal.updated_at = self._clock()
            await goals.update(goal)
            await session.commit()

    def _deadline_value(self, value: datetime | None) -> datetime | None:
        """校验并转换截止时间：必须带时区，入库 naive UTC。"""
        if value is None:
            return None
        if value.tzinfo is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, "截止时间要带时区"
            )
        return _naive_utc(value)

    # ------------------------------------------------------------------
    # 列表与详情
    # ------------------------------------------------------------------
    async def list_goals(
        self,
        *,
        statuses: list[GoalStatus] | None,
        page: int,
        size: int,
    ) -> GoalPage:
        """按状态过滤分页（``updated_at`` 倒序）。"""
        values = [str(status) for status in statuses] if statuses else None
        async with self._session_factory() as session:
            result = await GoalRepository(session).page(
                values, page=page, size=size
            )
            milestones = GoalMilestoneRepository(session)
            items: list[GoalSummary] = []
            for goal in result.items:
                rows = await milestones.list_by_goal(str(goal.id))
                items.append(
                    goal_summary(
                        goal,
                        rows,
                        next_review_at=self._next_review_at(str(goal.id)),
                    )
                )
        return GoalPage(
            items=items,
            total=int(result.total),
            page=int(result.page),
            size=int(result.size),
        )

    async def get(self, goal_id: str) -> GoalDetail:
        """目标详情；不存在抛 ``GOAL_NOT_FOUND``。"""
        async with self._session_factory() as session:
            goal = await GoalRepository(session).get(goal_id)
            if goal is None:
                raise AppError(
                    ErrorCode.GOAL_NOT_FOUND, "没有这个目标", {"goal_id": goal_id}
                )
            milestones = await GoalMilestoneRepository(session).list_by_goal(
                goal_id
            )
            tickets = await TicketRepository(session).list_by_goal(
                goal_id, limit=20
            )
            return goal_detail(
                goal,
                milestones,
                tickets,
                next_review_at=self._next_review_at(goal_id),
            )

    # ------------------------------------------------------------------
    # 修改
    # ------------------------------------------------------------------
    async def update(self, goal_id: str, payload: GoalUpdate) -> GoalDetail:
        """改标题、截止或状态（暂停/恢复/取消，spec §4.5.5）。"""
        async with self._session_factory() as session:
            goal = await GoalRepository(session).get(goal_id)
            if goal is None:
                raise AppError(
                    ErrorCode.GOAL_NOT_FOUND, "没有这个目标", {"goal_id": goal_id}
                )
            if str(goal.status) in _ENDED_STATUSES:
                raise AppError(
                    ErrorCode.GOAL_ENDED, "目标已结束", {"goal_id": goal_id}
                )

        fields = payload.model_fields_set
        changed = False
        now = self._clock()
        if "title" in fields and payload.title is not None:
            if payload.title != goal.title:
                goal.title = payload.title
                changed = True
        if "deadline" in fields:
            deadline = self._deadline_value(payload.deadline)
            goal.deadline = deadline
            changed = True
            if deadline is not None and deadline > now:
                progress = dict(goal.progress or {})
                progress["deadline_notified"] = False
                goal.progress = progress

        status = payload.status if "status" in fields else None
        if status is not None and status != str(goal.status):
            if status == GoalStatus.CANCELLED.value:
                await self._cancel(goal)
            elif (
                status == GoalStatus.PAUSED.value
                and str(goal.status) == GoalStatus.ACTIVE.value
            ):
                await self._pause(goal)
            elif (
                status == GoalStatus.ACTIVE.value
                and str(goal.status) == GoalStatus.PAUSED.value
            ):
                await self._resume(goal)
            changed = True

        goal.updated_at = now
        async with self._session_factory() as session:
            await GoalRepository(session).update(goal)
            await session.commit()
        return await self.get(goal_id)

    # ------------------------------------------------------------------
    # 暂停 / 恢复 / 取消
    # ------------------------------------------------------------------
    async def _pause(self, goal: Goal) -> None:
        """停用关联定时任务并暂停复盘 job（spec §4.5.5）。"""
        disabled: list[str] = []
        notes: list[str] = []
        for schedule_id in list(goal.schedule_ids or []):
            try:
                view = await self._schedule_get(str(schedule_id))
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {schedule_id} 已不存在")
                    continue
                raise
            if not bool(getattr(view, "enabled", False)):
                continue
            try:
                await self._schedule_patch(str(schedule_id), {"enabled": False})
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {schedule_id} 已不存在")
                    continue
                raise
            disabled.append(str(schedule_id))
        progress = dict(goal.progress or {})
        progress["paused_schedule_ids"] = disabled
        for note in notes:
            progress = _note(progress, note)
        goal.progress = progress
        goal.status = GoalStatus.PAUSED.value
        goal.updated_at = self._clock()
        self._pause_job(str(goal.id))

    async def _resume(self, goal: Goal) -> None:
        """重新启用暂停时停用的定时任务并恢复复盘 job（spec §4.5.5）。"""
        progress = dict(goal.progress or {})
        paused = [str(item) for item in (progress.get("paused_schedule_ids") or [])]
        notes: list[str] = []
        for schedule_id in paused:
            try:
                view = await self._schedule_get(schedule_id)
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {schedule_id} 已不存在")
                    continue
                raise
            if bool(getattr(view, "enabled", False)):
                # 暂停后用户自己启用了它：恢复时跳过（spec §4.5.8）。
                continue
            try:
                await self._schedule_patch(schedule_id, {"enabled": True})
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {schedule_id} 已不存在")
                    continue
                raise
        progress["paused_schedule_ids"] = []
        for note in notes:
            progress = _note(progress, note)
        goal.progress = progress
        goal.status = GoalStatus.ACTIVE.value
        goal.updated_at = self._clock()
        self._resume_job(str(goal.id))

    async def _cancel(self, goal: Goal) -> None:
        """取消目标：停计划、停任务、取消未结束工单、移除 job（spec §4.5.5）。"""
        notes: list[str] = []
        plan_id = goal.upgrade_plan_id
        if plan_id:
            status = await self._plan_status(str(plan_id))
            if status == "active":
                try:
                    await self._upgrade_plan_service.cancel(str(plan_id))
                except AppError as exc:
                    notes.append(f"养成计划取消失败：{exc.message}")
                except Exception as exc:  # noqa: BLE001 - 取消目标不能被计划拖垮
                    logger.warning("目标 %s 取消养成计划失败：%s", goal.id, exc)
                    notes.append(f"养成计划取消失败：{exc}")

        for schedule_id in list(goal.schedule_ids or []):
            try:
                view = await self._schedule_get(str(schedule_id))
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    continue
                raise
            if not bool(getattr(view, "enabled", False)):
                continue
            try:
                await self._schedule_patch(str(schedule_id), {"enabled": False})
            except AppError:
                continue

        async with self._session_factory() as session:
            tickets = await TicketRepository(session).list_by_goal(
                str(goal.id), limit=1000
            )
            ticket_ids = [
                str(ticket.id)
                for ticket in tickets
                if not TicketStatus(str(ticket.status)).is_terminal
            ]
        for ticket_id in ticket_ids:
            try:
                await self._ticket_service.cancel(
                    ticket_id, reason="目标已取消"
                )
            except AppError as exc:
                if exc.code == ErrorCode.TICKET_ENDED:
                    continue
                logger.warning("目标 %s 取消工单 %s 失败：%s", goal.id, ticket_id, exc)
                notes.append(f"工单取消失败：{ticket_id}")

        progress = dict(goal.progress or {})
        for note in notes:
            progress = _note(progress, note)
        goal.progress = progress
        goal.status = GoalStatus.CANCELLED.value
        goal.ended_at = self._clock()
        goal.updated_at = self._clock()
        self._remove_job(str(goal.id))

    # ------------------------------------------------------------------
    # 恢复确认（update_goal 用）
    # ------------------------------------------------------------------
    async def resume_consumption_reasons(
        self, goal_id: str, *, context: ToolContext
    ) -> list[str]:
        """恢复目标时，暂停期间被停用的定时任务里有哪些消耗项（spec §4.5.5）。"""
        async with self._session_factory() as session:
            goal = await GoalRepository(session).get(goal_id)
        if goal is None or str(goal.status) != GoalStatus.PAUSED.value:
            return []
        progress = dict(goal.progress or {})
        reasons: list[str] = []
        for schedule_id in progress.get("paused_schedule_ids") or []:
            try:
                view = await self._schedule_get(str(schedule_id))
            except AppError:
                continue
            template = list(getattr(view, "template", None) or [])
            fields = await _consumption_reasons(
                {"template": template},
                tool_name="update_schedule",
                context=context,
            )
            name = getattr(view, "name", None) or str(schedule_id)
            reasons.extend(f"{name}：{field}" for field in fields)
        return reasons

    # ------------------------------------------------------------------
    # 汇报（G3 装配监听器；本任务只实现入口）
    # ------------------------------------------------------------------
    async def on_ticket_report(self, ticket: Any, report: Any) -> None:
        """规划工单/复盘工单的汇报落到目标上（spec §4.5.2、§4.5.3）。"""
        goal_id = getattr(ticket, "goal_id", None)
        if not goal_id:
            return
        async with self._session_factory() as session:
            goal = await GoalRepository(session).get(str(goal_id))
        if goal is None or str(goal.status) in _ENDED_STATUSES:
            return

        status = _status_text(getattr(report, "status", ""))
        details = getattr(report, "details", None) or {}
        if str(goal.planning_ticket_id or "") == str(ticket.id):
            if status == ReportStatus.READY.value:
                await self._apply_plan(goal, ticket, details)
            elif status in (
                ReportStatus.FAILED.value,
                ReportStatus.DROPPED.value,
            ):
                await self._mark_planning(str(goal.id), status)
            return
        if status == ReportStatus.READY.value and isinstance(details, Mapping):
            if "goal_review" in details:
                await self._apply_review(goal, details["goal_review"])

    async def _apply_plan(
        self, goal: Goal, ticket: Any, details: Mapping[str, Any]
    ) -> None:
        """规划结果：整组替换里程碑、关联养成计划与定时任务（spec §4.5.2）。"""
        try:
            if not isinstance(details, Mapping):
                raise ValueError("details 不是对象")
            plan = GoalPlanDetails.model_validate(details.get("goal_plan"))
        except (ValidationError, ValueError, AttributeError) as exc:
            reason = (
                "规划结果缺里程碑"
                if not isinstance(details, Mapping) or "goal_plan" not in details
                else str(exc)
            )
            async with self._session_factory() as session:
                goals = GoalRepository(session)
                row = await goals.get(str(goal.id))
                if row is None:
                    return
                progress = dict(row.progress or {})
                progress["planning"] = "invalid_details"
                progress = _note(progress, f"规划结果不合法：{reason}")
                row.progress = progress
                row.updated_at = self._clock()
                await goals.update(row)
                await session.commit()
            return

        async with self._session_factory() as session:
            goals = GoalRepository(session)
            row = await goals.get(str(goal.id))
            if row is None or str(row.status) in _ENDED_STATUSES:
                return

            milestone_rows = [
                GoalMilestone(
                    goal_id=str(row.id),
                    ord=index,
                    title=draft.title,
                    due_at=_naive_utc(draft.due_at) if draft.due_at else None,
                    status=(
                        MilestoneStatus.ACTIVE.value
                        if index == 1
                        else MilestoneStatus.PENDING.value
                    ),
                    metrics=dict(draft.metrics or {}),
                )
                for index, draft in enumerate(plan.milestones, start=1)
            ]
            await GoalMilestoneRepository(session).replace_all(
                str(row.id), milestone_rows
            )

            linked = await self._find_active_plan(session, ticket)
            row.upgrade_plan_id = str(linked.id) if linked is not None else None
            scheduled: list[str] = []
            raw_ids = list(plan.schedule_ids)
            if linked is not None and linked.schedule_id:
                raw_ids.append(str(linked.schedule_id))
            valid, notes = await self._valid_schedules(raw_ids)
            scheduled.extend(valid)
            row.schedule_ids = scheduled
            progress = dict(row.progress or {})
            progress["planning"] = "done"
            progress["summary"] = plan.summary
            for note in notes:
                progress = _note(progress, note)
            row.progress = progress
            row.updated_at = self._clock()
            await goals.update(row)
            await session.commit()

    async def _apply_review(self, goal: Goal, details: Any) -> None:
        """复盘结果：按 ``ord`` 更新里程碑，必要时完成目标（spec §4.5.3）。"""
        try:
            review = GoalReviewDetails.model_validate(details)
        except ValidationError as exc:
            async with self._session_factory() as session:
                goals = GoalRepository(session)
                row = await goals.get(str(goal.id))
                if row is None:
                    return
                progress = dict(row.progress or {})
                progress = _note(progress, f"复盘结果不合法：{exc}")
                row.progress = progress
                row.updated_at = self._clock()
                await goals.update(row)
                await session.commit()
            return

        async with self._session_factory() as session:
            goals = GoalRepository(session)
            row = await goals.get(str(goal.id))
            if row is None or str(row.status) in _ENDED_STATUSES:
                return
            milestones = GoalMilestoneRepository(session)
            for update in review.milestones:
                await milestones.set_status(str(row.id), update.ord, update.status)
            row.updated_at = self._clock()
            await goals.update(row)
            await session.commit()
            rows = await milestones.list_by_goal(str(row.id))
            statuses = [str(milestone.status) for milestone in rows]

        if review.goal_status == "done" and all(
            status in _INACTIVE_MILESTONE_STATUSES for status in statuses
        ):
            await self._complete(str(goal.id), via="review")

    # ------------------------------------------------------------------
    # 复盘 job 与复盘
    # ------------------------------------------------------------------
    def start_jobs(self, scheduler: Any) -> None:
        """给每个 active/paused 目标注册复盘 job（spec §4.5.4）。"""
        if self._closed:
            return
        self._scheduler = scheduler
        try:
            self._jobs_ready = asyncio.create_task(
                self._load_review_jobs(), name="goal-review-jobs"
            )
        except RuntimeError:  # pragma: no cover - 装配期总在事件循环里
            logger.warning("目标服务没有事件循环，跳过首次复盘 job 注册")
            self._jobs_ready = None

    async def _load_review_jobs(self) -> None:
        try:
            async with self._session_factory() as session:
                goals = await GoalRepository(session).list_by_status(
                    list(_ACTIVE_GOAL_STATUSES)
                )
            for goal in goals:
                self._register_review_job(goal)
        except Exception:  # noqa: BLE001 - 启动期读库失败只记日志
            logger.exception("首次注册目标复盘 job 失败")

    def _job_id(self, goal_id: str) -> str:
        return f"{GOAL_REVIEW_JOB_PREFIX}{goal_id}"

    def _register_review_job(self, goal: Any) -> None:
        """按目标当前 cron 注册（或替换）复盘 job。"""
        if self._closed or self._scheduler is None:
            return
        if str(goal.status) not in _ACTIVE_GOAL_STATUSES:
            return
        try:
            trigger = cron_trigger(str(goal.review_cron), RULE_CRON_TIMEZONE)
        except (ValueError, TypeError):
            logger.warning(
                "目标 %s 的复盘 cron 不合法，跳过注册：%s",
                goal.id,
                goal.review_cron,
            )
            return
        job_id = self._job_id(str(goal.id))
        try:
            self._scheduler.add_job(
                self.review,
                trigger=trigger,
                args=[str(goal.id)],
                id=job_id,
                name=f"目标复盘：{goal.title}"[:64],
                misfire_grace_time=GOAL_REVIEW_MISFIRE_GRACE_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
        except Exception:  # noqa: BLE001 - 调度器已关闭时不能拖垮建目标
            logger.warning("注册目标 %s 的复盘 job 失败", goal.id, exc_info=True)
            return
        self._owned_job_ids.add(job_id)
        if str(goal.status) == GoalStatus.PAUSED.value:
            self._pause_job(str(goal.id))

    def _remove_job(self, goal_id: str) -> None:
        job_id = self._job_id(goal_id)
        if job_id not in self._owned_job_ids:
            return
        try:
            if self._scheduler is not None:
                self._scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001 - job 可能已被调度器清理
            logger.debug("移除目标复盘 job 失败：%s", job_id)
        self._owned_job_ids.discard(job_id)

    def _pause_job(self, goal_id: str) -> None:
        if self._scheduler is None:
            return
        try:
            self._scheduler.pause_job(self._job_id(goal_id))
        except Exception:  # noqa: BLE001 - job 可能还没注册
            logger.debug("暂停目标复盘 job 失败：%s", goal_id)

    def _resume_job(self, goal_id: str) -> None:
        if self._scheduler is None:
            return
        try:
            self._scheduler.resume_job(self._job_id(goal_id))
        except Exception:  # noqa: BLE001 - job 可能已被移除
            logger.debug("恢复目标复盘 job 失败：%s", goal_id)

    def _next_review_at(self, goal_id: str) -> datetime | None:
        """取 ``scheduler.get_job(id).next_run_time``（spec §4.5.4）。"""
        if self._scheduler is None:
            return None
        try:
            job = self._scheduler.get_job(self._job_id(goal_id))
        except Exception:  # noqa: BLE001 - JobLookupError 等
            return None
        return getattr(job, "next_run_time", None)

    async def review(self, goal_id: str) -> None:
        """复盘 job 的回调：快照、判完成、发事件、记上次复盘（spec §4.5.4）。"""
        settings = await self._settings()
        goals_settings = getattr(settings, "goals", None)
        if goals_settings is None or not bool(
            getattr(goals_settings, "enabled", True)
        ):
            return

        now = self._clock()
        async with self._session_factory() as session:
            goals = GoalRepository(session)
            goal = await goals.get(goal_id)
            if goal is None or str(goal.status) != GoalStatus.ACTIVE.value:
                return
            snapshot = await self._snapshot(session, goal, now=now)
            if await self._is_complete(session, goal, snapshot):
                await session.commit()
                await self._complete(goal_id, via="upgrade_plan")
                return

            progress = dict(goal.progress or {})
            notify_deadline = bool(
                goal.deadline is not None
                and now > goal.deadline
                and not progress.get("deadline_notified")
            )
            if notify_deadline:
                progress["deadline_notified"] = True
            progress["last_snapshot"] = snapshot
            goal.progress = progress
            goal.last_review_at = now
            goal.updated_at = now
            await goals.update(goal)
            await session.commit()

        title = goal.title
        deadline = goal.deadline
        if notify_deadline:
            await self._publish(
                _GOAL_DEADLINE_KIND,
                goal_id,
                {
                    "goal_id": goal_id,
                    "title": title,
                    "deadline": _iso_z(deadline),
                    "snapshot": snapshot,
                },
                dedupe_key=f"goal:{goal_id}:deadline",
            )
        scheduled_for = self._scheduled_for(now)
        await self._publish(
            _GOAL_REVIEW_KIND,
            goal_id,
            {"goal_id": goal_id, "title": title, "snapshot": snapshot},
            dedupe_key=f"goal:{goal_id}:review:{scheduled_for}",
        )

    def _scheduled_for(self, now: datetime) -> str:
        """当前北京时间截到分钟的 ISO 字符串（与 M81 定时规则同口径）。"""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(ZoneInfo(RULE_CRON_TIMEZONE)).replace(
            second=0, microsecond=0
        ).isoformat()

    async def _publish(
        self,
        kind: str,
        goal_id: str,
        payload: Mapping[str, Any],
        *,
        dedupe_key: str,
    ) -> None:
        try:
            await self._trigger_service.publish(
                source=EventSource.GOAL,
                kind=kind,
                thread_key=f"goal:{goal_id}",
                payload=dict(payload),
                dedupe_key=dedupe_key,
            )
        except Exception:  # noqa: BLE001 - 事件发不出去不能拖垮复盘
            logger.exception("目标 %s 发布 %s 失败", goal_id, kind)

    async def _snapshot(self, session: Any, goal: Goal, *, now: datetime) -> dict[str, Any]:
        """复盘快照（08 §5 的字段）；顺手把逾期的里程碑标为 missed。"""
        milestones = await GoalMilestoneRepository(session).list_by_goal(
            str(goal.id)
        )
        newly_missed: list[dict[str, Any]] = []
        for milestone in milestones:
            if (
                str(milestone.status) in _OVERDUE_MILESTONE_STATUSES
                and milestone.due_at is not None
                and milestone.due_at < now
            ):
                await GoalMilestoneRepository(session).set_status(
                    str(goal.id), int(milestone.ord), MilestoneStatus.MISSED.value
                )
                milestone.status = MilestoneStatus.MISSED.value
                newly_missed.append(
                    {
                        "ord": int(milestone.ord),
                        "title": str(milestone.title),
                        "due_at": _iso_z(milestone.due_at),
                    }
                )

        schedules = await self._schedule_snapshot(goal.schedule_ids or [])
        async with self._session_factory() as ticket_session:
            tickets = await TicketRepository(ticket_session).list_by_goal(
                str(goal.id), limit=100
            )
        active_tickets = [
            {"id": str(ticket.id), "status": str(ticket.status)}
            for ticket in tickets
            if not TicketStatus(str(ticket.status)).is_terminal
        ]

        deadline = goal.deadline
        if deadline is None:
            days_left = None
        else:
            days_left = int((deadline - now).total_seconds() // 86400)

        return {
            "now": _iso_z(now),
            "deadline": _iso_z(deadline),
            "days_left": days_left,
            "milestones": [
                {
                    "ord": int(item.ord),
                    "title": str(item.title),
                    "status": str(item.status),
                    "due_at": _iso_z(item.due_at),
                }
                for item in milestones
            ],
            "newly_missed": newly_missed,
            "plan": await self._plan_snapshot(goal),
            "schedules": schedules,
            "tickets": active_tickets,
            "last_review_at": _iso_z(goal.last_review_at),
        }

    async def _plan_snapshot(self, goal: Goal) -> dict[str, Any] | None:
        plan_id = goal.upgrade_plan_id
        if not plan_id:
            return None
        async with self._session_factory() as session:
            plan = await session.get(UpgradePlan, str(plan_id))
        if plan is None:
            return None
        progress = dict(plan.progress or {})
        stages = [
            row
            for row in (progress.get("stages") or [])
            if isinstance(row, Mapping) and int(row.get("runs") or 0) > 0
        ]
        return {
            "id": str(plan.id),
            "status": str(plan.status),
            "remaining_stages": len(stages),
            "remaining_runs": sum(int(row.get("runs") or 0) for row in stages),
        }

    async def _schedule_snapshot(
        self, schedule_ids: Iterable[str]
    ) -> dict[str, list[str]]:
        ok: list[str] = []
        disabled: list[str] = []
        missing: list[str] = []
        for schedule_id in schedule_ids:
            try:
                view = await self._schedule_get(str(schedule_id))
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    missing.append(str(schedule_id))
                    continue
                missing.append(str(schedule_id))
                continue
            except Exception:  # noqa: BLE001 - 读任务失败按缺失处理
                missing.append(str(schedule_id))
                continue
            if bool(getattr(view, "enabled", False)):
                ok.append(str(schedule_id))
            else:
                disabled.append(str(schedule_id))
        return {"ok": ok, "disabled": disabled, "missing": missing}

    async def _is_complete(
        self, session: Any, goal: Goal, snapshot: Mapping[str, Any]
    ) -> bool:
        """养成计划已完成且里程碑全部 done/skipped（spec §4.5.4）。"""
        if not goal.upgrade_plan_id:
            return False
        plan = await session.get(UpgradePlan, str(goal.upgrade_plan_id))
        if plan is None or str(plan.status) != "completed":
            return False
        return all(
            str(item.get("status")) in _INACTIVE_MILESTONE_STATUSES
            for item in snapshot.get("milestones") or []
        )

    async def _complete(self, goal_id: str, *, via: str) -> None:
        """完成目标：停用定时任务、移除 job、发 ``goal.completed``。"""
        async with self._session_factory() as session:
            goal = await GoalRepository(session).get(goal_id)
            if goal is None or str(goal.status) in _ENDED_STATUSES:
                return
            schedule_ids = [str(item) for item in (goal.schedule_ids or [])]
            title = str(goal.title)

        notes = await self._disable_schedules(schedule_ids)
        now = self._clock()
        async with self._session_factory() as session:
            goals = GoalRepository(session)
            goal = await goals.get(goal_id)
            if goal is None or str(goal.status) in _ENDED_STATUSES:
                return
            progress = dict(goal.progress or {})
            for note in notes:
                progress = _note(progress, note)
            goal.progress = progress
            goal.status = GoalStatus.DONE.value
            goal.ended_at = now
            goal.updated_at = now
            await goals.update(goal)
            await session.commit()

        self._remove_job(goal_id)
        await self._publish(
            _GOAL_COMPLETED_KIND,
            goal_id,
            {"goal_id": goal_id, "title": title, "via": via},
            dedupe_key=f"goal:{goal_id}:completed",
        )

    # ------------------------------------------------------------------
    # 外部服务薄封装
    # ------------------------------------------------------------------
    def _schedule_service(self) -> Any:
        return self._get_schedule_service()

    async def _schedule_get(self, schedule_id: str) -> Any:
        service = self._schedule_service()
        if service is None:
            raise AppError(
                ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在", {"schedule_id": schedule_id}
            )
        return await service.get(schedule_id)

    async def _schedule_patch(
        self, schedule_id: str, changes: Mapping[str, Any]
    ) -> Any:
        service = self._schedule_service()
        if service is None:
            raise AppError(
                ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在", {"schedule_id": schedule_id}
            )
        return await service.patch(schedule_id, changes)

    async def _disable_schedules(self, schedule_ids: Iterable[str]) -> list[str]:
        notes: list[str] = []
        for schedule_id in schedule_ids:
            try:
                view = await self._schedule_get(str(schedule_id))
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {schedule_id} 已不存在")
                else:
                    notes.append(f"定时任务 {schedule_id} 读取失败：{exc.message}")
                continue
            except Exception as exc:  # noqa: BLE001 - 停用失败不能拖垮完成
                logger.warning("读取定时任务 %s 失败：%s", schedule_id, exc)
                notes.append(f"定时任务 {schedule_id} 读取失败")
                continue
            if not bool(getattr(view, "enabled", False)):
                continue
            try:
                await self._schedule_patch(str(schedule_id), {"enabled": False})
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {schedule_id} 已不存在")
                else:
                    notes.append(f"定时任务 {schedule_id} 停用失败：{exc.message}")
            except Exception as exc:  # noqa: BLE001 - 停用失败不能拖垮完成
                logger.warning("停用定时任务 %s 失败：%s", schedule_id, exc)
                notes.append(f"定时任务 {schedule_id} 停用失败")
        return notes

    async def _valid_schedules(
        self, schedule_ids: Iterable[str]
    ) -> tuple[list[str], list[str]]:
        """校验定时任务 id：保留存在的、去重并保序；缺失的记一条说明。"""
        valid: list[str] = []
        notes: list[str] = []
        seen: set[str] = set()
        for schedule_id in schedule_ids:
            if not schedule_id:
                continue
            sid = str(schedule_id)
            if sid in seen:
                continue
            seen.add(sid)
            service = self._schedule_service()
            if service is None:
                valid.append(sid)
                continue
            try:
                await service.get(sid)
            except AppError as exc:
                if exc.code == ErrorCode.SCHEDULE_NOT_FOUND:
                    notes.append(f"定时任务 {sid} 已不存在，已从目标移除")
                    continue
                raise
            valid.append(sid)
        return valid, notes

    async def _find_active_plan(self, session: Any, ticket: Any) -> Any:
        """按 Worker 会话找这张规划工单建出的 active 养成计划（spec §4.5.2）。"""
        session_ids: list[str] = []
        worker_session_id = getattr(ticket, "worker_session_id", None)
        if worker_session_id:
            session_ids.append(str(worker_session_id))
        result = await session.execute(
            select(AgentSession.id).where(
                AgentSession.ticket_id == str(ticket.id),
                AgentSession.kind == AgentSessionKind.WORKER.value,
            )
        )
        for value in result.scalars().all():
            if str(value) not in session_ids:
                session_ids.append(str(value))
        if not session_ids:
            return None
        result = await session.execute(
            select(UpgradePlan)
            .where(
                UpgradePlan.session_id.in_(session_ids),
                UpgradePlan.status == "active",
            )
            .order_by(UpgradePlan.created_at.desc(), UpgradePlan.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _plan_status(self, plan_id: str) -> str | None:
        async with self._session_factory() as session:
            plan = await session.get(UpgradePlan, plan_id)
        return str(plan.status) if plan is not None else None

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """移除自己注册的复盘 job（spec §4.2）。"""
        self._closed = True
        if self._jobs_ready is not None:
            try:
                await self._jobs_ready
            except Exception:  # noqa: BLE001
                logger.debug("等待目标复盘 job 装载失败", exc_info=True)
            self._jobs_ready = None
        for job_id in tuple(self._owned_job_ids):
            try:
                if self._scheduler is not None:
                    self._scheduler.remove_job(job_id)
            except Exception:  # noqa: BLE001
                logger.debug("移除目标复盘 job 失败：%s", job_id)
        self._owned_job_ids.clear()
