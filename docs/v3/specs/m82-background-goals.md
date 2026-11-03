# M82 后台工单与目标模式（规格）

> 阶段：P3　依赖：M81　分支：`background-goals`　迁移：`0035_goals`
> 本文是 M82 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在新分支上，工作区干净 | `git status -sb` | `## background-goals`，没有改动 |
| 2 | M81 已合并 | `git tag -l 'v3-m81'` | `v3-m81` |
| 3 | 迁移 head 是 M81 的 | `ls maa_api/db/migrations/versions/*.py \| sort \| tail -n 1` | `0034_triggers`（或台账记的实际编号） |
| 4 | M81 的事件服务与登记表 | `rg -n "class TriggerService\|^KNOWN_KINDS\|^BUILTIN_RULES\|^RULE_CRON_TIMEZONE" maa_api/agent/events/` | 4 行 |
| 5 | 建单入口已有 `source_trigger_id` | `rg -n "source_trigger_id" maa_api/agent/tickets/service.py` | 至少 1 行；记下建单入口的方法名与参数 |
| 6 | M70 的汇报落库入口 | `rg -n "    async def " maa_api/agent/tickets/service.py` | 能找到 ReportMiddleware 调用的那个方法（记下名字，下文记作「汇报落库入口」） |
| 7 | `planner` 定义带养成计划工具 | `rg -n "start_upgrade_plan\|create_schedule" maa_api/agent/specs/builtin/planner.md` | 至少 1 行 |
| 8 | 总控定义 | `rg -n "^tools:\|^version:" maa_api/agent/specs/builtin/orchestrator.md` | 2 行；记下当前 `version` |
| 9 | `upgrade_plan.id` 的类型 | `rg -n -A 12 "class UpgradePlan\(" maa_api/db/models.py \| rg "id: str"` | `id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)`（2026-11-02 已核实） |
| 10 | 养成计划服务的方法 | `rg -n "async def (start\|cancel\|refresh\|list_plans)\(" maa_api/services/account/plans.py` | 4 行 |
| 11 | 定时任务服务的方法 | `rg -n "async def (get\|patch\|delete)\(" maa_api/services/schedule_service.py` | 3 行 |
| 12 | 推送与页面可见性 | `rg -n "async def broadcast" maa_api/services/webpush_service.py && rg -n "def has_visible_client" maa_api/api/ws.py` | 各 1 行 |
| 13 | 工具目录计数 | `rg -n "assert len\(actual\) ==\|model_visible\) ==" tests/agent/test_tool_catalog.py` | 2 行；记下两个数（下文记作 N、V） |
| 14 | 复用的 v2 错误码在 | `rg -n "AGENT_DISABLED = \|SCHEDULE_CRON_INVALID = " maa_api/domain/errors.py` | 2 行 |
| 15 | 测试基线 | `.venv/bin/python -m pytest -q > /tmp/m82-base.log 2>&1; echo $?; tail -n 5 /tmp/m82-base.log` | 记下失败数 |

任何一条不符合，停下来报告。

## 1. 背景与目标

用户的很多诉求跨好几天（「月底前把银灰练到精二专三」），v2 只能一次性建养成计划，之后没人盯。M82 加目标（goal）：拆里程碑、关联 v2 的养成计划与定时任务、每天复盘（[08 §5](../08-主动性与通道.md)）。同时补上「后台工单完成推送」：用户不在页面上时，工单有结果、卡住或需要决定，推一条 Web Push（08 §1.5）。

做完后：

1. 总控的 `create_goal` 工具与 `POST /api/agent/goals` 都能建目标；建目标时同时建一张 `planner` 规划工单（显式委托，`goal_id` 指回目标，订阅 `goal:<id>`）。
2. 规划工单汇报 ready 时，GoalService 写入里程碑，关联它建的养成计划与定时任务。
3. 每个进行中的目标按 `review_cron` 复盘：确定性地把逾期里程碑标为 missed、判断完成与截止，然后发 `goal.review_due`；内置规则 `goal-review`（auto）研判通过才建复盘工单。
4. 暂停、恢复、取消目标会联动定时任务、养成计划、复盘 job 与目标工单。
5. 任何工单的汇报为 ready、blocked、failed，或 waiting 且要用户回答或决定时，没有可见页面就推送；同一工单 10 分钟内至多一次。
6. 前端有目标列表、详情与新建页。

## 2. 非目标

- 不改 v2 养成计划的算法、确认卡片与完成通知；不改 `planner` 的工具名单。
- 不做目标模板、目标之间的依赖、多个目标共享定时任务。
- 推送只走 Web Push；IM 与发件箱由 M85 接手（届时把 `TicketPushNotifier` 改成写发件箱）。
- 每日简报属于 M84。

## 3. 相关文档

- [08 §1.5、§2.2（goal 三种事件）、§3.2（`goal-review`）、§5](../08-主动性与通道.md)。
- [03 §11.4、§13、§15](../03-数据模型.md)；[04 §2.4（M82 四个端点）、§5、§6](../04-接口与事件协议.md)；[05 §3.2（总控工具）、§6.1（goals 组）](../05-运行时与代理定义.md)。
- 委托强度：[03 §6.2](../03-数据模型.md)、[13 V3-D14](../13-决策记录.md)；本 spec 对规划工单的处理见 §4.5.1。
- v2 推送口径：`rg -n "13.1 人工确认的触达设计" docs/v2/09-前端重构方案.md`（[v2/09 §13](../../v2/09-前端重构方案.md)）；养成计划：`rg -n "养成计划" docs/v2/11-Agent模块设计.md`。

既有代码入口：

| 位置 | 符号 | 用途 |
|---|---|---|
| `maa_api/services/account/plans.py` 约 191、257、431、543、624 | `UpgradePlanService`、`start`、`refresh`、`on_pipeline_finished`、`cancel` | 计划由 `start_upgrade_plan` 工具建，`session_id` 记调用工具的会话；流水线终态时自动重算，材料齐了自己标 completed 并删定时任务 |
| `maa_api/db/models.py` 约 1165 | `UpgradePlan`（`id` str36，`status` active/completed/cancelled，`schedule_id`，`session_id`） | 目标关联的计划 |
| `maa_api/agent/tools/account.py` 约 92 | `UpgradeTarget` | 目标里的练度目标复用它 |
| `maa_api/services/schedule_service.py` 约 314、392、413、163 | `get`、`patch(id, {"enabled": ...})`、`delete`、`cron_trigger` | 停用与恢复定时任务；cron 校验 |
| `maa_api/agent/policy.py` 约 145、174–190 | `PolicyEngine.evaluate`、消耗类判定分支（`_consumption_reasons`） | `update_goal` 的参数规则照它写 |
| `maa_api/services/webpush_service.py` 约 352 | `WebPushService.broadcast(payload)` | 推送；载荷 `{title, body, tag, data: {url}}`，`web/src/sw.ts` 点击时打开 `data.url` |
| `maa_api/api/ws.py` 约 117 | `ConnectionManager.has_visible_client()` | 「没有可见页面」的判断 |

