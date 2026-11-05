"""目标模式的输入、汇报约定与响应模型（M82 规格 §4.2）。

三组形状：

- 用户输入：:class:`GoalTarget` / :class:`GoalCreate` / :class:`GoalUpdate`；
- 规划与复盘工单的汇报 ``details``：:class:`GoalPlanDetails` / :class:`GoalReviewDetails`
  （规格 §4.5.2、§4.5.3 与 08 §5）；
- 对外的响应：:class:`MilestoneOut` / :class:`GoalTicketOut` / :class:`GoalOut` /
  :class:`GoalDetail` / :class:`GoalSummary` / :class:`GoalPage`。

时间字段一律按 UTC 的 ``Z`` 后缀输出：库里存 naive UTC，Pydantic 默认会带
``+00:00`` 或不带时区，这里在序列化时统一补上（与 ``api/schemas/agent_v3.py`` 同口径）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from maa_api.agent.tools.account import UpgradeTarget

__all__ = [
    "GoalCreate",
    "GoalDetail",
    "GoalOut",
    "GoalPage",
    "GoalPlanDetails",
    "GoalReviewDetails",
    "GoalSummary",
    "GoalTarget",
    "GoalTicketOut",
    "GoalUpdate",
    "MilestoneDraft",
    "MilestoneOut",
    "MilestoneUpdate",
]


def _utc_z(value: datetime | None) -> str | None:
    """把库里的 naive UTC 时间渲染成 ``...Z``；``None`` 原样返回。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------
class GoalTarget(BaseModel):
    """结构化目标：用户原话、可选练度目标与养成计划的运行时刻。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    operators: list[UpgradeTarget] = Field(default_factory=list, max_length=10)
    run_times: list[str] = Field(default_factory=list, max_length=4)


class GoalCreate(BaseModel):
    """新建目标的请求体（REST 与 ``create_goal`` 工具共用）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    target: GoalTarget
    #: 必须带时区；入库转成 naive UTC（spec §4.5.1）。
    deadline: datetime | None = None
    #: 空时取 ``goals.default_review_cron``。
    review_cron: str | None = Field(default=None, max_length=64)


