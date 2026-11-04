"""计划服务：一份未结束计划的创建、更新与状态机（M75 规格 §4.5、v3 06 §7.1）。

一个会话同时最多一份未结束计划（``draft`` / ``awaiting_approval`` / ``active``），
由 ``run_plan`` 的部分唯一索引兜底。``update_plan`` 每次提交完整列表，服务负责：

1. **归一化步骤**：``id`` 是 8 位十六进制、同计划唯一；调用方没带 id 时按标题
   复用上一版的 id，模型不回传 id 也能只改状态。标题都不匹配的步骤算新增。
2. **判定是否重新批准**：只有新增步骤或改了某步标题才重新判定；只改状态、备注
   或把步骤标成 ``cancelled`` 时保持 ``active``。是否需要批准由
   ``plan.approval_mode`` 与 ``plan.complex_min_steps`` 决定。
3. **校验**：同一时刻最多一步 ``in_progress``；违反时抛 :class:`PlanInvalid`，
   不写库。``update_plan`` 工具把它翻成 ``error.code = plan_invalid`` 的工具结果。

``plan.approval_mode`` / ``plan.complex_min_steps`` 的设置项由 M75 T4/G6 接入
``Settings``；在它落地之前读不到时按 06 §7.1 的默认值（``complex`` / 4）处理，
与 ``SpillMiddleware`` 读 ``context.spill_threshold_chars`` 的口径一致。
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import RunPlan, utcnow
from maa_api.domain.enums import PlanItemStatus, PlanStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "ACTIVE_PLAN_STATUSES",
    "APPROVAL_MODES",
    "DEFAULT_APPROVAL_MODE",
    "DEFAULT_COMPLEX_MIN_STEPS",
    "PLAN_INVALID",
    "PlanInvalid",
    "PlanService",
    "plan_human_action",
    "render_plan_text",
    "resolve_plan_policy",
]

logger = logging.getLogger(__name__)

#: 未结束计划的状态；同一会话最多一份（部分唯一索引的同一条口径）。
ACTIVE_PLAN_STATUSES: frozenset[str] = frozenset(
    {
        PlanStatus.DRAFT.value,
        PlanStatus.AWAITING_APPROVAL.value,
        PlanStatus.ACTIVE.value,
    }
)
#: 终态；不再出现在计划端点与尾部。
TERMINAL_PLAN_STATUSES: frozenset[str] = frozenset(
    {PlanStatus.DONE.value, PlanStatus.ABANDONED.value}
)
#: 步骤状态的中文名（06 §7.2 的示例逐字）。
ITEM_STATUS_LABELS: Mapping[str, str] = {
    PlanItemStatus.PENDING.value: "待办",
    PlanItemStatus.IN_PROGRESS.value: "进行中",
    PlanItemStatus.DONE.value: "完成",
    PlanItemStatus.CANCELLED.value: "已取消",
}
#: 计划状态的中文名。
PLAN_STATUS_LABELS: Mapping[str, str] = {
    PlanStatus.DRAFT.value: "草稿",
    PlanStatus.AWAITING_APPROVAL.value: "等待批准",
    PlanStatus.ACTIVE.value: "进行中",
    PlanStatus.DONE.value: "已完成",
    PlanStatus.ABANDONED.value: "已放弃",
}
#: 合法的批准模式（04 §6）。
APPROVAL_MODES: frozenset[str] = frozenset({"always", "complex", "never"})
DEFAULT_APPROVAL_MODE = "complex"
DEFAULT_COMPLEX_MIN_STEPS = 4
#: 复杂计划的步骤数范围（04 §6）。
MIN_COMPLEX_MIN_STEPS = 2
MAX_COMPLEX_MIN_STEPS = 20
#: HTTP 批准端点写进 ``approved_by`` 的身份标签。
DEFAULT_APPROVED_BY = "user"
#: ``plan_invalid`` 是工具结果里的 ``error.code``，不进 ``ErrorCode``（spec §9）。
PLAN_INVALID = "plan_invalid"

_ITEM_ID_RE = re.compile(r"^[0-9a-f]{8}$")


class PlanInvalid(ValueError):
    """提交的步骤列表不合法（同一时刻多于一步 ``in_progress`` 等）。"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