## 4. 设计

### 4.1 数据模型

枚举（`maa_api/domain/enums.py`，同步 `tests/domain/test_enums.py`）：`GoalStatus`：active, paused, done, failed, cancelled；`MilestoneStatus`：pending, active, done, missed, skipped。

```python
class Goal(SQLModel, table=True):
    """长程目标：拆成里程碑，执行靠养成计划与定时任务（docs/v3/08 §5）。"""
    __tablename__ = "goal"
    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    title: str = Field(max_length=64)
    description: str | None = Field(default=None, sa_column=text_column("description"))
    target: dict[str, Any] = Field(default_factory=dict, sa_column=json_column("target", nullable=False, server_default="'{}'"))
    deadline: datetime | None = Field(default=None, sa_column=datetime_column("deadline", nullable=True))
    status: str = Field(default=GoalStatus.ACTIVE, max_length=16)
    planning_ticket_id: str | None = Field(default=None, foreign_key="ticket.id", ondelete="SET NULL", max_length=36)
    upgrade_plan_id: str | None = Field(default=None, foreign_key="upgrade_plan.id", ondelete="SET NULL", max_length=36)
    schedule_ids: list[str] = Field(default_factory=list, sa_column=json_column("schedule_ids", nullable=False, server_default="'[]'"))
    review_cron: str = Field(default="0 22 * * *", max_length=64)
    last_review_at: datetime | None = Field(default=None, sa_column=datetime_column("last_review_at", nullable=True))
    progress: dict[str, Any] = Field(default_factory=dict, sa_column=json_column("progress", nullable=False, server_default="'{}'"))
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))
    ended_at: datetime | None = Field(default=None, sa_column=datetime_column("ended_at", nullable=True))
    __table_args__ = (Index("ix_goal_status_updated_at", "status", "updated_at"),)


class GoalMilestone(SQLModel, table=True):
    __tablename__ = "goal_milestone"
    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    goal_id: str = Field(foreign_key="goal.id", ondelete="CASCADE", max_length=36)
    ord: int
    title: str = Field(max_length=120)
    due_at: datetime | None = Field(default=None, sa_column=datetime_column("due_at", nullable=True))
    status: str = Field(default=MilestoneStatus.PENDING, max_length=16)
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=json_column("metrics", nullable=False, server_default="'{}'"))
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))
    __table_args__ = (Index("ix_goal_milestone_goal_id_ord", "goal_id", "ord"),)
```

`Ticket` 加 `goal_id: str | None = Field(default=None, foreign_key="goal.id", ondelete="SET NULL", max_length=36)`。`goal.planning_ticket_id`↔`ticket.goal_id` 是循环外键，处理方式与 M69、M81 一致。

`goal.progress` 的键：`planning`（`pending`、`done`、`failed`、`dropped`、`invalid_details`）、`summary`（规划摘要）、`last_snapshot`（08 §5 的复盘快照）、`deadline_notified`（bool）、`paused_schedule_ids`（暂停时停用了哪些定时任务）、`notes`（至多 10 条处理说明，例如「定时任务 X 已不存在，已从目标移除」）。

迁移 `0035_goals.py`（`down_revision` 为前置检查 #3 的值）：

```python
def upgrade() -> None:
    op.create_table("goal", ...,            # 列照模型；target、progress 带 server_default '{}'，schedule_ids 带 '[]'
        sa.ForeignKeyConstraint(["planning_ticket_id"], ["ticket.id"], name="fk_goal_planning_ticket_id_ticket", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["upgrade_plan_id"], ["upgrade_plan.id"], name="fk_goal_upgrade_plan_id_upgrade_plan", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_goal"))
    op.create_index("ix_goal_status_updated_at", "goal", ["status", "updated_at"])
    op.create_table("goal_milestone", ...,
        sa.ForeignKeyConstraint(["goal_id"], ["goal.id"], name="fk_goal_milestone_goal_id_goal", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_goal_milestone"))
    op.create_index("ix_goal_milestone_goal_id_ord", "goal_milestone", ["goal_id", "ord"])
    with op.batch_alter_table("ticket", copy_from=_ticket_table(with_goal=False)) as batch_op:
        batch_op.add_column(sa.Column("goal_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key("fk_ticket_goal_id_goal", "goal", ["goal_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("ticket", copy_from=_ticket_table(with_goal=True)) as batch_op:
        batch_op.drop_constraint("fk_ticket_goal_id_goal", type_="foreignkey")
        batch_op.drop_column("goal_id")
    op.drop_index("ix_goal_milestone_goal_id_ord", table_name="goal_milestone")
    op.drop_table("goal_milestone")
    op.drop_index("ix_goal_status_updated_at", table_name="goal")
    op.drop_table("goal")
```

`_ticket_table` 照 M81 的同名函数写，列包含 M81 加的 `source_trigger_id` 及其外键。降级会删掉全部目标与里程碑，关联的养成计划与定时任务不受影响。

仓储 `maa_api/db/repositories/goals.py`：`GoalRepository`（`create`、`get`、`page(statuses, page, size)`（按 `updated_at` 倒序）、`list_by_status(statuses)`、`update`），`GoalMilestoneRepository`（`list_by_goal(goal_id)`（按 `ord`）、`replace_all(goal_id, rows)`、`set_status(goal_id, ord, status)`）。M69 的工单仓储加 `list_by_goal(goal_id, *, limit=20)`（`created_at` 倒序）。

### 4.2 模块与文件

#### `maa_api/agent/goals/models.py`