class GoalUpdate(BaseModel):
    """改目标：用 ``model_fields_set`` 区分「没传」与「传 null」。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=64)
    #: 传 null 表示去掉截止。
    deadline: datetime | None = None
    status: Literal["active", "paused", "cancelled"] | None = None


class MilestoneDraft(BaseModel):
    """规划结果里的一个里程碑。"""

    title: str = Field(min_length=1, max_length=120)
    due_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class GoalPlanDetails(BaseModel):
    """规划工单汇报的 ``details["goal_plan"]``（05 §4.1 的目标约定）。"""

    milestones: list[MilestoneDraft] = Field(min_length=1, max_length=12)
    schedule_ids: list[str] = Field(default_factory=list, max_length=8)
    summary: str | None = Field(default=None, max_length=600)


class MilestoneUpdate(BaseModel):
    """复盘结果里对单个里程碑的更新。"""

    ord: int = Field(ge=1, le=12)
    status: Literal["pending", "active", "done", "missed", "skipped"]
    note: str | None = Field(default=None, max_length=200)


class GoalReviewDetails(BaseModel):
    """复盘工单汇报的 ``details["goal_review"]``（spec §4.5.3）。"""

    milestones: list[MilestoneUpdate] = Field(default_factory=list, max_length=12)
    summary: str | None = Field(default=None, max_length=600)
    goal_status: Literal["done"] | None = None


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------
class MilestoneOut(BaseModel):
    """一个里程碑的对外形状。"""

    id: str
    ord: int
    title: str
    due_at: datetime | None = None
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("due_at")
    def _serialize_due_at(self, value: datetime | None) -> str | None:
        return _utc_z(value)


class GoalTicketOut(BaseModel):
    """目标详情里的一张相关工单（至多 20 张）。"""

    id: str
    title: str
    kind: str
    status: str
    last_report_status: str | None = None
    updated_at: datetime

    @field_serializer("updated_at")
    def _serialize_updated_at(self, value: datetime) -> str | None:
        return _utc_z(value)


class GoalOut(BaseModel):
    """目标本体：``goal`` 的全部列，另加 ``next_review_at``。"""

    id: str
    title: str
    status: str
    description: str | None = None
    target: GoalTarget
    deadline: datetime | None = None
    planning_ticket_id: str | None = None
    upgrade_plan_id: str | None = None
    schedule_ids: list[str] = Field(default_factory=list)
    review_cron: str
    next_review_at: datetime | None = None
    last_review_at: datetime | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None

    @field_serializer(
        "deadline",
        "next_review_at",
        "last_review_at",
        "created_at",
        "updated_at",
        "ended_at",
    )
    def _serialize_times(self, value: datetime | None) -> str | None:
        return _utc_z(value)


class GoalDetail(GoalOut):
    """目标详情：本体 + 里程碑 + 最近 20 张目标工单。"""

    milestones: list[MilestoneOut] = Field(default_factory=list)
    tickets: list[GoalTicketOut] = Field(default_factory=list)


class GoalSummary(BaseModel):
    """目标列表的一行。"""

    id: str
    title: str
    status: str
    deadline: datetime | None = None
    milestones_total: int = 0
    milestones_done: int = 0
    next_review_at: datetime | None = None
    updated_at: datetime

    @field_serializer("deadline", "next_review_at", "updated_at")
    def _serialize_times(self, value: datetime | None) -> str | None:
        return _utc_z(value)


class GoalPage(BaseModel):
    """目标分页响应。"""

    items: list[GoalSummary]
    total: int
    page: int
    size: int


# ---------------------------------------------------------------------------
# ORM → 响应
# ---------------------------------------------------------------------------
def milestone_out(row: Any) -> MilestoneOut:
    """``goal_milestone`` 行 → :class:`MilestoneOut`。"""
    return MilestoneOut(
        id=str(row.id),
        ord=int(row.ord),
        title=str(row.title),
        due_at=row.due_at,
        status=str(row.status),
        metrics=dict(row.metrics or {}),
    )


def goal_ticket_out(row: Any) -> GoalTicketOut:
    """``ticket`` 行 → :class:`GoalTicketOut`。"""
    return GoalTicketOut(
        id=str(row.id),
        title=str(row.title),
        kind=str(row.kind),
        status=str(row.status),
        last_report_status=row.last_report_status,
        updated_at=row.updated_at,
    )


def goal_out(goal: Any, *, next_review_at: datetime | None = None) -> GoalOut:
    """``goal`` 行 → :class:`GoalOut`。"""
    target = goal.target or {}
    try:
        target_model = GoalTarget.model_validate(target)
    except Exception:  # noqa: BLE001 - 库里若有旧形状，至少还能返回标题
        target_model = GoalTarget(text=str(goal.title))
    return GoalOut(
        id=str(goal.id),
        title=str(goal.title),
        status=str(goal.status),
        description=goal.description,
        target=target_model,
        deadline=goal.deadline,
        planning_ticket_id=goal.planning_ticket_id,
        upgrade_plan_id=goal.upgrade_plan_id,
        schedule_ids=[str(item) for item in (goal.schedule_ids or [])],
        review_cron=str(goal.review_cron),
        next_review_at=next_review_at,
        last_review_at=goal.last_review_at,
        progress=dict(goal.progress or {}),
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        ended_at=goal.ended_at,
    )


def goal_detail(
    goal: Any,
    milestones: list[Any],
    tickets: list[Any],
    *,
    next_review_at: datetime | None = None,
) -> GoalDetail:
    """``goal`` 行 + 里程碑 + 工单 → :class:`GoalDetail`。"""
    base = goal_out(goal, next_review_at=next_review_at)
    return GoalDetail(
        **base.model_dump(),
        milestones=[milestone_out(row) for row in milestones],
        tickets=[goal_ticket_out(row) for row in tickets],
    )


def goal_summary(
    goal: Any, milestones: list[Any], *, next_review_at: datetime | None = None
) -> GoalSummary:
    """``goal`` 行 + 里程碑 → 目标列表的一行。"""
    done = sum(
        1 for row in milestones if str(row.status) == "done"
    )
    return GoalSummary(
        id=str(goal.id),
        title=str(goal.title),
        status=str(goal.status),
        deadline=goal.deadline,
        milestones_total=len(milestones),
        milestones_done=done,
        next_review_at=next_review_at,
        updated_at=goal.updated_at,
    )