def resolve_plan_policy(
    provider: Callable[[], Any] | None = None,
) -> tuple[str, int]:
    """读 ``plan.approval_mode`` 与 ``plan.complex_min_steps``；读不到时用默认值。"""
    try:
        settings = provider() if provider is not None else get_settings()
        plan = getattr(settings, "plan", None)
        mode = getattr(plan, "approval_mode", DEFAULT_APPROVAL_MODE)
        steps = getattr(plan, "complex_min_steps", DEFAULT_COMPLEX_MIN_STEPS)
    except Exception:  # noqa: BLE001 - 读设置失败按默认策略，不该挡住计划
        logger.warning("读取计划批准设置失败，使用默认值", exc_info=True)
        return DEFAULT_APPROVAL_MODE, DEFAULT_COMPLEX_MIN_STEPS
    if mode not in APPROVAL_MODES:
        logger.warning("未知的计划批准模式 %r，按 complex 处理", mode)
        mode = DEFAULT_APPROVAL_MODE
    if not isinstance(steps, int) or isinstance(steps, bool):
        steps = DEFAULT_COMPLEX_MIN_STEPS
    steps = max(MIN_COMPLEX_MIN_STEPS, min(MAX_COMPLEX_MIN_STEPS, steps))
    return str(mode), steps


def plan_human_action(plan: RunPlan) -> dict[str, Any]:
    """待批准计划的 ``human_action``（spec §4.5、06 §7.1 逐字）。"""
    return {
        "type": "decision",
        "ref_id": str(plan.id),
        "prompt": f"批准计划「{plan.title}」？",
    }


def render_plan_text(plan: RunPlan) -> str:
    """动态尾部 ``plan`` 段的文本（06 §7.2 的示例）。"""
    status = PLAN_STATUS_LABELS.get(str(plan.status), str(plan.status))
    lines = [f"当前计划「{plan.title}」（{status}）："]
    for index, item in enumerate(plan.items or (), start=1):
        if not isinstance(item, Mapping):
            continue
        label = ITEM_STATUS_LABELS.get(str(item.get("status")), str(item.get("status")))
        lines.append(f"{index}. [{label}] {item.get('title', '')}")
    return "\n".join(lines)


def _one_in_progress(items: Sequence[Mapping[str, Any]]) -> bool:
    return (
        sum(1 for item in items if item.get("status") == PlanItemStatus.IN_PROGRESS.value)
        <= 1
    )


def _all_terminal(items: Sequence[Mapping[str, Any]]) -> bool:
    return bool(items) and all(
        item.get("status") in (PlanItemStatus.DONE.value, PlanItemStatus.CANCELLED.value)
        for item in items
    )


def _new_item_id(used: set[str]) -> str:
    """生成 8 位十六进制、同计划唯一的步骤 id。"""
    while True:
        value = secrets.token_hex(4)
        if value not in used:
            used.add(value)
            return value


def _normalize_status(value: Any, *, title: str) -> str:
    try:
        return PlanItemStatus(str(value)).value
    except ValueError as exc:
        raise PlanInvalid(
            f"步骤「{title}」的状态不合法：{value}",
            {"title": title, "status": str(value)},
        ) from exc


def _normalize_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """校验并收窄调用方提交的步骤；标题、状态、备注都在这里定型。"""
    if not items:
        raise PlanInvalid("计划至少要有一个步骤")
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            raise PlanInvalid("计划步骤必须是对象", {"item": str(raw)})
        title = str(raw.get("title") or "").strip()
        if not title:
            raise PlanInvalid("计划步骤的标题不能为空")
        note = raw.get("note")
        note = str(note).strip() if note is not None else None
        normalized.append(
            {
                "id": raw.get("id"),
                "title": title,
                "status": _normalize_status(raw.get("status", "pending"), title=title),
                "note": note or None,
            }
        )
    if not _one_in_progress(normalized):
        raise PlanInvalid("同一时刻最多只能有一个步骤处于 in_progress")
    return normalized


async def _active_row(db: AsyncSession, session_id: str) -> RunPlan | None:
    """该会话当前未结束的计划；部分唯一索引保证最多一条。"""
    result = await db.execute(
        select(RunPlan)
        .where(
            RunPlan.session_id == session_id,
            RunPlan.status.in_(list(ACTIVE_PLAN_STATUSES)),
        )
        .order_by(RunPlan.created_at.desc(), RunPlan.id.desc())
        .limit(1)
    )
    return result.scalars().first()