```python
class GoalTarget(BaseModel):                       # extra="forbid"
    text: str = Field(min_length=1, max_length=1000)                       # 整理过的用户原话
    operators: list[UpgradeTarget] = Field(default_factory=list, max_length=10)   # 练度目标，可空
    run_times: list[str] = Field(default_factory=list, max_length=4)       # "HH:MM"，给养成计划用，可空

class GoalCreate(BaseModel):                       # extra="forbid"
    title: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    target: GoalTarget
    deadline: datetime | None = None               # 必须带时区；入库转成 naive UTC
    review_cron: str | None = Field(default=None, max_length=64)   # 空时取 goals.default_review_cron

class GoalUpdate(BaseModel):                       # extra="forbid"；用 model_fields_set 区分「没传」与「传 null」
    title: str | None = Field(default=None, min_length=1, max_length=64)
    deadline: datetime | None = None               # 传 null 表示去掉截止
    status: Literal["active", "paused", "cancelled"] | None = None

class MilestoneDraft(BaseModel):
    title: str = Field(min_length=1, max_length=120); due_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)

class GoalPlanDetails(BaseModel):                  # 规划工单汇报 details["goal_plan"]
    milestones: list[MilestoneDraft] = Field(min_length=1, max_length=12)
    schedule_ids: list[str] = Field(default_factory=list, max_length=8)
    summary: str | None = Field(default=None, max_length=600)

class MilestoneUpdate(BaseModel):
    ord: int = Field(ge=1, le=12)
    status: Literal["pending", "active", "done", "missed", "skipped"]
    note: str | None = Field(default=None, max_length=200)

class GoalReviewDetails(BaseModel):                # 复盘工单汇报 details["goal_review"]
    milestones: list[MilestoneUpdate] = Field(default_factory=list, max_length=12)
    summary: str | None = Field(default=None, max_length=600)
    goal_status: Literal["done"] | None = None
```

响应模型（同文件）：`MilestoneOut`（id、ord、title、due_at、status、metrics）、`GoalTicketOut`（id、title、kind、status、last_report_status、updated_at）、`GoalOut`（goal 的全部列，另加 `next_review_at`）、`GoalDetail(GoalOut)`（加 `milestones`、`tickets`（至多 20 张，`created_at` 倒序））、`GoalSummary`（id、title、status、deadline、milestones_total、milestones_done、next_review_at、updated_at）、`GoalPage`（`{items, total, page, size}`）。时间输出带 `Z`。

#### `maa_api/agent/goals/service.py`

```python
GOAL_REVIEW_JOB_PREFIX = "maa-goal-review-"

class GoalService:
    def __init__(self, session_factory, *, app, ticket_service, thread_router, trigger_service,
                 upgrade_plan_service, get_schedule_service: Callable[[], Any], settings_provider,
                 clock: Callable[[], datetime] = utcnow) -> None: ...
    async def create(self, payload: GoalCreate, *, source: EventSource = EventSource.WEB,
                     sender: str = "web") -> GoalDetail: ...
    async def list_goals(self, *, statuses: list[GoalStatus] | None, page: int, size: int) -> GoalPage: ...
    async def get(self, goal_id: str) -> GoalDetail: ...                     # GOAL_NOT_FOUND
    async def update(self, goal_id: str, payload: GoalUpdate) -> GoalDetail: ...   # GOAL_NOT_FOUND、GOAL_ENDED
    async def resume_consumption_reasons(self, goal_id: str, *, context: ToolContext) -> list[str]: ...
    async def on_ticket_report(self, ticket: Any, report: Any) -> None: ...  # 汇报监听器
    async def review(self, goal_id: str) -> None: ...                        # 复盘 job 的回调
    def start_jobs(self, scheduler: Any) -> None: ...                        # 给每个 active 目标注册复盘 job
    async def close(self) -> None: ...                                       # 移除自己的 job
```

`get_schedule_service` 返回 `app.state.schedule_service`（定时任务服务在 main.py 后段才建，所以惰性取）。

#### `maa_api/agent/tickets/push.py`（新增文件）

```python
class TicketPushNotifier:
    def __init__(self, *, webpush, has_visible_client: Callable[[], bool],
                 clock: Callable[[], float] = time.monotonic, min_interval_seconds: float = 600.0) -> None: ...
    async def on_report(self, ticket: Any, report: Any) -> None: ...   # 汇报监听器，条件见 §4.5.6
```

#### `maa_api/agent/tools/goals.py`（新增文件）

四个工具，组 `goals`，全部 `rest_visible=False`、`model_visible=True`，在 `build_registry()` 里接在当前最后一组之后注册：

| 工具 | 风险 | 参数 | 返回 |
|---|---|---|---|
| `create_goal` | CONDITIONAL | `GoalCreate` 的字段 | `{goal_id, title, status, planning_ticket_id, next_review_at}` |
| `list_goals` | SAFE（`read_only=True`） | `status?`（多值）、`limit`（1–20，默认 10） | `{items: [GoalSummary]}` |
| `get_goal` | SAFE（`read_only=True`） | `goal_id` | 目标、里程碑、最近 5 张目标工单、`progress.last_snapshot` |
| `update_goal` | CONDITIONAL | `goal_id` + `GoalUpdate` 的字段 | `{goal_id, status, changed: [字段名]}` |

工具描述（写进 `ToolDefinition.description`，祈使句）：

- `create_goal`：「用户要在一段时间内达成一件事时建目标，例如月底前练到某个练度、攒够某种材料。目标会自己建规划工单并按时复盘，同一件事不要再建工单。」
- `list_goals`：「列出目标与进度。用户问有哪些目标、目标进展如何时用。」
- `get_goal`：「看一个目标的里程碑、最近一次复盘与相关工单。」
- `update_goal`：「改目标的标题、截止，或暂停、恢复、取消目标。恢复时如果会重新启用用理智药或源石的定时任务，会请用户确认。」

#### 其他文件的改动

- `maa_api/agent/policy.py`：`PolicyEngine.evaluate` 在消耗类分支之前加两条：`create_goal` 返回 `PolicyDecision(False, RiskLevel.NONE)`（它本身不建定时任务，05 §6.1 的「建定时任务时」由 `update_goal` 的恢复承担）；`update_goal` 调 `context.request.app.state.goal_service.resume_consumption_reasons(goal_id, context=context)`，有理由时返回 `PolicyDecision(True, RiskLevel.CONSUME, reasons=..., confirmation_action="update_goal")`，否则无需确认。取不到服务时按 fail closed 返回需要确认。
- 事件（M81 的文件）：`kinds.py` 登记 `goal.review_due`、`goal.completed`、`goal.deadline_passed`（后两个 attention 恒为真，08 §2.2）；`builtin_rules.py` 加 `goal-review`（§4.7）；`bus.py` 的建单处在 `row.source == "goal"` 时把 `goal_id=row.payload["goal_id"]` 传给建单入口。
- M69 建单入口：加关键字参数 `goal_id: str | None = None`。
- M70 汇报落库入口：提交事务之后，对 `self._report_listeners` 里的每个监听器起一个被跟踪的后台任务 `listener(ticket, report)`，异常只记日志；加 `def add_report_listener(self, listener: Callable[[Any, Any], Awaitable[None]]) -> None`。
- `maa_api/agent/specs/builtin/orchestrator.md`：`tools` 加四个目标工具，正文加 §4.7 的一段，`version` +1。
- `maa_api/api/routers/goals.py`（新）、`maa_api/main.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`maa_api/domain/errors.py`。

### 4.3 接口

路由 `maa_api/api/routers/goals.py`，`APIRouter(prefix="/api/agent", tags=["agent"])`，挂 `require_auth`；服务取 `request.app.state.goal_service`，取不到抛 `SERVICE_UNAVAILABLE`。

| 方法与路径 | 函数名 | 请求 | 响应 | 错误码 |
|---|---|---|---|---|
| GET `/api/agent/goals` | `list_goals` | query：`status`（可多值）、`page=1`、`size=20` | 200 `GoalPage` | `INVALID_PAGINATION` |
| POST `/api/agent/goals` | `create_goal` | `GoalCreate` | 201 `GoalDetail`，`Location: /api/agent/goals/{id}` | `VALIDATION_ERROR`、`SCHEDULE_CRON_INVALID`、`AGENT_DISABLED` |
| GET `/api/agent/goals/{goal_id}` | `get_goal` | — | 200 `GoalDetail` | `GOAL_NOT_FOUND` |
| PUT `/api/agent/goals/{goal_id}` | `update_goal` | `GoalUpdate` | 200 `GoalDetail` | `GOAL_NOT_FOUND`、`GOAL_ENDED`、`VALIDATION_ERROR` |

REST 是用户本人在操作，`update_goal` 的恢复不走确认。`AGENT_DISABLED`（v2 既有，503）用于 `goals.enabled=false`，message「目标功能已在设置里关闭，打开后再建目标」；`SCHEDULE_CRON_INVALID`（v2 既有，400）用于复盘时间不合法。

新错误码：`GOAL_NOT_FOUND` 404「没有这个目标」、`GOAL_ENDED` 409「目标已结束」，同步 `ERROR_HTTP_STATUS` 与 `tests/domain/test_errors.py` 计数各 +2。

创建的响应示例：

```json
{"id": "6f1c…", "title": "银灰精二专三", "status": "active", "description": null,
 "target": {"text": "月底前把银灰练到精二、三技能专三", "operators": [{"operator": "银灰", "elite": 2, "masteries": {"3": 3}}], "run_times": []},
 "deadline": "2026-11-30T14:00:00Z", "planning_ticket_id": "a2e9…", "upgrade_plan_id": null, "schedule_ids": [],
 "review_cron": "0 22 * * *", "next_review_at": "2026-11-20T14:00:00Z", "last_review_at": null,
 "progress": {"planning": "pending"}, "milestones": [], "tickets": [{"id": "a2e9…", "title": "规划：银灰精二专三", "kind": "planner", "status": "open", "last_report_status": null, "updated_at": "2026-11-20T03:00:00Z"}],
 "created_at": "2026-11-20T03:00:00Z", "updated_at": "2026-11-20T03:00:00Z", "ended_at": null}
```

### 4.4 配置项

| 键 | 类型 | 默认 | `_FIELDS` |
|---|---|---|---|
| `goals.enabled` | bool | true | `dict(label="启用目标模式", group="Agent", type="boolean", hot_action="none")` |
| `goals.default_review_cron` | str | `0 22 * * *` | `dict(label="目标默认复盘时间", group="Agent", type="string", description="五段 cron，按北京时间", hot_action="none")` |

`maa_api/settings.py` 新段 `GoalsSettings`（`extra="ignore"`），`Settings.goals`，`SETTING_KEYS` 两行，都不是 config-only；`tests/test_settings.py` 的 ENV_OVERRIDES 加 `MAA_GOALS_ENABLED`、`MAA_GOALS_DEFAULT_REVIEW_CRON`。设置值不是合法 cron 时，GoalService 用 `0 22 * * *` 并记一条 warning。

```yaml
goals:
  # 目标模式开关。关掉后不能新建目标，已有目标停止复盘
  enabled: true
  # 新目标的默认复盘时间，五段 cron，按北京时间
  default_review_cron: "0 22 * * *"
```

### 4.5 行为与算法

#### 4.5.1 建立目标

```
async def create(payload, source, sender):
    if not settings.goals.enabled: raise AppError(AGENT_DISABLED, ...)
    cron = payload.review_cron or settings.goals.default_review_cron
    cron_trigger(cron, RULE_CRON_TIMEZONE)            # 不合法 → AppError(SCHEDULE_CRON_INVALID, "复盘时间不合法：…")
    deadline = payload.deadline 转 naive UTC；不带时区 → AppError(VALIDATION_ERROR, "截止时间要带时区")
    goal = 写一行（status=active，progress={"planning": "pending"}），提交
    ticket = await ticket_service.create(              # 建单入口；参数名以实际为准
        kind="planner", title=f"规划：{goal.title}"[:64], brief=planning_brief(goal), scope=planning_scope(goal),
        origin=TicketOrigin.GOAL, strength=MandateStrength.EXPLICIT, source=source, sender=sender,
        rule_id=None, risk_ceiling=planner 定义的 risk_ceiling（consume）, goal_id=goal.id)
    await thread_router.subscribe(ticket.id, f"goal:{goal.id}")
    goal.planning_ticket_id = ticket.id，提交
    注册复盘 job（§4.5.4）
    return await self.get(goal.id)
```

规划工单用显式委托，理由：它是用户这一次明确提出的请求的直接执行，与总控 `create_ticket` 建的工单同性质；养成计划要用 `start_upgrade_plan`，它每次都要确认（v2 `ACCOUNT_WRITE_TOOLS`），推断委托做不了。之后由复盘规则建的工单是推断委托（风险上限 none），与 V3-D14 一致。03 §6.2 括号里「目标推断」的说法需要据此改写（见末节）。

建单失败（例如 `planner` 定义不在）时，目标仍然保留，`progress.planning="failed"`，接口照常返回 201，详情页显示「规划没开始：<原因>」。

#### 4.5.2 规划结果

`on_ticket_report(ticket, report)`：

```
if ticket.goal_id is None: return
goal = 读；不存在或已结束 → return
if ticket.id == goal.planning_ticket_id:
    if report.status == ready: await _apply_plan(goal, ticket, report.details or {})
    elif report.status in (failed, dropped): progress.planning = report.status
    return
if report.status == ready and "goal_review" in (report.details or {}):
    await _apply_review(goal, report.details["goal_review"])