class PlanService:
    """``run_plan`` 的读写与状态机；不持有全局状态，会话工厂由装配方注入。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_provider: Callable[[], Any] | None = None,
        approval_mode: str | None = None,
        complex_min_steps: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._approval_mode = approval_mode
        self._complex_min_steps = complex_min_steps

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def active_for_session(self, session_id: str) -> RunPlan | None:
        """该会话当前未结束的计划；没有时返回 ``None``。"""
        async with self._session_factory() as db:
            return await _active_row(db, session_id)

    async def get(self, plan_id: str) -> RunPlan | None:
        async with self._session_factory() as db:
            return await db.get(RunPlan, plan_id)

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def needs_approval(self, item_count: int) -> bool:
        """「需要批准」规则（spec §4.5）。"""
        mode, min_steps = self._policy()
        if mode == "always":
            return True
        if mode == "never":
            return False
        return item_count >= min_steps

    async def create_or_update(
        self,
        *,
        session_id: str,
        ticket_id: str | None,
        title: str,
        items: Sequence[Mapping[str, Any]],
    ) -> RunPlan:
        """按 spec §4.5 的表写入一版完整计划；``items`` 不合法时抛 ``PlanInvalid``。"""
        normalized = _normalize_items(items)
        async with self._session_factory() as db:
            plan = await _active_row(db, session_id)
            previous = list(plan.items) if plan is not None else []
            result_items, new_step, title_changed = _merge_items(previous, normalized)
            if not _one_in_progress(result_items):  # 防御：_normalize_items 已校验
                raise PlanInvalid("同一时刻最多只能有一个步骤处于 in_progress")
            need = self.needs_approval(len(result_items))
            status = _next_status(
                None if plan is None else str(plan.status),
                all_terminal=_all_terminal(result_items),
                need_approval=need,
                new_step=new_step,
                title_changed=title_changed,
            )
            if status == PlanStatus.ACTIVE.value and all(
                item["status"] == PlanItemStatus.PENDING.value for item in result_items
            ):
                result_items[0]["status"] = PlanItemStatus.IN_PROGRESS.value
            now = utcnow()
            if plan is None:
                plan = RunPlan(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    title=title,
                    items=result_items,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                db.add(plan)
            else:
                plan.title = title
                if ticket_id is not None:
                    plan.ticket_id = ticket_id
                plan.items = result_items
                plan.status = status
                plan.updated_at = now
                if status == PlanStatus.AWAITING_APPROVAL.value:
                    # 重新待批准：上一版的批准不再算数。
                    plan.approved_by = None
                    plan.approved_at = None
            await db.commit()
            return plan

    async def approve(
        self, plan_id: str, *, approved_by: str = DEFAULT_APPROVED_BY
    ) -> RunPlan:
        """待批准 → ``active``；不在待批准时 409 ``PLAN_NOT_AWAITING_APPROVAL``。"""
        async with self._session_factory() as db:
            plan = await self._require(db, plan_id)
            self._require_awaiting(plan)
            now = utcnow()
            plan.status = PlanStatus.ACTIVE.value
            plan.approved_by = approved_by or DEFAULT_APPROVED_BY
            plan.approved_at = now
            plan.updated_at = now
            await db.commit()
            return plan

    async def reject(self, plan_id: str, *, reason: str | None = None) -> RunPlan:
        """待批准 → ``draft``；原因只随收件箱消息给 Worker，不落 ``run_plan``。"""
        async with self._session_factory() as db:
            plan = await self._require(db, plan_id)
            self._require_awaiting(plan)
            plan.status = PlanStatus.DRAFT.value
            plan.approved_by = None
            plan.approved_at = None
            plan.updated_at = utcnow()
            if reason:
                logger.info("计划被退回：plan=%s reason=%s", plan_id, reason)
            await db.commit()
            return plan

    async def abandon(self, plan_id: str) -> RunPlan:
        """未结束计划 → ``abandoned``；已是终态时原样返回。"""
        async with self._session_factory() as db:
            plan = await self._require(db, plan_id)
            if str(plan.status) not in TERMINAL_PLAN_STATUSES:
                plan.status = PlanStatus.ABANDONED.value
                plan.updated_at = utcnow()
                await db.commit()
            return plan

    async def mark_done(self, plan_id: str) -> RunPlan:
        """未结束计划 → ``done``（全部步骤完成/取消的另一条入口）。"""
        async with self._session_factory() as db:
            plan = await self._require(db, plan_id)
            if str(plan.status) not in TERMINAL_PLAN_STATUSES:
                plan.status = PlanStatus.DONE.value
                plan.updated_at = utcnow()
                await db.commit()
            return plan

    async def abandon_for_ticket(self, ticket_id: str) -> int:
        """工单结束时把它名下所有未结束计划改成 ``abandoned``，返回条数。"""
        async with self._session_factory() as db:
            result = await db.execute(
                select(RunPlan).where(
                    RunPlan.ticket_id == ticket_id,
                    RunPlan.status.in_(list(ACTIVE_PLAN_STATUSES)),
                )
            )
            rows = list(result.scalars().all())
            now = utcnow()
            for plan in rows:
                plan.status = PlanStatus.ABANDONED.value
                plan.updated_at = now
            if rows:
                await db.commit()
            return len(rows)

    async def on_ticket_ended(self, ended: Any) -> int:
        """``TicketService`` 的结束监听者：到终态就放弃该工单的未结束计划。"""
        ticket_id = getattr(ended, "ticket_id", None)
        if not ticket_id:
            return 0
        return await self.abandon_for_ticket(str(ticket_id))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _policy(self) -> tuple[str, int]:
        if self._approval_mode is None or self._complex_min_steps is None:
            mode, steps = resolve_plan_policy(self._settings_provider)
            mode = self._approval_mode if self._approval_mode is not None else mode
            steps = (
                self._complex_min_steps
                if self._complex_min_steps is not None
                else steps
            )
            return str(mode), int(steps)
        return str(self._approval_mode), int(self._complex_min_steps)

    @staticmethod
    async def _require(db: AsyncSession, plan_id: str) -> RunPlan:
        plan = await db.get(RunPlan, plan_id)
        if plan is None:
            raise AppError(ErrorCode.PLAN_NOT_FOUND, "没有这个计划")
        return plan

    @staticmethod
    def _require_awaiting(plan: RunPlan) -> None:
        if str(plan.status) != PlanStatus.AWAITING_APPROVAL.value:
            raise AppError(
                ErrorCode.PLAN_NOT_AWAITING_APPROVAL,
                "计划不在待批准状态",
                {"plan_id": str(plan.id), "status": str(plan.status)},
            )


def _merge_items(
    previous: Sequence[Mapping[str, Any]],
    submitted: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """把提交的步骤并到上一版：返回（新列表、是否新增、是否改了标题）。

    匹配优先用调用方给的 8 位 id，其次是标题；两者都对不上的步骤算新增。
    """
    by_id = {
        str(item.get("id")): item
        for item in previous
        if isinstance(item, Mapping) and _ITEM_ID_RE.match(str(item.get("id") or ""))
    }
    by_title: dict[str, list[Mapping[str, Any]]] = {}
    for item in previous:
        if isinstance(item, Mapping):
            by_title.setdefault(str(item.get("title", "")), []).append(item)
    used = set(by_id)
    matched: set[str] = set()
    result: list[dict[str, Any]] = []
    new_step = False
    title_changed = False
    for source in submitted:
        candidate = source.get("id")
        old = None
        if isinstance(candidate, str) and candidate in by_id and candidate not in matched:
            old = by_id[candidate]
            matched.add(candidate)
        else:
            for item in by_title.get(str(source["title"]), ()):
                item_id = str(item.get("id"))
                if item_id not in matched:
                    old = item
                    matched.add(item_id)
                    break
        if old is None:
            new_step = True
            item_id = _new_item_id(used)
        else:
            item_id = str(old.get("id"))
            if str(old.get("title", "")) != source["title"]:
                title_changed = True
        result.append(
            {
                "id": item_id,
                "title": source["title"],
                "status": source["status"],
                "note": source["note"],
            }
        )
    return result, new_step, title_changed


def _next_status(
    existing_status: str | None,
    *,
    all_terminal: bool,
    need_approval: bool,
    new_step: bool,
    title_changed: bool,
) -> str:
    """spec §4.5 的状态表：返回这一版计划的目标状态。"""
    if all_terminal:
        return PlanStatus.DONE.value
    if existing_status is None:
        return (
            PlanStatus.AWAITING_APPROVAL.value
            if need_approval
            else PlanStatus.ACTIVE.value
        )
    if existing_status == PlanStatus.ACTIVE.value:
        if not (new_step or title_changed):
            return PlanStatus.ACTIVE.value
        return (
            PlanStatus.AWAITING_APPROVAL.value
            if need_approval
            else PlanStatus.ACTIVE.value
        )
    # draft / awaiting_approval：按是否需要批准重新判定。
    return (
        PlanStatus.AWAITING_APPROVAL.value if need_approval else PlanStatus.ACTIVE.value
    )