```

`_apply_plan`：

1. `GoalPlanDetails.model_validate(details.get("goal_plan"))`；不合法时 `progress.planning="invalid_details"`、`progress.notes` 记校验错误，里程碑不动，返回。
2. 里程碑整体替换：`ord` 从 1 编号，第一条 `active`、其余 `pending`。
3. 养成计划：查 `upgrade_plan` 里 `session_id == ticket.worker_session_id` 且 `status == "active"`、`created_at` 最晚的一行，写进 `goal.upgrade_plan_id`；它的 `schedule_id` 不为空时并进 `schedule_ids`。
4. 定时任务：`details.goal_plan.schedule_ids` 逐个 `ScheduleService.get` 校验，不存在的丢弃并在 `notes` 记一条；与第 3 步合并去重，写进 `goal.schedule_ids`。
5. `progress.planning="done"`、`progress.summary=summary`，提交。

#### 4.5.3 复盘结果

`_apply_review`：`GoalReviewDetails` 校验不过就在 `notes` 记一条后返回；按 `ord` 更新里程碑状态（不存在的 `ord` 跳过）；`goal_status == "done"` 且全部里程碑为 done 或 skipped 时走 §4.5.5 的完成。

#### 4.5.4 复盘 job

job id `maa-goal-review-<goal_id>`，触发器 `cron_trigger(goal.review_cron, RULE_CRON_TIMEZONE)`，`misfire_grace_time=300`、`coalesce=True`、`max_instances=1`、`replace_existing=True`，回调 `GoalService.review(goal_id)`。`start_jobs` 给所有 active 目标注册；paused 目标注册后立即 `scheduler.pause_job`；结束的目标不注册。`next_review_at` 取 `scheduler.get_job(id).next_run_time`。

```
async def review(goal_id):
    if not settings.goals.enabled: return
    goal = 读；不是 active → return
    snapshot = await _snapshot(goal)                 # 08 §5 的字段；pending/active 且 due_at < now 的里程碑改成 missed，列进 newly_missed
    if _is_complete(goal, snapshot): await _complete(goal, via="upgrade_plan"); return
    if goal.deadline and now > goal.deadline and not progress.deadline_notified:
        publish(source=goal, kind="goal.deadline_passed", thread_key=f"goal:{id}",
                payload={"goal_id", "title", "deadline", "snapshot"}, dedupe_key=f"goal:{id}:deadline")
        progress.deadline_notified = True
    scheduled_for = 当前本地时间截到分钟（ISO 带时区）
    publish(source=goal, kind="goal.review_due", thread_key=f"goal:{id}",
            payload={"goal_id", "title", "snapshot"}, dedupe_key=f"goal:{id}:review:{scheduled_for}")
    goal.last_review_at = now；progress.last_snapshot = snapshot；提交
```

`_is_complete`：`upgrade_plan_id` 不为空、该计划 `status == "completed"`，且没有里程碑或全部里程碑为 done、skipped。养成计划完成时 v2 已经删了它的定时任务，所以 `snapshot.schedules.missing` 里出现它不算异常。

#### 4.5.5 完成、暂停、恢复、取消

| 动作 | 触发 | 做什么 |
|---|---|---|
| 完成 | `_is_complete` 为真；或复盘汇报 `goal_status="done"` 且里程碑全部 done、skipped | `status=done`、`ended_at`；停用 `schedule_ids` 里还在的定时任务；移除复盘 job；发 `goal.completed`（`payload.via` 为 `upgrade_plan` 或 `review`） |
| 暂停 | `update(status="paused")`，当前 active | 停用 `schedule_ids` 里启用着的定时任务，把它们记进 `progress.paused_schedule_ids`；`pause_job` |
| 恢复 | `update(status="active")`，当前 paused | 重新启用 `paused_schedule_ids`（不存在的跳过并记 `notes`）；`resume_job`；清空 `paused_schedule_ids` |
| 取消 | `update(status="cancelled")` | 养成计划仍为 active 时 `UpgradePlanService.cancel(plan_id)`；停用其余定时任务；用 M70 的取消入口取消这个目标所有未结束的工单（原因「目标已取消」）；移除复盘 job；`status=cancelled`、`ended_at` |

`update` 的其他规则：目标已是 done、failed、cancelled 时抛 `GOAL_ENDED`；状态与当前相同时什么都不做；`deadline` 改到当前时间之后时把 `deadline_notified` 置回 false。

`resume_consumption_reasons`：取 `paused_schedule_ids` 对应定时任务的 `template`，用 `policy._consumption_reasons({"template": template}, tool_name="update_schedule", context=context)` 逐个判断，返回理由列表（每条前加定时任务名）。目标不是 paused 时返回空列表。

#### 4.5.6 后台工单完成推送

`TicketPushNotifier.on_report(ticket, report)`：

```
status = report.status；action = report.human_action or {}
if status not in (ready, blocked, failed) and not (status == waiting and action.get("type") in ("question", "decision")): return
if has_visible_client(): return
if status == waiting and (ticket.id, action.get("ref_id")) 推过: return
if clock() - last_push[ticket.id] < min_interval_seconds: return
payload = {"title": f"{LABELS[status]}：{ticket.title}"[:40],
           "body": (report.summary or "")[:120],
           "tag": f"ticket:{ticket.id}",
           "data": {"url": f"/tickets/{ticket.id}", "ticketId": ticket.id}}
await webpush.broadcast(payload)，异常只记日志；记下 last_push 与 ref_id
```

`LABELS`：ready「已完成」、failed「没做成」、blocked「卡住了」、waiting「等你决定」。waiting 且 `type="confirmation"` 不推：确认请求已有 v2 的推送。推送对来源为 user 的工单同样生效：用户离开页面后它也是后台工单。

#### 4.5.7 装配（main.py）

在 M81 的 `trigger_service` 装配之后：构造 `GoalService`（`get_schedule_service=lambda: getattr(app.state, "schedule_service", None)`）与 `TicketPushNotifier(webpush=webpush_service, has_visible_client=ws_manager.has_visible_client)`，挂 `app.state.goal_service`、`app.state.ticket_push_notifier`；`ticket_service.add_report_listener(goal_service.on_ticket_report)`、`add_report_listener(ticket_push_notifier.on_report)`。在 `schedule_service` 构造之后（约 1345 行）调 `goal_service.start_jobs(update_scheduler)`。关闭时在 `update_scheduler.shutdown` 之前 `await goal_service.close()`，并把两个属性加进两份 `delattr` 清单。

#### 4.5.8 边界情况

| 输入 | 期望 |
|---|---|
| 规划工单汇报 ready 但没有 `details.goal_plan` | `planning=invalid_details`，详情页提示「规划结果缺里程碑」，目标照常复盘 |
| 规划工单没调 `start_upgrade_plan` | `upgrade_plan_id` 为空，只用 `schedule_ids`；完成只能由复盘汇报判定 |
| 用户在定时任务页删掉了目标的定时任务 | 下次复盘 `snapshot.schedules.missing` 列出它；不自动重建 |
| 复盘时 `goals.enabled=false` | 回调直接返回，不发事件 |
| 同一分钟内复盘 job 被触发两次 | `dedupe_key` 相同，只入表一次 |
| 复盘研判为 ignore | 只有事件记录，不建工单，不打扰用户 |
| 规划工单还开着时到了复盘时间 | `goal.review_due` 直通规划工单（它订阅着 `goal:<id>`），不另建复盘工单 |
| 暂停后用户手动启用了某个定时任务 | 恢复时它已启用，跳过 |
| 取消时养成计划已完成或已取消 | 不调 `cancel` |
| 截止过了 | 只发一次 `goal.deadline_passed`，目标保持 active |
| 同一工单 10 分钟内 ready 两次 | 只推第一次 |

### 4.6 前端

- 路由（`web/src/routes/router.tsx`）：`more/goals` → `GoalsPage`，`more/goals/:goalId` → `GoalDetailPage`，都从 `@/features/goals/` 懒加载。
- 入口：`secondaryNav` 在「事件规则」之前加 `{ to: '/more/goals', label: '目标', icon: Target, description: '长期目标、里程碑与复盘' }`。
- 文件：`web/src/features/goals/` 下 `api.ts`、`queries.ts`、`format.ts`、`GoalsPage.tsx`、`GoalDetailPage.tsx`、`GoalCreateDialog.tsx`、`goals.test.tsx`。
- query key（`web/src/api/keys.ts` 的 `agent` 下）：`goals: (f?) => [...root, 'agent', 'goals', f ?? {}]`、`goal: (id) => [...root, 'agent', 'goals', 'detail', id]`。`query-bridge.ts` 收到 `ticket_status` 时多失效一次 `goals`；详情页另设 `refetchInterval: 30_000`。

| 页面 | 内容 | 操作与文案 |
|---|---|---|
| 目标列表 | 分页签「进行中」「已暂停」「已结束」；每行：标题、状态、截止、里程碑「已完成 n / 共 m」、下次复盘时间 | 按钮「新建目标」；空状态「还没有目标」+「在总控里说一句「月底前把某某练到精二」也能建」+ 按钮「新建目标」 |
| 目标详情 | 标题、状态徽章、截止、下次复盘；里程碑列表（序号、标题、预计完成、状态）；进度（养成计划剩余关卡与场次、最近一次复盘时间）；相关工单（点进 `/tickets/<id>`）；`progress.notes` | 按钮「暂停目标」「恢复目标」「取消目标」；取消的确认弹窗标题「取消目标？」，描述「停用它的定时任务，并停止关联的养成计划。」；Toast「已暂停」「已恢复」「已取消」 |
| 新建目标 | 字段：标题、目标（多行，`Field` description「写清要达成什么，例如「银灰精二、三技能专三」」）、补充说明（可空）、截止（日期时间，可空）、复盘时间（时:分，默认取设置换算） | 按钮「新建目标」；成功后跳详情页，Toast「已新建目标」 |

复盘时间换算：界面填 `HH:MM` → cron `MM HH * * *`；设置里的 cron 不是这种形状时，界面显示原 cron，只读。`planning` 为 `pending` 时详情页里程碑区显示「规划中」；为 `failed`、`dropped`、`invalid_details` 时显示对应原因与按钮「查看规划工单」。

### 4.7 提示词与代理定义

规划工单的说明（`planning_brief(goal)`，花括号为字段）：

```
目标「{title}」刚建立，由你做规划。
目标：{target.text}
补充说明：{description 或「没有」}
练度目标：{operators 的 JSON，或「没有」}
截止：{deadline 的北京时间，或「没有截止」}；每天 {复盘时间} 复盘。

要做的事：
1. 弄清现状：练度（get_operator_box）、仓库（get_depot）、相关关卡与活动。
2. 把目标拆成 1–12 个里程碑，每个写标题、预计完成时间与怎么判断完成。
3. 要按计划长期刷材料就用 start_upgrade_plan 建养成计划，它会请用户确认一次；其他需要定时做的事用 create_schedule。
4. 不要用理智药和源石，除非补充说明里写明可以用。
5. 只改动为这个目标新建的养成计划与定时任务。

完成后 report 汇报 ready，details 写：
{"goal_plan": {"milestones": [{"title": "…", "due_at": "2026-11-25T22:00:00+08:00", "metrics": {"how": "…"}}], "schedule_ids": ["…"], "summary": "…"}}
养成计划不用写进 details，系统会按这张工单找到它。
```

规划工单的范围（`planning_scope(goal)`）：「为目标「{title}」做规划：拆里程碑，按需建养成计划与定时任务；只改动为这个目标新建的计划与定时任务，消耗类操作逐次请用户确认。」

内置规则 `goal-review`（`builtin_rules.py`）：`source="goal"`、`kind_pattern="goal.review_due"`、`conditions={}`、`mode=AUTO`、`worker="planner"`、`cooldown_seconds=0`、`draft=goal_review_draft`。研判提示：

```
这是一个目标的定时复盘，payload.snapshot 是程序算好的进度。
进度正常、没有新逾期的里程碑、养成计划在跑、定时任务都在时 ignore。
有新逾期的里程碑、养成计划已完成或已取消、定时任务被删或停用、截止前不到 3 天而里程碑没过半时 accept：
建复盘工单，brief 写清哪里不对，scope 写「复盘目标「标题」并调整为它建立的计划与定时任务，只做不需要确认的操作」。
```

`goal_review_draft`：标题取研判的，没有就「复盘：{title}」；说明为研判的 `brief`（没有就用快照摘要）加固定的一段：

```
这是推断委托的复盘工单：只能做不需要确认的操作（查询、重算、建或停用不用理智药与源石的定时任务）；需要用户决定的事写进汇报的 human_action。
目标 id：{goal_id}。完成后 report 汇报 ready，details 写：
{"goal_review": {"milestones": [{"ord": 1, "status": "done", "note": "…"}], "summary": "…", "goal_status": null}}
目标已经全部达成时 goal_status 写 "done"。没有需要告诉用户的变化时汇报 dropped。
```

总控定义（`orchestrator.md`）正文加一段，`version` +1：

```
用户想在一段时间内达成一件事（练到某个练度、攒够某种材料、在某天之前做完某件事）时，用 create_goal 建目标；目标会自己建规划工单并每天复盘，同一件事不要再 create_ticket。用户问目标进展用 get_goal；要暂停、恢复、取消或改截止用 update_goal。目标的复盘只在有变化时才会转给你。
```

## 5. 实施任务

### T1 枚举、错误码、配置项

- 写范围：`maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`tests/test_settings.py`、`docs/v3/14-现行接口清单.md`（§4）。
- 验证：`.venv/bin/python -m pytest -q tests/domain tests/test_settings.py tests/test_vapid_settings.py > /tmp/m82-t1.log 2>&1; echo $?` → `0`。

### T2 数据模型、迁移与仓储

- 写范围：`maa_api/db/models.py`、`maa_api/db/migrations/versions/0035_goals.py`、`maa_api/db/repositories/goals.py`、M69 工单仓储（`list_by_goal`）、`tests/db/test_models.py`、`tests/db/test_goals_migration.py`（新）。
- 步骤：按 §4.1；`test_models.py` 的 `BUSINESS_TABLES` 加 `goal`、`goal_milestone`，`EXPECTED_INDEXES` 加两条，外键清单加四条（`goal.planning_ticket_id`、`goal.upgrade_plan_id`、`goal_milestone.goal_id`、`ticket.goal_id`），JSON/TEXT 列清单加 `goal.description/target/schedule_ids/progress`、`goal_milestone.metrics`。
- 验证：`.venv/bin/python -m pytest -q tests/db > /tmp/m82-t2.log 2>&1; echo $?` → `0`。

### T3 GoalService

- 写范围：`maa_api/agent/goals/__init__.py`、`models.py`、`service.py`、M69 建单入口（`goal_id`）、`tests/agent/goals/test_service.py`。
- 步骤：按 §4.2、§4.5.1–§4.5.5 实现；测试里的工单服务、话题路由、事件服务用真实实例接临时库，养成计划服务与定时任务服务用 v2 真实服务（`ScheduleService` 配一个不启动的 `AsyncIOScheduler`）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/goals/test_service.py > /tmp/m82-t3.log 2>&1; echo $?` → `0`。

### T4 事件与复盘规则

- 写范围：`maa_api/agent/events/kinds.py`、`builtin_rules.py`、`bus.py`、`tests/agent/events/test_goal_events.py`（新）、M81 的 `test_builtin_rules_match_doc_table`。
- 验证：`.venv/bin/python -m pytest -q tests/agent/events > /tmp/m82-t4.log 2>&1; echo $?` → `0`。

### T5 汇报监听与推送

- 写范围：M70 汇报落库入口所在文件、`maa_api/agent/tickets/push.py`、`tests/agent/tickets/test_push.py`（新）、`tests/agent/tickets/test_report_listeners.py`（新）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/tickets > /tmp/m82-t5.log 2>&1; echo $?` → `0`。

### T6 目标工具、策略与总控定义

- 写范围：`maa_api/agent/tools/goals.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/policy.py`、`maa_api/agent/specs/builtin/orchestrator.md`、`tests/agent/test_tool_catalog.py`、`tests/agent/goals/test_tools.py`（新）、`tests/agent/test_registry_policy.py`。
- 步骤：按 §4.2、§4.7；`EXPECTED_TOOLS` 加四个名字，`len(actual)` 从 N 改为 N+4，模型可见数从 V 改为 V+4。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_tool_catalog.py tests/agent/goals tests/agent/test_registry_policy.py tests/agent/specs > /tmp/m82-t6.log 2>&1; echo $?` → `0`。

### T7 REST 端点与装配

- 写范围：`maa_api/api/routers/goals.py`、`maa_api/main.py`、`tests/api/test_goals_router.py`（新）、`tests/api/test_agent_assembly_wiring.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`（§6 agent 小节加 4 行）。
- 步骤：按 §4.3、§4.5.7；`.venv/bin/python scripts/dump_openapi.py`；`cd web && pnpm gen:api`。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_goals_router.py tests/api/test_agent_assembly_wiring.py tests/domain/test_route_inventory.py > /tmp/m82-t7.log 2>&1; echo $?` → `0`；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → `0`。

### T8 前端

- 写范围：`web/src/features/goals/**`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/api/keys.ts`、`web/src/realtime/query-bridge.ts`。
- 验证：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run > /tmp/m82-t8.log 2>&1; echo $?` → `0`。

### T9 冒烟与评测

- 写范围：`scripts/orchestrator_smoke.py`、`tests/evals/scenarios/`、`tests/evals/cassettes/`。
- 步骤：
  1. 冒烟加 `[ok] goals`：`POST /api/agent/goals` → 断言规划工单存在且 `goal_id` 正确；用汇报落库入口写一条带 `goal_plan` 的 ready 汇报 → 断言里程碑写入；直接 `await goal_service.review(goal_id)` → 断言 `agent_trigger` 有一行 `goal.review_due`。
  2. regression 场景 `orchestrator_create_goal`：用户说「月底前把银灰练到精二」，期望总控调用 `create_goal` 且没有调用 `create_ticket`（手写 cassette，做法同 M81 T10）。
- 验证：`.venv/bin/python scripts/orchestrator_smoke.py 2>&1 | tail -n 1` → `SMOKE OK`；`.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m82-eval.log 2>&1; echo $?` → `0`；总控定义改了，另跑 `--suite capability --mode replay`，通过率不低于台账里 M81 的基线。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/goals/test_service.py` | `test_create_builds_explicit_planning_ticket_and_subscribes` | 工单 `kind=planner`、`origin=goal`、委托 explicit、上限 consume、`goal_id`；订阅 `goal:<id>`；job 已注册 |
| 同上 | `test_create_rejects_bad_cron_and_disabled_goals` | `SCHEDULE_CRON_INVALID`；`goals.enabled=false` 时 `AGENT_DISABLED` |
| 同上 | `test_apply_plan_writes_milestones_and_links_plan_by_worker_session` | 里程碑按序、第一条 active；`upgrade_plan_id` 与计划的定时任务都关联上 |
| 同上 | `test_apply_plan_drops_missing_schedules_and_records_note` | `notes` 有一条 |
| 同上 | `test_invalid_plan_details_marks_planning_invalid` | 里程碑不变 |
| 同上 | `test_review_marks_overdue_milestones_missed_and_publishes` | `newly_missed` 列出；一行 `goal.review_due`；`last_review_at` 更新 |
| 同上 | `test_review_completes_goal_when_plan_completed` | `status=done`；一行 `goal.completed` 交总控；job 移除 |
| 同上 | `test_deadline_passed_published_once` | 两次复盘只有一行 `goal.deadline_passed` |
| 同上 | `test_pause_and_resume_toggle_schedules_and_job` | 暂停后定时任务停用、job 暂停；恢复后反之 |
| 同上 | `test_cancel_cancels_plan_tickets_and_job` | 计划 cancelled、目标工单 cancelled、job 不在 |
| 同上 | `test_update_ended_goal_raises_goal_ended` | `GOAL_ENDED` |
| 同上 | `test_review_report_updates_milestones_and_can_finish` | `goal_status=done` 且里程碑全完成时目标 done |
| `tests/agent/events/test_goal_events.py` | `test_goal_review_rule_creates_inferred_planner_ticket_with_goal_id` | FakeLLM 研判 accept；工单 inferred、上限 none、`origin=goal`、`goal_id`；说明末尾有 `goal_review` 格式 |
| 同上 | `test_goal_review_ignore_records_only`、`test_review_passes_through_open_planning_ticket` | ignore 不建单；规划工单开着时直通它 |
| `tests/agent/tickets/test_push.py` | `test_pushes_ready_blocked_failed_when_no_visible_client` | 三种各一次，载荷 `tag`、`data.url` 正确 |
| 同上 | `test_no_push_when_client_visible_or_progress_or_confirmation_waiting` | 都不推 |
| 同上 | `test_waiting_pushed_once_per_ref_and_interval_limit` | 同 `ref_id` 只推一次；10 分钟内第二次不推 |
| `tests/agent/tickets/test_report_listeners.py` | `test_listener_failure_does_not_break_report` | 监听器抛错，汇报照常落库并转发 |
| `tests/agent/goals/test_tools.py` | `test_goal_tools_registered_hidden_from_rest` | 四个工具、组 `goals`、`rest_visible=False` |
| 同上 | `test_update_goal_resume_with_medicine_schedule_requires_confirmation` | `requires_confirmation=True`、`risk=consume`、理由带定时任务名 |
| 同上 | `test_create_goal_needs_no_confirmation` | `requires_confirmation=False` |
| `tests/api/test_goals_router.py` | `test_create_returns_201_with_location`、`test_get_unknown_404`、`test_update_status_transitions_and_ended_409`、`test_list_filters_status_and_paginates` | 状态码、错误码与分页 |
| `tests/db/test_goals_migration.py` | `test_upgrade_adds_goal_tables_and_ticket_goal_id`、`test_downgrade_removes_them` | 造一行 `ticket` 升降级后数据保留 |
| `web/src/features/goals/goals.test.tsx` | `create dialog posts goal and navigates`、`pause and cancel send status`、`detail shows milestones and planning state` | 请求体、确认弹窗文案、里程碑渲染 |

## 7. 验收

按 [12 §4](../12-实施路线与里程碑.md) 第 2–7 项重跑。另外：

1. 手动：在总控里说「这个月底前把银灰练到精二」，总控调 `create_goal`，目标页出现目标与一张「规划：……」工单；规划工单请求养成计划确认时，全局确认面板出现卡片。
2. 手动：新建一个复盘时间为两分钟后的目标（复盘时间建立后不能改），到点后「事件规则 → 事件记录」出现 `goal.review_due`。
3. 手动：关掉浏览器页面（或切到后台），让一张工单汇报 ready，手机或桌面收到「已完成：……」推送，点开进入工单页。

## 8. 风险与回滚

- 规划工单的显式委托：它能发起需要确认的操作，但每一次仍逐次确认，范围文字限定为本目标的计划与定时任务；与 V3-D14 的差异记 13 §8，等用户确认（§10）。
- 复盘花钱：复盘先过研判，研判计入 `triggers.eval_daily_limit`；默认每天每个目标一次。
- 推送打扰：只在没有可见页面时推，同一工单 10 分钟一次，进度不推。
- 回滚：`git revert -m 1 <merge>`，先 `alembic downgrade 0034_triggers`（或 M81 的实际编号）；降级删掉目标与里程碑，v2 的养成计划与定时任务保留，需要时在定时任务页手动处理。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：§4 加 2 个错误码；§6 agent 小节加 4 条端点，改条数与总数句。
- `config.template.yaml`：`goals` 段。
- 13 §8 记一条：「目标建立时的规划工单用显式委托」（原定 03 §6.2、V3-D14 的「目标推断」）。
- 计数：错误码 +2；端点 +4；业务表 +2；工具 +4（模型可见 +4）；总控定义 `version` +1。

## 10. 停下来问

- 开工前：用户还没确认「规划工单用显式委托」与「`goal-review` 默认 auto」两处（§4.5.1、08 §3.2）。主 agent 若没有拿到确认，先问再做。
- 前置检查 #5、#6 找不到建单入口或汇报落库入口。
- M70 的取消工单入口不能由服务代码调用（只有工具或路由能调）。
- `upgrade_plan.session_id` 记的不是 Worker 会话 id（v3 的 ToolContext 改了口径），导致计划关联不上。
- 需要新增本 spec 没写的错误码、配置键、端点或工具。

## 需要补进契约的名字

| 名字 | 用途 | 建议写进 |
|---|---|---|
| `GoalTarget`、`GoalCreate`、`GoalUpdate`、`GoalPlanDetails`、`GoalReviewDetails` 与响应模型 `GoalDetail`、`GoalSummary` | 目标的输入、汇报 details 约定与响应形状 | 04 §2.4；03 §11.4 `target`、`progress` 的说明 |
| `details.goal_plan`、`details.goal_review` | Worker 汇报里给目标用的结构 | 05 §4.1 汇报契约 |
| `TicketPushNotifier`（`maa_api/agent/tickets/push.py`）、`app.state.ticket_push_notifier` | 后台工单推送 | 02 §2.2、§5 |
| `TicketService.add_report_listener` | 汇报落库后的监听器 | 02 §2.2 TicketService 行 |
| 建单入口参数 `goal_id`、工单仓储 `list_by_goal` | 目标工单 | 02 §2.2 |
| job id `maa-goal-review-<goal_id>` | 复盘 job | 接缝速查 §8 |
| 复用 v2 错误码 `AGENT_DISABLED`（目标功能关闭）、`SCHEDULE_CRON_INVALID`（复盘时间不合法） | 不新增错误码 | 04 §2.4 M82 行 |
| 03 §6.2 `strength` 说明改为「inferred（事件、目标复盘、Watchdog 推断）」，补「目标建立时的规划工单为 explicit」 | 与本 spec §4.5.1 一致 | 03 §6.2、13 V3-D14 |
| 前端路由 `/more/goals`、`/more/goals/:goalId`；推送深链 `/tickets/<id>`（M72 的工单页路由） | 目标页与推送点击 | 11 |
