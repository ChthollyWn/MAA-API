# M84 每日简报与消耗护栏（规格）

> 阶段：P3　依赖：M83（另用到 M67 成本账本、M69 工单、M71 审计列、M72 指挥台、M75 产物的名字）　分支：`brief-spend`　迁移：`0037_spend_ledger`
> 本文是 M84 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 `brief-spend` 分支，基点是 dev，工作区干净 | `git status -sb && git merge-base --is-ancestor dev HEAD && echo ok` | `## brief-spend` 与 `ok` |
| 2 | M83 已合并 | `git tag -l 'v3-m83'` | `v3-m83` |
| 3 | 迁移 head | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0036_watchdog.py`；不是时取下一个号，记进台账 |
| 4 | 策略层的消耗字段没变 | `rg -n '"Fight": \("stone", "medicine", "expiring_medicine"\)\|"Recruit": \("expedite",\)' maa_api/agent/policy.py` | 2 行 |
| 5 | 公招加急次数仍由招募次数决定 | `rg -n "当前内核不再读取此值" maa_api/domain/task.py` | 1 行（`RecruitInput.expedite_times`） |
| 6 | 养成计划每日模板仍用过期药 999 | `rg -n "^EXPIRING_MEDICINE = 999" maa_api/services/account/plans.py` | 1 行 |
| 7 | 队列的归一化与落库位置 | `rg -n "normalized = \[normalize\(item, defaults\)\|stored = await repo.create\(pipeline, tasks\)" maa_api/services/queue_service.py` | 2 行 |
| 8 | 运行中改参入口 | `rg -n "async def set_task_params" maa_api/services/pipeline_runner.py` | 1 行 |
| 9 | 还没有消耗账本 | `rg -n "spend_ledger\|class SpendKind" maa_api` | 无输出 |
| 10 | M70、M71 的中间件已在 | `rg -n "class MandateMiddleware\|class LeaseMiddleware" maa_api/agent/harness/builtin_middleware.py` | 2 行 |
| 11 | 委托上限拦截怎样写审计 | `rg -n "MANDATE_SCOPE_EXCEEDED" maa_api/agent` | 找到写 `status=rejected` 审计的函数，记下名字与参数 |
| 12 | 建单入口 | `rg -n "async def create" maa_api/agent/tickets/service.py` | 至少 1 行；记下能在工具之外调用、可传 origin 与委托字段的方法 |
| 13 | 产物服务的方法 | `rg -n "    async def " maa_api/agent/artifacts/service.py` | 记下建产物与改产物的方法 |
| 14 | 成本账本存在 | `rg -n '__tablename__ = "cost_ledger"' maa_api/db/models.py` | 1 行 |
| 15 | analyst 代理定义存在 | `test -f maa_api/agent/specs/builtin/analyst.md && echo ok` | `ok` |
| 16 | 指挥台的待处理合并与导航 | `rg -n "export function buildAttentionItems" web/src/features/console/attention.ts && rg -n "secondaryNav" web/src/components/layout/nav.ts && rg -n "tickets/:ticketId" web/src/routes/router.tsx` | 3 处都有 |
| 17 | 现行接口清单 | `test -f docs/v3/14-现行接口清单.md && echo ok` | `ok` |
| 18 | 后端基线 | `.venv/bin/python -m pytest -q > /tmp/m84-base.log 2>&1; echo $?; tail -n 5 /tmp/m84-base.log` | 退出码 0；不为 0 时把失败用例记进台账 |
| 19 | §10 第 1 条的裁决已经用户确认 | 看台账或 13 §8 | 已确认；没有就停下来问 |

## 1. 背景与目标

13 V3-D26：理智药、源石、加急许可按日设上限，超额即使确认也拦，默认不限。01 §4：每日简报属于 M84。两件事放在一起，因为简报要报告消耗。

做完以后：

1. 理智药、源石、加急许可的计划消耗在流水线入队时写进 `spend_ledger`，数量取内核实际会收到的参数（§4.5.1）。
2. 代理与定时任务来源的提交超过当日上限时被拦下：代理在确认卡弹出之前就拿到 `SPEND_CAP_EXCEEDED`，这次调用照 09 §4.3 落审计；确认之后、入队之前用量变化导致超额的，入队时再拦一次。前端手动提交不受限，但计入用量（裁决 R1）。
3. 上限默认 -1（不限），0 为禁止；改设置立即生效。
4. `GET /api/agent/spend` 返回今日与近几日用量、当前上限、今天被拦下的提交；`/more/spend` 页面展示它。
5. 每天 `brief.cron` 时刻，`BriefService` 直接建一张 analyst 简报工单（08 §7）；简报全文落成 `report` 产物；工单汇报经总控转述；没有可见页面时 Web Push 一条。
6. `POST /api/agent/brief/run` 立即生成简报，202 返回工单 id。
7. 指挥台「待你处理」在今天有被拦下的提交、或某种用量达到上限的 80% 时出现一行汇总，点开去 `/more/spend`；设置页能改上限并手动生成简报。

## 2. 非目标

- 不从内核回调回填 `actual`（裁决 R3）。一次 `MedicineConfirm` 可能一次用掉多瓶药，按确认次数计会少算，少算会放行超额；v2 把这些回调定为事后审计证据（[v2/06 §3.3](../../v2/06-实时日志与WebSocket.md)，`rg -n "事后审计证据" docs/v2/06-实时日志与WebSocket.md`）。`actual` 列保留，本里程碑一律不写。
- 不管其他消耗：商店购物（`Mall.shopping`）、肉鸽投资（`Roguelike.investment_enabled`）仍只走 v2 的逐次确认。
- 上限是确认之外多加的一道闸，不替代、不改动 v2 的确认分级与口径。
- 新建或修改定时任务时不查上限；定时任务到点入队时查。流水线领取时不再查（裁决 R6）。
- 简报不经事件规则（08 §7），不发事件；被上限拦下的定时任务也不发事件（08 §2.1 只收登记过的种类）。
- 不改 v2 通知（`NotifyService` 的事件与确认的 Web Push）；13 §9 Q7 默认维持不变。
- 简报不推送到 IM（M85 接入；`brief.push_im` 本里程碑只保存不生效）。
- 不新增 WebSocket 事件、工具、代理定义；不改 `analyst.md`。

## 3. 相关文档

- [03 §11.6](../03-数据模型.md)：`spend_ledger`；§13：`SpendKind`；§14：本表不在保留策略里，不自动删除。
- [04 §2.4](../04-接口与事件协议.md) 的 M84 两行；§5 的 `SPEND_CAP_EXCEEDED`；§6 的 `brief.*`、`spend.*`。
- [05 §5.2](../05-运行时与代理定义.md) 第 15 条 `SpendMiddleware`；§3.2 的 `analyst`；§4.1 汇报契约。
- [08 §1.2、§1.5、§7](../08-主动性与通道.md)：简报长度、推送条件、简报的建单方式与内容。
- [09 §4.3、§6](../09-安全与治理.md)：拦截落审计；消耗护栏（与本文的口径差异见 §10 第 1 条）。
- [11 §2.3、§3.2、§9](../11-前端指挥台.md)：`/more/spend` 路由、待你处理的来源、管理页清单。
- [13](../13-决策记录.md) V3-D26、§9 Q4、Q7。
- v2：养成计划用药口径 Q46（`rg -n "Q46" docs/v2/13-决策记录.md`）；Web Push 只在用户没看页面时推（`rg -n "13.1" docs/v2/09-前端重构方案.md`）。
- 代码入口：
  - `maa_api/agent/policy.py`：`_CONSUMPTION_FIELD_ORDER`（约 59 行）、`_consumption_reasons`（约 628 行）、`_task_consumption_reasons`（约 791 行）、`_is_consuming`（约 813 行）。
  - `maa_api/domain/task.py`：`FightInput.medicine` / `expiring_medicine` / `stone`（约 327–350 行）、`RecruitInput.times` / `expedite` / `expedite_times`（约 458–484 行）、`TASK_MODELS`（约 1534 行）、`ChannelDefaults`（约 1642 行）、`normalize`（约 1669 行）。
  - `maa_api/services/queue_service.py`：`QueueService.submit`（约 74 行，归一化约 165 行，落库约 191 行）；`maa_api/services/pipeline_runner.py`：`PipelineRunner.set_task_params`（约 591 行，只改运行中的任务）。
  - `maa_api/agent/tools/pipeline.py`：`submit_pipeline`（约 309 行，来源固定 `AGENT`）、`set_task_params`（约 347 行）；`maa_api/api/routers/agent.py`：`invoke_tool`（约 442 行）。
  - `maa_api/services/schedule_service.py`：`run_now`（约 424 行）、`_submit_schedule`（约 531 行）；`maa_api/services/account/plans.py`：`EXPIRING_MEDICINE = 999`（约 61 行）。
  - `maa_api/db/models.py`：`SanityObservation`（约 282 行）、`Schedule`（约 391 行）、`AgentAudit`（约 545 行）、`UpgradePlan`（约 1165 行）。
  - `maa_api/agent/knowledge/__init__.py`：`render_activity_section`（约 246 行）、`render_gacha_lines`（约 328 行）。`maa_api/services/game/activities.py`：`report_from_snapshot`（约 490 行）。`maa_api/services/game/gacha.py`：`GachaService.pools`（约 169 行）。
  - `maa_api/services/webpush_service.py`：`WebPushService.broadcast`（约 352 行），载荷形状见 main.py 约 1074 行。

## 4. 设计

### 4.1 数据模型

`maa_api/domain/enums.py` 新增（同步 `tests/domain/test_enums.py`）：

```python
class SpendKind(StrEnum):
    MEDICINE = "medicine"            # 理智药（不含 48 小时内过期的药，裁决 R2）
    STONE = "stone"                  # 源石
    EXPEDITE = "expedite"            # 加急许可
```

`maa_api/db/models.py` 末尾新增。`pipeline.id`、`task.id` 已核实都是 `str`、`max_length=36`：

```python
class SpendLedger(SQLModel, table=True):
    """每日消耗账本：一行是一个任务计划消耗的一种资源（docs/v3/03 §11.6）。"""

    __tablename__ = "spend_ledger"

    id: int | None = Field(default=None, primary_key=True)
    pipeline_id: str | None = Field(default=None, foreign_key="pipeline.id", ondelete="SET NULL", max_length=36)
    task_id: str | None = Field(default=None, foreign_key="task.id", ondelete="SET NULL", max_length=36)
    kind: str = Field(max_length=16)
    planned: int
    actual: int | None = Field(default=None)
    source: str = Field(max_length=16)
    ticket_id: str | None = Field(default=None, foreign_key="ticket.id", ondelete="SET NULL", max_length=36)
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))

    __table_args__ = (
        Index("ix_spend_ledger_kind_created_at", "kind", "created_at"),
        {"sqlite_autoincrement": True},
    )
```

迁移 `maa_api/db/migrations/versions/0037_spend_ledger.py`（模块 docstring 写清「纯新增、不回填」）：

```python
revision: str = "0037_spend_ledger"
down_revision: Union[str, Sequence[str], None] = "0036_watchdog"


def upgrade() -> None:
    op.create_table(
        "spend_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("planned", sa.Integer(), nullable=False),
        sa.Column("actual", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipeline.id"], name="fk_spend_ledger_pipeline_id_pipeline", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], name="fk_spend_ledger_task_id_task", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"], name="fk_spend_ledger_ticket_id_ticket", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_spend_ledger"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_spend_ledger_kind_created_at", "spend_ledger", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_spend_ledger_kind_created_at", table_name="spend_ledger")
    op.drop_table("spend_ledger")
```

同步 `tests/db/test_models.py`：`BUSINESS_TABLES` 加 `spend_ledger`；`EXPECTED_INDEXES` 加 `ix_spend_ledger_kind_created_at`；外键清单加三条。

### 4.2 模块与文件

| 文件 | 动作 | 内容 |
|---|---|---|
| `maa_api/agent/spend.py` | 新建 | 计划量、日界、用量、上限判定、拦截审计、`SpendGuard` |
| `maa_api/agent/harness/builtin_middleware.py` | 改 | `SpendMiddleware`；按 05 §5.2 第 15 位挂进链 |
| `maa_api/services/queue_service.py` | 改 | `bind_spend_guard`；`submit` 在落库后、提交前预留 |
| `maa_api/services/pipeline_runner.py` | 改 | `bind_spend_guard`；`set_task_params` 加 `spend_source` |
| `maa_api/agent/tools/pipeline.py` | 改 | `set_task_params` 工具传 `spend_source=PipelineSource.AGENT` |
| `maa_api/services/schedule_service.py` | 改 | 到点入队被上限拦下时记 `skipped` |
| `maa_api/api/routers/agent.py` | 改 | `invoke_tool` 在调确认服务之前预检；`responses` 加 `SPEND_CAP_EXCEEDED` |
| `maa_api/agent/brief/__init__.py`、`facts.py`、`render.py`、`service.py` | 新建 | 事实收集、渲染、`BriefService` |
| `maa_api/api/routers/spend.py` | 新建 | `summarize_spend`、`run_brief` |
| `maa_api/main.py` | 改 | 装配与 include_router |

`maa_api/agent/spend.py` 的签名：

```python
SPEND_TIMEZONE = "Asia/Shanghai"
SPEND_DAY_RESET_HOUR = 4                      # 国服每日刷新时刻（裁决 R4）
CAPPED_SOURCES = frozenset({PipelineSource.AGENT, PipelineSource.SCHEDULED})
SPEND_TOOLS = frozenset({"submit_pipeline", "set_task_params"})
BLOCK_HISTORY_LIMIT = 50
SPEND_LABELS = {SpendKind.MEDICINE: "理智药", SpendKind.STONE: "源石", SpendKind.EXPEDITE: "加急许可"}

def planned_spend(type_name: str, params: Mapping[str, Any]) -> dict[SpendKind, int]:
    """内核会收到的完整参数 → 计划消耗，只返回大于 0 的项（§4.5.1）。"""

def merged_task_params(type_name: str, existing: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """已有参数叠加补丁：用 TASK_MODELS 校验后按别名导出；校验失败（ValidationError、AppError）退回字典合并。"""

def spend_day_window(now: datetime | None = None) -> tuple[datetime, datetime, date]:
    """当前消耗日的 [start, end)（naive UTC）与日期标签（§4.5.2）。"""

def spend_day_of(created_at: datetime) -> date:
    """一条 naive UTC 时间属于哪个消耗日。"""

@dataclass(frozen=True, slots=True)
class SpendViolation:
    kind: SpendKind
    limit: int
    used: int
    requested: int

class SpendGuard:
    """消耗账本的唯一写入者与上限判定者（docs/v3/02 §2.2）。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *,
                 settings_provider: Callable[[], Settings] = get_settings,
                 clock: Callable[[], datetime] = utcnow) -> None: ...
    def limits(self) -> dict[SpendKind, int]:
        """每次现读 spend.daily_*_limit。"""
    async def usage(self, session: AsyncSession, *, start: datetime, end: datetime) -> dict[SpendKind, int]:
        """窗口内各种类的已计用量（§4.5.3），三种都有键。"""
    async def precheck_tool_call(self, name: str, arguments: Mapping[str, Any], *,
                                 agent_session_id: str | None) -> None:
        """确认之前的只读预检，超额抛 AppError(SPEND_CAP_EXCEEDED) 并记一条拦截；
        参数解析失败、任务行不存在、读库异常一律直接返回（入队时的检查是最终裁决）。"""
    @asynccontextmanager
    async def reserve_submission(self, session: AsyncSession, *, source: PipelineSource,
                                 tasks: Sequence[Task], agent_session_id: str | None,
                                 ref: str | None = None) -> AsyncIterator[None]:
        """持守卫锁，按 tasks[i].params 算计划量；受限来源超额就抛错，否则把账本行加进 session 并 flush。
        调用方在 async with 块内提交，提交后才放锁。"""
    @asynccontextmanager
    async def reserve_patch(self, session: AsyncSession, *, task: Task, merged_params: Mapping[str, Any],
                            source: PipelineSource) -> AsyncIterator[None]:
        """运行中改参：按调高的差额判定，调高就改写或补账本行，调低不减（裁决 R5）。"""
    async def summary(self, *, since: date | None = None) -> dict[str, Any]:
        """GET /api/agent/spend 的响应体（§4.3）。"""
    def blocked_today(self) -> list[dict[str, Any]]:
        """今天（本进程启动以来）被拦下的提交，最新的在前。"""
```

守卫锁是 `SpendGuard` 自己的 `asyncio.Lock`，永远是最内层：队列路径先持 `QueueService._admission_lock`，改参路径先持 `PipelineRunner.operation_lock`，然后才拿守卫锁；锁内不再拿别的锁。

`SpendMiddleware`（`builtin_middleware.py`）：

```python
class SpendMiddleware(Middleware):
    """消耗上限的确认前预检（docs/v3/05 §5.2 第 15 位）。"""

    name = "spend"
    applies_to = {"worker", "subagent"}

    async def wrap_tool(self, ctx: RunContext, call: ToolCall, call_next) -> ToolOutcome:
        # call.name 不在 SPEND_TOOLS，或 app.state 没有 spend_guard：直接 call_next。
        # 否则 await guard.precheck_tool_call(call.name, call.arguments, agent_session_id=ctx.session_id)；
        # 抛 SPEND_CAP_EXCEEDED 时：用 §0 第 11 条的函数写一条审计（status=rejected、
        # error_code=SPEND_CAP_EXCEEDED、risk_level=consume，带 run_id、ticket_id、agent_name），
        # 再照 MandateMiddleware 的写法返回 ToolOutcome(status="error", error={code, message, details}, audit_id=…)，
        # 不调用 call_next，因此不会建确认、不会弹卡。其他异常不拦，交给 call_next。
```

`QueueService` 与 `PipelineRunner` 的改动：

- `QueueService.bind_spend_guard(guard)`；`submit()` 约 191–192 行的「`repo.create` 后 `session.commit()`」改成：绑定了守卫时，在 `async with self._spend_guard.reserve_submission(session, source=source, tasks=tasks, agent_session_id=agent_session_id, ref=f"schedule:{schedule_id}" if schedule_id else None):` 块内提交；没绑定时照旧。幂等键命中返回旧行的路径不经过预留，不会重复记账。
- `PipelineRunner.bind_spend_guard(guard)`；`set_task_params(self, task_id, params, *, spend_source: PipelineSource = PipelineSource.MANUAL)`。在 `validated` 算出之后、调用内核之前进入 `reserve_patch(session, task=task, merged_params=validated.model_dump(mode="python", by_alias=True, exclude_unset=False), source=spend_source)`，内核调用、`update_params`、`session.commit()` 都在块内；内核拒绝时抛错，账本改动随会话回滚。

### 4.3 接口

新文件 `maa_api/api/routers/spend.py`，`router = APIRouter(prefix="/api/agent", tags=["agent"])`，每条挂 `require_auth`，响应模型写在本文件。

| 方法与路径 | 函数名 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| GET `/api/agent/spend` | `summarize_spend` | query `since: date \| None`（`YYYY-MM-DD`，缺省为今天往前 6 天） | 200 `SpendSummary` | 400 `INVALID_PARAMETER`（晚于今天，或跨度超过 90 天） |
| POST `/api/agent/brief/run` | `run_brief` | 无请求体 | 202 `BriefRunResult` | 503 `SERVICE_UNAVAILABLE`（`brief_service` 或 `ticket_service` 未装配） |

```python
class SpendKindUsage(BaseModel):
    kind: Literal["medicine", "stone", "expedite"]
    label: str                      # 理智药 / 源石 / 加急许可
    used: int
    limit: int                      # -1 不限，0 禁止
    remaining: int | None           # 不限时为 null

class SpendDayRow(BaseModel):
    date: str                       # 消耗日，YYYY-MM-DD
    medicine: int
    stone: int
    expedite: int

class SpendBlock(BaseModel):
    at: str                         # ISO 8601 带 Z
    source: Literal["agent", "scheduled"]
    kind: Literal["medicine", "stone", "expedite"]
    requested: int
    used: int
    limit: int
    ref: str | None                 # schedule:<id> / ticket:<id> / tool:<name>

class SpendSummary(BaseModel):
    date: str
    day_start: str                  # ISO 8601 带 Z
    day_end: str
    today: list[SpendKindUsage]     # 固定三项，顺序 medicine、stone、expedite
    days: list[SpendDayRow]         # since 到今天，按日期升序
    blocked_today: list[SpendBlock]
    capped_sources: list[str]       # 固定 ["agent", "scheduled"]

class BriefRunResult(BaseModel):
    ticket_id: str
    artifact_id: str
```

`SPEND_CAP_EXCEEDED`（403）的形状：

```json
{"error": {"code": "SPEND_CAP_EXCEEDED",
  "message": "超过今日消耗上限：理智药今天已计 4，上限 5，本次需要 2。可以在设置里调高上限，或改为手动提交",
  "details": {"kind": "medicine", "limit": 5, "used": 4, "requested": 2, "source": "agent",
              "violations": [{"kind": "medicine", "limit": 5, "used": 4, "requested": 2}],
              "day_start": "2026-11-01T20:00:00Z"}}}
```

上限为 0 时 message 改为「今日禁止使用理智药（上限为 0）。可以在设置里调整」。多种同时超额时 message 只写第一种，`violations` 全列。`invoke_tool` 的 `error_responses(...)` 加 `"SPEND_CAP_EXCEEDED"`，OpenAPI 快照随之变化。

### 4.4 配置项

7 个键与默认值照 04 §6，都在分组 `Agent`，都不是 config-only；`brief.cron` 改完要重启（定时在启动时注册），其余立即生效。`settings.py`：

```python
class BriefSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    cron: str = "0 8 * * *"
    push_web: bool = True
    push_im: bool = True

    @field_validator("cron")
    @classmethod
    def _five_fields(cls, value: str) -> str:
        """只校验是五段；能否被 APScheduler 解析在注册时检查（schedule_service.cron_trigger）。"""

class SpendSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    daily_medicine_limit: int = Field(default=-1, ge=-1, le=999)
    daily_stone_limit: int = Field(default=-1, ge=-1, le=999)
    daily_expedite_limit: int = Field(default=-1, ge=-1, le=999)
```

`Settings` 加 `brief: BriefSettings`、`spend: SpendSettings`；`SETTING_KEYS` 加 7 行（`"brief.enabled": ("brief", "enabled")` 这种写法）。`_FIELDS`：

```python
"brief.enabled": dict(label="每日简报", group="Agent", type="boolean", hot_action="none"),
"brief.cron": dict(label="简报生成时间", group="Agent", type="string", description="五段 cron，按北京时间", hot_action="restart_required"),
"brief.push_web": dict(label="简报推送到浏览器", group="Agent", type="boolean", hot_action="none"),
"brief.push_im": dict(label="简报推送到 IM", group="Agent", type="boolean", hot_action="none"),
"spend.daily_medicine_limit": dict(label="每日理智药上限", group="Agent", type="integer", minimum=-1, maximum=999, description="-1 不限，0 禁止；只限制代理与定时任务", hot_action="none"),
"spend.daily_stone_limit": dict(label="每日源石上限", group="Agent", type="integer", minimum=-1, maximum=999, description="-1 不限，0 禁止；只限制代理与定时任务", hot_action="none"),
"spend.daily_expedite_limit": dict(label="每日加急许可上限", group="Agent", type="integer", minimum=-1, maximum=999, description="-1 不限，0 禁止；只限制代理与定时任务", hot_action="none"),
```

`config.template.yaml` 追加：

```yaml
# 每日简报：到点建一张分析工单，结果落成报告产物并推送；cron 按北京时间，改完重启生效
brief:
  enabled: true
  cron: "0 8 * * *"
  push_web: true
  push_im: true

# 消耗上限（北京时间 04:00 分日）：-1 不限，0 禁止；只拦代理与定时任务，手动提交计入用量但不拦
spend:
  daily_medicine_limit: -1
  daily_stone_limit: -1
  daily_expedite_limit: -1
```

`tests/test_settings.py` 的 ENV_OVERRIDES 整表加 7 行（`MAA_BRIEF_ENABLED` 等）。

### 4.5 行为与算法

#### 4.5.1 计划量

输入是一个任务的类型名与「内核会收到的完整参数」：入队路径用 `Task.params`（`normalize` 已注入默认值、剔除 None）；预检路径对每个任务调 `normalize(task, ChannelDefaults())`（客户端渠道的默认值不影响消耗字段）；运行中改参用 `merged_task_params`。

```
def planned_spend(type_name, params):
    out = {}
    if type_name == "Fight":
        out[MEDICINE] = _count(params.get("medicine"))        # 不读 expiring_medicine（R2）
        out[STONE] = _count(params.get("stone"))
    elif type_name == "Recruit" and params.get("expedite") is True:
        out[EXPEDITE] = _count(params.get("times"))           # 不读 expedite_times（内核已不读）
    return {k: v for k, v in out.items() if v > 0}

_count(v) = v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
```

- 字段缺失按 0 计：三个计数字段与 `Recruit.times` 的 schema 默认都是 0，`expedite` 默认 false，内核默认相同。
- 计划量是参数里的上限，实际可能用得少；界面与简报都写明「按参数上限计」。
- 一条流水线可以有多个 Fight，每个任务单独一行账本，用量按种类求和。
- §0 第 5 条不成立（`expedite_times` 又被内核读取）时停下来问，不要自己改口径。

#### 4.5.2 日界

消耗日从北京时间 04:00 起算（R4）。`spend_day_window(now)`：把 `now`（naive UTC）换成 `Asia/Shanghai`，减 4 小时取日期 `d`；`start` = `d` 当天 04:00 北京时间换回 naive UTC，`end` = `start + 1 天`。`spend_day_of(created_at)` 用同样的换算。跨过 04:00 才执行的流水线计入入队那一天。

#### 4.5.3 用量

一行账本的有效量：`actual` 不为空用 `actual`；任务从未开始（`task.started_at IS NULL`）且流水线已是终态时为 0；其余用 `planned`。这样取消排队、前一个任务失败导致后面没跑的任务都不计，不依赖终态钩子。

```sql
SELECT l.kind,
       SUM(CASE
             WHEN l.actual IS NOT NULL THEN l.actual
             WHEN t.id IS NOT NULL AND t.started_at IS NULL
                  AND p.status IN ('completed', 'failed', 'cancelled') THEN 0
             ELSE l.planned
           END) AS used
FROM spend_ledger l
LEFT JOIN task t ON t.id = l.task_id
LEFT JOIN pipeline p ON p.id = l.pipeline_id
WHERE l.kind IN ('medicine', 'stone', 'expedite')
  AND l.created_at >= :start AND l.created_at < :end
GROUP BY l.kind
```

用 SQLAlchemy `select` + `case` 写，不拼字符串。所有来源（含 manual）都计入。

#### 4.5.4 判定

```
def violations(used, requested, limits, source):
    if source not in CAPPED_SOURCES: return []
    out = []
    for kind, need in requested.items():          # need > 0
        limit = limits[kind]
        if limit == -1: continue
        if used[kind] + need > limit: out.append(SpendViolation(kind, limit, used[kind], need))
    return out
```

有任何一项超额就整条拒绝（不部分入队）。拒绝时：记一条拦截（内存 `deque(maxlen=BLOCK_HISTORY_LIMIT)`，字段同 `SpendBlock`，`ref` 由调用方给），写一行 `logger.warning`，抛 `AppError(SPEND_CAP_EXCEEDED, message, details)`。

#### 4.5.5 适用范围（裁决 R1）

| 入口 | 流水线来源 | 受限 | 计入用量 |
|---|---|---|---|
| Worker 与子代理的 `submit_pipeline`、`set_task_params` | agent | 是（确认前预检 + 入队或改参时复检） | 是 |
| REST `POST /api/agent/tools/{name}/invoke` 调这两个工具 | agent（工具固定写 `AGENT`） | 是（路由里预检 + 入队时复检） | 是 |
| 定时任务到点、定时任务「立即执行」 | scheduled | 是（入队时检查） | 是 |
| 前端 `POST /api/pipelines`、重试、前端运行中改参 | manual | 否 | 是 |

#### 4.5.6 三个检查点

1. 预检（只读，确认之前）：`SpendMiddleware` 与 `invoke_tool` 都调 `precheck_tool_call`。`submit_pipeline`：`PipelineCreate.model_validate(arguments)` 失败就返回；对每个任务 `normalize` 后算计划量求和，与 `usage(今天)` 判定，来源按 agent。`set_task_params`：读 `Task` 行，取不到就返回；`merged_task_params` 算新计划量，减去该任务现有账本行的 `planned` 得差额（负数按 0），按差额判定。`ref` 写 `ticket:<agent_session 的 ticket_id>`，没有工单写 `tool:<name>`。两处被拦都写一条 `status=rejected` 的审计；`invoke_tool` 的调用方是 `rest`、没有运行 id，§0 第 11 条的函数只收运行上下文时，在 `spend.py` 加 `record_spend_rejection(session_factory, *, caller, tool_name, arguments, session_id, request_id, details)`，用 `AuditRepository(session).create(AgentAudit(...))` 写，入参按 v2 审计的裁剪函数处理（`rg -n "__truncated__" maa_api/agent` 找到的那个）。
2. 入队（`reserve_submission`，与流水线同一事务）：持守卫锁读用量、判定、加账本行（`ticket_id` 取 `agent_session.ticket_id`，`source` 写来源的值），提交后放锁。确认通过后才执行的提交也走这里，所以「超额即使确认也拦」：工具返回 `SPEND_CAP_EXCEEDED`，确认服务把审计记为失败，流水线不建。账本在入队时就占住了额度，所以领取时不再检查（R6）。
3. 运行中改参（`reserve_patch`，与改参同一事务）：每个种类 `old` 为现有行的 `planned`（没有行为 0），`new` 为新计划量；`new > old` 时，受限来源按差额判定，通过后把行改成 `new`（没有行就补一行，`pipeline_id` 取 `task.pipeline_id`，`ticket_id` 沿用旧行）；`new <= old` 不改。

#### 4.5.7 定时任务被拦

`ScheduleService._submit_schedule`：在现有 `except Exception` 之前加 `except AppError as exc`，`exc.code == ErrorCode.SPEND_CAP_EXCEEDED` 时 `record_run(result="skipped")`、`logger.warning("定时任务 %s 超过今日消耗上限，本次跳过", ...)`，返回 None；`propagate_error=True`（立即执行）时照常抛给调用方。拦截记录的 `ref` 是 `schedule:<id>`。

#### 4.5.8 边界情况

| 输入 | 期望 |
|---|---|
| 上限 -1，Worker 提交 Fight `medicine=999` | 放行；记一行 planned 999 |
| 源石上限 0，Worker 提交 Fight `stone=1` | 预检拦下；没有确认行；有一条 rejected 审计；details.limit 为 0 |
| 理智药上限 5，手动已计 4，Worker 提交 `medicine=2` | 拦下；同样的流水线手动提交放行，今日用量 6 |
| 两个 Fight 各 `medicine=2`，上限 3，已计 0 | 需要 4，整条拦下 |
| Recruit `expedite=true, times=4`，加急上限 3 | 拦下；`times=0` 时计划量 0，放行 |
| Recruit `expedite=false, times=4` | 计划量 0 |
| Fight `expiring_medicine=999, medicine=0`（养成计划每日模板） | 计划量 0，放行（R2） |
| Fight 显式传 `medicine: null` | 不下发，计 0 |
| 预检通过、确认挂起期间定时任务用掉额度、用户再批准 | 入队时抛 `SPEND_CAP_EXCEEDED`；审计失败；流水线不建 |
| 定时任务到点超额 | `last_result=skipped`；不建流水线；`blocked_today` 有一条 `ref=schedule:<id>` |
| 定时任务页点「立即执行」超额 | 前端收到 403 `SPEND_CAP_EXCEEDED` |
| Worker 把运行中 Fight 的 `medicine` 从 2 改成 5，已计 3，上限 5 | 差额 3，拦下；从 5 改成 2 放行，账本仍是 5 |
| 取消一条排队中、计划 3 瓶药的流水线 | 今日用量减 3 |
| 流水线在第 1 个任务失败，第 2 个 Fight（`medicine=2`）没开始 | 第 2 个不计 |
| 03:59 与 04:00（北京时间）各入队一条 | 分属前一天与当天 |
| 改上限 | 下一次检查立即生效（每次现读设置）；已经入队的不受影响 |
| 没装配 `spend_guard`（测试与最小应用） | 队列、改参、工具行为与 v2 相同 |
| 同一个幂等键重复提交 | 返回旧流水线，不新增账本行 |
| 流水线只有 Copilot 任务 | 计划量 0，不写账本 |
| 预检读库异常 | 记日志后放行；入队时的检查照常生效 |

#### 4.5.9 每日简报的流程

1. 到 `brief.cron` 时刻，`BriefService.on_cron()`：`brief.enabled` 为假就返回；今天（北京时间的日历日）已经有一份 `origin=schedule` 的简报产物就返回（08 §7：同一天自动生成的至多一张）。
2. 收集事实，建一份 `report` 产物（`status=pending`），再用 §0 第 12 条的方法建工单：`kind=analyst`、`origin=system`、推断委托（风险上限 none）、来源 `schedule`、`sender="brief"`，工单说明里带事实与产物 id；把工单 id 写回产物 `meta.ticket_id`。
3. analyst 写建议，以 `report(ready, details={brief_artifact_id, suggestions})` 收尾；汇报按 05 §4.1 进总控收件箱，总控转述。
4. `BriefService` 每 30 秒巡检一次，发现汇报就把建议写回产物（`status=ready`），按 08 §1.5 推送。
5. `POST /api/agent/brief/run` 走第 2–4 步，不受「一天一张」限制；工单 `origin=user`、显式委托、来源 `web`、`sender="web"`（analyst 的上限仍是 none）。

`BriefService`（`maa_api/agent/brief/service.py`）：

```python
BRIEF_TIMEZONE = "Asia/Shanghai"
BRIEF_JOB_ID = "maa-brief-daily"
BRIEF_SCOPE = "生成今日简报：只读查询数据并写建议，不提交流水线，不操作设备"
BRIEF_FINALIZE_TIMEOUT_MINUTES = 30
BRIEF_WATCH_SECONDS = 30

class BriefService:
    def __init__(self, app: Any, *, session_factory: async_sessionmaker[AsyncSession],
                 has_visible_client: Callable[[], bool],
                 settings_provider: Callable[[], Settings] = get_settings,
                 clock: Callable[[], datetime] = utcnow) -> None: ...
    def attach_scheduler(self, scheduler: AsyncIOScheduler) -> None:
        """用 cron_trigger(brief.cron, BRIEF_TIMEZONE) 注册 BRIEF_JOB_ID（max_instances=1, coalesce=True,
        replace_existing=True）；cron 解析失败只记 error，不注册，不拦启动。"""
    async def start(self) -> None:
        """恢复：pending 且早于 30 分钟的简报产物直接标 facts_only（不推送）；然后起巡检任务。"""
    async def stop(self) -> None: ...
    async def on_cron(self) -> None: ...
    async def run_now(self) -> BriefRunResult: ...
    async def finalize(self, artifact_id: str, *, ticket_id: str | None,
                       status: Literal["ready", "facts_only"], summary: str | None,
                       suggestions: list[str], push: bool = True) -> None:
        """重新渲染全文写回产物（meta.status、meta.ticket_id），按 §4.5.12 推送。"""
```

`on_cron` 与 `run_now` 共用的准备步骤：

```
facts = await collect_facts(app, now)                       # facts.py
facts_md = render_facts_markdown(facts)                     # render.py
artifact = 产物服务建产物(kind="report", title=f"每日简报 · {M} 月 {D} 日",
    mime="text/markdown", content=render_brief_markdown(facts, None, [], "pending"),
    meta={"brief": True, "brief_date": "YYYY-MM-DD", "status": "pending",
          "origin": "schedule" | "manual", "ticket_id": None, "facts_markdown": facts_md})
brief_text = render_ticket_brief(facts_md, artifact.id, facts.date)   # ≤ 4000 字，facts_md 超长时从尾部截断
title = f"每日简报 {MM-DD}"
```

产物服务没有改内容的方法时（§0 第 13 条），在 `maa_api/agent/artifacts/service.py` 加 `async def update(self, artifact_id: str, *, content: str, meta: dict[str, Any]) -> None`：整体替换 content 与 meta，重算 `size_bytes`、`sha256`，写 `updated_at`。

#### 4.5.10 简报事实

内容合并 08 §7 与本里程碑的要求。`facts.py`：

```python
@dataclass(frozen=True, slots=True)
class BriefFacts:
    date: date
    weekday: str                                  # 周一…周日
    sanity: dict[str, Any] | None                 # {estimated, max, full_at_local, sampled_at_local}
    schedules_today: list[dict[str, Any]]         # [{name, at_local, fight: bool}]
    activity_lines: list[str]
    gacha_lines: list[str]
    plans: list[dict[str, Any]]                   # [{name, missing_kinds, missing_total, days_estimated}]
    goals: list[dict[str, Any]]                   # [{title, deadline_local, done, total}]
    pipelines_yesterday: dict[str, Any]           # {completed, failed, cancelled, failed_items: [≤3 {title, reason}]}
    drops_yesterday: list[dict[str, Any]]         # [{item_name, quantity}] 前 5
    tickets_yesterday: dict[str, Any]             # {done, failed, cancelled, titles: [≤3]}
    cost_yesterday: dict[str, int]                # {币种: cost_micros}
    spend_today: list[dict[str, Any]]             # SpendKindUsage 的 dict
    spend_yesterday: dict[str, int]
    pending: dict[str, Any]                       # {confirmations, questions, tickets: [≤3 标题], tickets_total, alerts, memories, observations}

async def collect_facts(app: Any, now: datetime) -> BriefFacts:
    """每一块单独 try：读失败记 debug，该块为空，不让整份简报失败。"""
```

「昨日」都指上一个消耗日（§4.5.2 的窗口往前一天）。

| 块 | 来源 | 取法 |
|---|---|---|
| 理智 | `sanity_observation` 最新一行（7 天内） | 估算 `min(max, current + 经过分钟 // 6)`；回满时刻 = 采样时刻 + `(max − current) × 6` 分钟；超过 7 天没有采样为 None |
| 今天的定时任务 | `schedule` | `enabled` 且 `next_run_at` 在 [now, 今日 end)；`template` 里有 `name == "Fight"` 的标 `fight=true` |
| 当期活动 | `app.state.activity_index` | `render_activity_section(report_from_snapshot(index.snapshot()))`，最多 12 行 |
| 卡池 | `app.state.gacha_service` | `render_gacha_lines(await gacha.pools())`，最多 6 行 |
| 养成计划 | `upgrade_plan` | `status='active'`；`progress.missing` 的种类数与总数，`progress.days_estimated`（有就写）。不调 `list_plans()`，它会重算并写库 |
| 目标 | `goal`、`goal_milestone` | `goal.status='active'` 最多 5 个；里程碑 `status='done'` 数与总数；`deadline` |
| 昨日流水线 | `pipeline` | `created_at` 在昨日窗口内，按状态计数；失败的取前 3 个 `title` 与 `error_message` 前 40 字 |
| 昨日掉落 | `stage_drop` | 昨日窗口内按 `item_name` 求 `add_quantity` 之和，取前 5 |
| 昨日结束的工单 | `ticket` | `ended_at` 在昨日窗口内，按 done、failed、cancelled 计数，取前 3 个标题 |
| 昨日成本 | `cost_ledger` | 昨日窗口内按 `currency` 求 `cost_micros` 之和 |
| 消耗 | `SpendGuard` | 今日 `summary()["today"]`；昨日 `usage(昨日窗口)` |
| 待处理 | `confirmation`、`agent_question`、`ticket`、`watchdog_alert`、`memory_item`、`trigger_observation` | 依次：`status='pending'` 计数；`status='pending'` 计数；`status IN ('waiting','blocked')` 取前 3 个标题与总数；`resolved_at IS NULL AND severity='critical'` 计数；`status='proposed'` 计数；`verdict IS NULL` 计数 |

跨里程碑的表（`ticket`、`goal`、`goal_milestone`、`cost_ledger`、`watchdog_alert`、`memory_item`、`trigger_observation`）一律用 `sqlalchemy.text()` 只读 03 定死的表名与列名，不 import 它们的模型类。

#### 4.5.11 模板

`render_facts_markdown`（给 analyst 读，也是简报正文的数据部分）与 `render_brief_markdown`（产物全文）共用小节；数据为空的小节整节省略，「理智」与「今日建议」除外：

```markdown
# 每日简报 · 11 月 2 日（周一）

## 理智
- 估算当前 85/135，约 15:20 回满（按每 6 分钟恢复 1 点估算，没算吃药与碎石）
## 今天的定时任务
- 12:00 日常（刷理智）；20:00 基建换班
## 当期活动与关卡
（render_activity_section 的行）
## 卡池
（render_gacha_lines 的行）
## 养成与目标
- 养成计划「精二 xx」：还差 12 种材料，共 340 个，预计 6 天
- 目标「…」：里程碑 2/5，截止 11 月 30 日
## 昨天
- 流水线完成 3 条，失败 1 条：「日常」内核崩溃…
- 掉落：固源岩 ×42、装置 ×9…
- 工单完成 2 张、失败 1 张：「…」
- 模型花费 1.32 元
## 消耗（按任务参数上限计）
- 今日：理智药 0/5，源石 0（不限），加急许可 1/3
- 昨日：理智药 2，源石 0，加急许可 1
## 待处理
- 2 条确认、1 个提问；工单「…」在等你决定；1 条严重告警
## 今日建议
1. …
```

- 没有理智采样时「理智」节写「没有理智记录（跑过一次刷理智后才有）」。
- 成本按 `cost_micros / 1_000_000` 保留两位小数，币种 `CNY` 写「元」，其他写币种代码。
- `status="pending"` 时「今日建议」写「生成中」；`facts_only` 时写「这次没有生成建议」。
- `render_ticket_brief(facts_md, artifact_id, day)` 的全文：

```text
【每日简报】{YYYY-MM-DD}
根据下面整理好的数据写今日建议。数值以这份数据为准；不提交流水线，不操作设备。

{facts_md}

要做的事：
1. 需要时用 get_drop_rates、get_stage、get_item、list_upgrade_plans、read_knowledge 补充查询；查不到的就不写。
2. 写 1 到 5 条今日建议，按优先级排，每条不超过 80 字，说清做什么、为什么；引用数值时写出处。
3. 用 report 收尾：status 为 ready；summary 用三到五句概括今天，不超过 600 字；details 写 {"brief_artifact_id": "{artifact_id}", "suggestions": ["…"]}。
4. 数据缺失的部分在 summary 里如实说明，不要补。
```

#### 4.5.12 收尾与推送

巡检任务每 `BRIEF_WATCH_SECONDS` 秒：

```
for art in 产物里 meta.brief = true 且 meta.status = 'pending' 的行（text() + json_extract）:
    row = SELECT r.ticket_id, r.status, r.summary, r.details, t.kind
          FROM ticket_report r JOIN ticket t ON t.id = r.ticket_id
          WHERE json_extract(r.details, '$.brief_artifact_id') = :art_id
            AND r.status IN ('ready', 'failed', 'blocked')
          ORDER BY r.id DESC LIMIT 1
    if row and row.kind == 'analyst':
        suggestions = details.suggestions 里的字符串，最多 5 条，每条截到 120 字
        finalize(art.id, ticket_id=row.ticket_id,
                 status='ready' if row.status == 'ready' and suggestions else 'facts_only',
                 summary=row.summary, suggestions=suggestions)
    elif art.created_at < now - 30 分钟:
        finalize(art.id, ticket_id=art.meta.ticket_id, status='facts_only', summary=None, suggestions=[])
```

`finalize` 的推送：`push` 为真、`brief.push_web` 为真、`has_visible_client()` 为假时（08 §1.5），`await app.state.webpush_service.broadcast(payload)`，异常只记日志：

```json
{"type": "notification", "title": "每日简报",
 "body": "（summary 截到 120 字；facts_only 时为「今天的简报只生成了数据部分」）",
 "data": {"kind": "brief", "artifact_id": "…", "ticket_id": "…", "url": "/tickets/<ticket_id>"}}
```

没有 `ticket_id` 时 `url` 为 `/`。总控消息来自标准汇报转发（05 §4.1，ready、failed、blocked 都转），`BriefService` 不另写总控收件箱。

#### 4.5.13 简报的边界情况

| 输入 | 期望 |
|---|---|
| `brief.enabled=false` | 到点不做事；`run_brief` 照常 |
| `brief.cron` 一天到点两次（例如 `0 8,20 * * *`） | 第二次发现当天已有 `origin=schedule` 的简报，不再建 |
| analyst 汇报 ready 且有建议 | 产物 `status=ready`，正文有「今日建议」；推送一条 |
| analyst 汇报 failed 或 blocked | `facts_only`；推送「只生成了数据部分」 |
| 30 分钟没有汇报 | `facts_only` 并推送 |
| 汇报的 `brief_artifact_id` 指向非简报产物或已收尾的产物 | 忽略（巡检只看 pending 的简报产物） |
| 活动、卡池、成本读取失败 | 对应小节或行省略，其他照常 |
| 重启时有 pending 简报且早于 30 分钟 | 启动时标 `facts_only`，不推送 |
| 连点两次「生成今日简报」 | 建两张工单（08 §7：手动不受限）；前端在请求进行中禁用按钮 |
| 没配 VAPID 或页面开着 | 不推送；产物照常收尾 |
| `brief.cron` 写错 | 启动日志一条 error，不注册定时；设置页保存时只校验五段 |

### 4.6 前端

- `web/src/api/keys.ts`：`agent.spend: (since?: string) => [...root, 'agent', 'spend', since ?? 'default'] as const`。
- `web/src/features/spend/api.ts`：`spendQueryOptions(since?)`（`GET /api/agent/spend`，`refetchInterval: 60_000`）、`useRunBrief()`（`POST /api/agent/brief/run`，成功后失效工单列表的 query key）；查询函数照 `if (error) throw toApiError(error, response)` 写。
- `web/src/features/spend/SpendPage.tsx`：路由 `/more/spend`（11 §2.3），`Page title="消耗护栏" back="/more"`。只读：今日三行（`理智药 4 / 5` 带进度条；不限时 `源石 3（不限）`，不画进度条）；近 7 天的按日表格（可切到 30 天）；「今天拦下的提交」列表（每条 `20:00 定时任务 · 理智药 需要 3，已计 4 / 5`），空时不显示这一节；页尾按钮「调整上限」去 `/more/settings/agent`。标题旁 InfoHint：「按任务参数里的上限计算，实际可能用得更少。只限制代理与定时任务，手动提交不受限但计入用量。每天 04:00 重新计算。」
- `web/src/components/layout/nav.ts`：`secondaryNav` 在 Watchdog 之后加 `{ to: '/more/spend', label: '消耗护栏', description: '理智药、源石、加急许可的每日用量与上限', group: 'agent' }`（图标用 lucide `Gauge`）；`routes/router.tsx` 懒加载 `SpendPage`。
- 待你处理（11 §3.2）：在 `features/console/attention.ts` 按 M79、M81 加汇总行的同一写法加来源 `spend`（输入 `spend?: SpendSummary`）。进入条件：`blocked_today` 非空，或某一种 `limit > 0 且 used >= ceil(limit × 0.8)`。文案：有拦截时「今天有 N 次提交被消耗上限拦下」，否则「理智药今天已用 4 / 5」（取比例最高的一种）；点开去 `/more/spend`；排在观察记录汇总行之后。`use-attention.ts` 加 `spendQueryOptions()` 查询。
- 设置页：`SettingsPage.tsx` 的 `case 'agent'` 在已有 intro 之后加 `BriefControl`（`web/src/features/settings/BriefControl.tsx`）：`Section title="每日简报"`，按钮「生成今日简报」（请求进行中禁用，成功 Toast「已开始生成简报」，失败「生成简报失败：{原因}」）。`sections.ts` 里 agent 分区的 `summary` 末尾加「、简报与消耗上限」。
- 测试 `web/src/features/spend/spend.test.tsx`：`vi.stubGlobal('fetch')` 造三种响应，断言页面三行与拦截列表、「调整上限」链接；`attention.test.ts` 加 `spend` 来源的进入条件与排序；`BriefControl` 点击后发出 POST 且出现 Toast。

### 4.7 提示词与代理定义

无。简报的做法写在工单说明里（§4.5.11），不改 `analyst.md`，不改总控提示词。

## 5. 实施任务

### T1 数据模型与迁移

- 写范围：`maa_api/domain/enums.py`、`maa_api/db/models.py`、`maa_api/db/migrations/versions/0037_spend_ledger.py`、`tests/domain/test_enums.py`、`tests/db/test_models.py`、`tests/db/test_spend_ledger_migration.py`
- 步骤：1. 加 `SpendKind`。2. 加 `SpendLedger`。3. 按 §4.1 写迁移。4. 同步两份既有测试。5. 照 `tests/db/test_depot_sources_migration.py` 的写法（临时库、`command.upgrade`/`downgrade`）写迁移测试。
- 验证：`.venv/bin/python -m pytest -q tests/db tests/domain/test_enums.py > /tmp/m84-t1.log 2>&1; echo $?; tail -n 5 /tmp/m84-t1.log` → 退出码 0
- 完成判据：`test_initial_migration.py` 的元数据比对、可重复升级、降级到底再升级全部通过。

### T2 错误码与设置项

- 写范围：`maa_api/domain/errors.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`
- 步骤：1. `ErrorCode.SPEND_CAP_EXCEEDED`（上一行中文注释「超过今日消耗上限」），`ERROR_HTTP_STATUS` 写 403。2. 14 的 §4 错误码表加一行并改总数；`test_errors.py` 的计数常量各加 1。3. 按 §4.4 加两个设置类、7 个键、`_FIELDS`、模板片段、ENV_OVERRIDES。
- 验证：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py tests/domain/test_errors.py tests/services/test_setting_service.py > /tmp/m84-t2.log 2>&1; echo $?; tail -n 5 /tmp/m84-t2.log` → 退出码 0
- 完成判据：`GET /api/settings` 里出现 7 个新键，分组都是 `Agent`。

### T3 SpendGuard

- 写范围：`maa_api/agent/spend.py`、`tests/agent/test_spend.py`
- 步骤：按 §4.2 的签名与 §4.5.1–§4.5.6 实现；`summary()` 一次查询取 `since` 到今天的行，在 Python 里按 `spend_day_of` 分桶。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_spend.py > /tmp/m84-t3.log 2>&1; echo $?; tail -n 5 /tmp/m84-t3.log` → 退出码 0
- 完成判据：§6 里 `test_spend.py` 的用例全部通过。

### T4 接入提交路径

- 写范围：`maa_api/services/queue_service.py`、`maa_api/services/pipeline_runner.py`、`maa_api/agent/tools/pipeline.py`、`maa_api/services/schedule_service.py`、`maa_api/api/routers/agent.py`、`tests/services/test_queue_spend.py`、`tests/agent/test_pipeline_runner_set_params.py`、`tests/services/test_schedule_spend.py`、`tests/api/test_agent_invoke_spend.py`
- 步骤：1. 两个 `bind_spend_guard` 与 §4.2 的改动。2. 工具层 `set_task_params` 传 `spend_source=PipelineSource.AGENT`。3. 定时任务按 §4.5.7 处理。4. `invoke_tool` 在 `service = _confirmation_service(request)` 之后、`service.invoke` 之前调 `request.app.state.spend_guard.precheck_tool_call(name, payload.arguments, agent_session_id=None)`（没有 `spend_guard` 属性时跳过），被拦时按 §4.5.6 写审计再抛；`responses` 加码。
- 验证：`.venv/bin/python -m pytest -q tests/services tests/agent/test_pipeline_runner_set_params.py tests/agent/test_pipeline_tools.py tests/api/test_agent_invoke_spend.py > /tmp/m84-t4.log 2>&1; echo $?; tail -n 5 /tmp/m84-t4.log` → 退出码 0
- 完成判据：未绑定守卫时 v2 的队列与改参测试原样通过。

### T5 SpendMiddleware

- 写范围：`maa_api/agent/harness/builtin_middleware.py`、组装中间件链的文件（`rg -n "LeaseMiddleware\(" maa_api/agent` 找到的那一处）、`tests/agent/harness/test_spend_middleware.py`
- 步骤：按 §4.2 实现；在链里放在 `MandateMiddleware` 之后、`LeaseMiddleware` 之前。
- 验证：`.venv/bin/python -m pytest -q tests/agent/harness > /tmp/m84-t5.log 2>&1; echo $?; tail -n 5 /tmp/m84-t5.log` → 退出码 0
- 完成判据：链顺序测试（M65 已有的那条）更新后通过。

### T6 简报事实与渲染

- 写范围：`maa_api/agent/brief/__init__.py`、`maa_api/agent/brief/facts.py`、`maa_api/agent/brief/render.py`、`tests/agent/brief/test_facts.py`、`tests/agent/brief/test_render.py`
- 步骤：按 §4.5.10、§4.5.11 实现；测试在临时库里造 `sanity_observation`、`schedule`、`upgrade_plan`、`pipeline`、`stage_drop`、`ticket`、`cost_ledger` 等行。
- 验证：`.venv/bin/python -m pytest -q tests/agent/brief > /tmp/m84-t6.log 2>&1; echo $?; tail -n 5 /tmp/m84-t6.log` → 退出码 0
- 完成判据：渲染结果不含感叹号；空小节被省略。

### T7 BriefService

- 写范围：`maa_api/agent/brief/service.py`、`maa_api/agent/artifacts/service.py`（只在需要加 `update` 时）、`tests/agent/brief/test_service.py`
- 步骤：按 §4.5.9、§4.5.12 实现。
- 验证：`.venv/bin/python -m pytest -q tests/agent/brief tests/agent/artifacts > /tmp/m84-t7.log 2>&1; echo $?; tail -n 5 /tmp/m84-t7.log` → 退出码 0
- 完成判据：§6 里 `test_service.py` 的用例全部通过。

### T8 接口与装配

- 写范围：`maa_api/api/routers/spend.py`、`maa_api/main.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`、`tests/api/test_spend_router.py`
- 步骤：
  1. 路由按 §4.3。
  2. lifespan 第 10 步（02 §5）：`spend_guard = SpendGuard(db_session.session_factory)`，挂 `app.state.spend_guard`，调 `queue_service.bind_spend_guard(spend_guard)` 与 `pipeline_runner.bind_spend_guard(spend_guard)`；`brief_service = BriefService(app, session_factory=..., has_visible_client=ws_manager.has_visible_client)`，挂 `app.state.brief_service`。
  3. `update_scheduler` 建好之后、`start()` 之前调 `brief_service.attach_scheduler(update_scheduler)`；`update_scheduler.start()` 之后 `await brief_service.start()`；两条关停路径（约 1356 行的异常路径与约 1425 行的正常路径）都加 `await brief_service.stop()`，照 `schedule_service.close()` 的写法放在调度器关停之前。
  4. `create_app()` 里 include 新路由。
  5. `.venv/bin/python scripts/dump_openapi.py`，`cd web && pnpm gen:api`；14 的 agent 小节加两行、改条数与总数。
- 验证：`.venv/bin/python -m pytest -q tests/api tests/domain > /tmp/m84-t8.log 2>&1; echo $?; tail -n 5 /tmp/m84-t8.log` → 退出码 0；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0
- 完成判据：路由清单测试与 OpenAPI 快照通过；tag 仍是 19 个。

### T9 前端

- 写范围：`web/src/api/keys.ts`、`web/src/features/spend/`（`api.ts`、`SpendPage.tsx`、`spend.test.tsx`）、`web/src/components/layout/nav.ts`、`web/src/components/layout/layout.test.tsx`（只在它枚举 `secondaryNav` 时）、`web/src/routes/router.tsx`、`web/src/features/console/attention.ts`、`use-attention.ts`、`attention.test.ts`、`web/src/features/settings/BriefControl.tsx`、`SettingsPage.tsx`、`sections.ts`
- 步骤：按 §4.6。
- 验证：`cd web && pnpm install --frozen-lockfile > /tmp/m84-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m84-tc.log 2>&1 && pnpm test --run > /tmp/m84-vitest.log 2>&1 && pnpm build > /tmp/m84-build.log 2>&1; echo $?` → 0
- 完成判据：`.venv/bin/python scripts/frontend_smoke.py` 末行 `SMOKE OK`。

### T10 冒烟与文档

- 写范围：`scripts/brief_spend_smoke.py`、§9 列出的文档
- 步骤：冒烟用真实 lifespan、临时目录、`FakeAsst`，按 `scripts/orchestrator_smoke.py` 注入 `FakeLLM` 的方式让 analyst 回一次带建议的 `report`（应答函数从消息里取出产物 id）。每步打印 `[ok] …`：
  1. PUT `/api/settings` 把理智药上限设为 2。
  2. REST 调 `submit_pipeline`（Fight `medicine=3`）→ 403 `SPEND_CAP_EXCEEDED`；`GET /api/confirmations?status=pending` 的 total 为 0；`GET /api/agent/audits` 最新一条 `status=rejected`。
  3. `POST /api/pipelines` 提交同样的流水线 → 成功；`GET /api/agent/spend` 的理智药 used 为 3。
  4. 建一个 Fight `medicine=1` 的定时任务并「立即执行」→ 403 `SPEND_CAP_EXCEEDED`。
  5. `POST /api/agent/brief/run` → 202；60 秒内产物 `meta.status` 变成 `ready`。
  末行 `SMOKE OK`，失败打印 `[brief_spend_smoke][FAIL]` 并返回 1。
- 验证：`.venv/bin/python scripts/brief_spend_smoke.py > /tmp/m84-smoke.log 2>&1; echo $?; tail -n 3 /tmp/m84-smoke.log` → 0 与 `SMOKE OK`
- 完成判据：§7 全部通过。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_spend_ledger_migration.py` | `test_upgrade_creates_spend_ledger` | 9 列、可空性、索引名、三条外键与 `SET NULL` |
| | `test_downgrade_drops_spend_ledger` | 表与索引消失；再升级成功 |
| `tests/agent/test_spend.py` | `test_planned_spend_fight_counts_medicine_and_stone` | `medicine=2, stone=1` → 两项；`medicine=True`、`-1` → 0 |
| | `test_planned_spend_ignores_expiring_medicine` | `expiring_medicine=999` → 空 |
| | `test_planned_spend_recruit_uses_times_when_expedite` | `expedite=true, times=4` → expedite 4；false → 空 |
| | `test_day_window_resets_at_four_beijing` | 19:59Z 与 20:00Z 分属两天 |
| | `test_usage_skips_tasks_that_never_started` | 终态流水线里 `started_at` 为空的任务计 0，运行中的计 planned |
| | `test_unlimited_and_zero_limits` | -1 放行任意数；0 拒绝任何正数 |
| | `test_manual_source_counted_not_capped` | manual 超额也写行；随后 agent 被拒 |
| | `test_reserve_submission_rolls_back_on_violation` | 抛错后会话里没有账本行与流水线 |
| | `test_reserve_submission_serializes_concurrent_calls` | 两个并发 agent 提交，只有一个通过 |
| | `test_reserve_patch_counts_only_increase` | 2→5 判差额 3；5→2 不改行 |
| | `test_precheck_submit_blocks_and_records` | 抛 `SPEND_CAP_EXCEEDED`；`blocked_today()` 多一条 |
| | `test_precheck_tolerates_invalid_arguments` | 参数不合法直接返回 |
| | `test_summary_shapes_days_and_today` | 三项顺序固定；`days` 连续；不限时 remaining 为 None |
| `tests/services/test_queue_spend.py` | `test_agent_submit_over_cap_creates_nothing` | 抛错；`pipeline` 表无新行 |
| | `test_idempotent_replay_adds_no_ledger_rows` | 同一幂等键第二次不新增行 |
| | `test_queue_without_guard_behaves_as_v2` | 未绑定时与 v2 一致 |
| `tests/agent/test_pipeline_runner_set_params.py` | `test_agent_patch_over_cap_is_rejected_before_core` | 假内核没收到命令；账本不变 |
| `tests/services/test_schedule_spend.py` | `test_scheduled_fire_over_cap_records_skipped` | `last_result="skipped"`；不建流水线 |
| | `test_run_now_over_cap_raises` | `run_now` 抛 `SPEND_CAP_EXCEEDED` |
| `tests/api/test_agent_invoke_spend.py` | `test_rest_invoke_over_cap_returns_403_without_confirmation` | 403；没有 pending 确认；审计一条 `rejected` |
| `tests/agent/harness/test_spend_middleware.py` | `test_blocks_before_policy_and_audits` | 内层 `call_next` 未被调用；返回 error 结果；审计一条 `rejected` 且带 `ticket_id` |
| | `test_passes_other_tools_and_under_cap` | 非消耗工具、未超额时都调用 `call_next` |
| `tests/agent/brief/test_facts.py` | `test_sanity_estimate_caps_at_max` | 估算不超过上限；回满时刻正确 |
| | `test_missing_sources_leave_sections_empty` | 活动、卡池抛错时对应字段为空列表 |
| | `test_yesterday_blocks_use_previous_spend_day` | 流水线、掉落、工单、成本只取昨日窗口 |
| | `test_pending_counts_from_contract_tables` | 六项计数来自对应表 |
| `tests/agent/brief/test_render.py` | `test_empty_sections_are_omitted` | 只剩「理智」与「今日建议」 |
| | `test_ticket_brief_fits_4000_chars` | 超长事实被截断，总长 ≤ 4000 |
| `tests/agent/brief/test_service.py` | `test_on_cron_creates_one_system_ticket_per_day` | 第一次建 `origin=system` 推断委托工单；同一天第二次不建 |
| | `test_on_cron_respects_enabled` | `brief.enabled=false` 不建 |
| | `test_watch_finalizes_on_ready_report` | 写入 ready 汇报后一次巡检把产物置 ready，调用推送一次 |
| | `test_watch_times_out_to_facts_only` | 超过 30 分钟无汇报 → facts_only |
| | `test_run_now_is_not_limited` | 连续两次各建一张 `origin=user` 工单 |
| | `test_start_marks_stale_pending_without_push` | 启动恢复不推送 |
| | `test_push_skipped_when_page_visible` | `has_visible_client()` 为真时不推 |
| `tests/api/test_spend_router.py` | `test_summarize_spend_default_range` | 7 天；三项 today |
| | `test_summarize_spend_rejects_bad_since` | 未来日期与超过 90 天都回 400 |
| | `test_run_brief_returns_202` | 202，含 `ticket_id`、`artifact_id` |

异步用例写成同步函数加 `asyncio.run(...)`；API 用例用 `tests/api/conftest.py` 的 `make_client`。

## 7. 验收

照 [12 §4](../12-实施路线与里程碑.md) 跑第 2–7 项：

```bash
.venv/bin/python -m pytest -q > /tmp/m84-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m84-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m84-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck && pnpm test --run > /tmp/m84-vitest.log 2>&1 && pnpm build > /tmp/m84-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/brief_spend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m84-eval.log 2>&1; echo $?
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 与基线相比不新增失败；各退出码 0；四个冒烟末行 `SMOKE OK`；断链 `BROKEN 0`。

手动验收（补充检查，结果记台账）：

1. 设置页把加急许可上限设为 1，在总控里说「公招 4 次，用加急」：工单里出现 `SPEND_CAP_EXCEEDED` 的工具结果，确认面板没有新卡片，「待你处理」出现「今天有 1 次提交被消耗上限拦下」，点开 `/more/spend` 能看到这一条。
2. 设置页点「生成今日简报」：工单列表出现「每日简报 MM-DD」；几分钟内产物里有「今日建议」，总控转述一次。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 按参数上限计数偏高，用户觉得拦得太早 | 页面与简报写明口径；R3 列给用户确认；`actual` 列留给以后按可靠回调回填 |
| 定时任务被拦后用户不知道 | `last_result=skipped`、待你处理的汇总行、`/more/spend` 的拦截列表、次日简报四处可见 |
| 守卫锁与既有锁死锁 | 守卫锁永远最内层，锁内不调用会拿别的锁的方法；`test_reserve_submission_serializes_concurrent_calls` 覆盖 |
| analyst 不按格式汇报 | 30 分钟超时收尾为 facts_only，数据部分照常送达 |

回滚：`git revert -m 1 <merge>` 之前先 `alembic downgrade 0036_watchdog`（降级只删表，账本数据丢弃，不影响其他表）。

## 9. 文档同步

- 02 §2.2 服务表加 `BriefService`（`maa_api/agent/brief/service.py`，M84，定时生成简报、收尾与推送）；§4.1 的 `brief/` 写明 `facts.py`、`render.py`、`service.py`。
- 03 §11.6 补一句「`actual` 在 M84 不写」；§13 的 `SpendKind` 注明不含过期理智药。
- 08 §7 的内容清单补上理智估算与今日建议；09 §6 按 §10 第 1 条确认后的口径改（R2–R4、R6）。
- 11 §3.2 来源表加 `spend` 汇总行一条；§9 消耗护栏页的说明补「今天拦下的提交」。
- `docs/v3/14-现行接口清单.md`：agent 小节 +2 条路由，错误码 +1（总数同步）。
- `docs/v3/16-部署指南.md`：`brief.*`、`spend.*` 两段配置说明（含「按北京时间 04:00 分日」「只限制代理与定时任务」）。
- 计数：表 +1（`spend_ledger`）；端点 +2；错误码 +1；工具数不变；设置键 +7。

## 10. 停下来问

1. 开工前把下面的裁决与待拍板交给用户确认，任何一条被推翻就按用户的口径改 §4.5 再做：
   - R1 适用范围：按流水线来源判定，agent（含 REST 工具调用）与 scheduled（含定时任务「立即执行」）受限，manual 不受限但计入用量。
   - R2 48 小时内过期的理智药不计入理智药上限。09 §6 写的是计入；本文不计，因为养成计划的每日模板用 `expiring_medicine=999`，计入会让设了上限的用户的养成计划每天被拦。
   - R3 计划量按任务参数里的上限计，不从回调回填 `actual`（09 §6 写的是回填）；没开始就结束的任务不计。
   - R4 每天北京时间 04:00 分日（09 §6 写的是本机时区 04:00；本文取国服刷新时刻，测试也不随机器时区变）。
   - R5 运行中调高消耗参数按调高后的值计，调低不减。
   - R6 入队时预留额度，领取时不再检查（09 §6 写的是领取时再查一次、超额置 failed）；代价是排队期间调低上限不影响已入队的流水线。
   - B1 简报由 `BriefService` 直接建单、不经事件规则（照 08 §7）；本里程碑的任务说明原先写的是内置事件规则 `daily-brief`，两者只能取一个。
   - 13 §9 Q4：上限默认 -1（不限）。
   - 13 §9 Q7：现有通知维持 v2 不变，不经总控合并。
2. §0 第 11 条找不到委托上限拦截写审计的函数，`MandateMiddleware` 的拦截也不落审计。
3. §0 第 12、13 条找不到可在工具之外调用的建单方法，或产物服务的存取方式与 03 §8.2 不一致。
4. `pipeline.id` 或 `task.id` 的类型不再是 36 位字符串。
5. 需要新增本文没写的错误码、WebSocket 事件、工具或依赖。
6. analyst 现有的工具不够写简报，需要给它加工具。

## 需要补进契约的名字

| 名字 | 用途 |
|---|---|
| `BriefService`（`maa_api/agent/brief/service.py`）；`brief/facts.py`、`brief/render.py` | 简报的服务与模块，02 §2.2、§4.1 |
| `SpendGuard` 的方法 `precheck_tool_call`、`reserve_submission`、`reserve_patch`、`summary`、`blocked_today`；函数 `planned_spend`、`spend_day_window`、`record_spend_rejection`；常量 `SPEND_TIMEZONE`、`SPEND_DAY_RESET_HOUR`、`CAPPED_SOURCES`、`SPEND_TOOLS` | 消耗护栏的接口 |
| `QueueService.bind_spend_guard`、`PipelineRunner.bind_spend_guard`、`PipelineRunner.set_task_params(..., spend_source=)` | v2 服务的新挂点 |
| `BriefSettings`、`SpendSettings`（`Settings.brief`、`Settings.spend`） | 设置类 |
| 定时 job id `maa-brief-daily`；委托 `sender="brief"` | 自动简报 |
| 响应模型 `SpendSummary`、`SpendKindUsage`、`SpendDayRow`、`SpendBlock`、`BriefRunResult` | 04 §2.4 两行的响应形状 |
| 简报产物的 `meta` 键：`brief`、`brief_date`、`status`（pending / ready / facts_only）、`origin`（schedule / manual）、`ticket_id`、`facts_markdown` | 03 §8.2 `artifact.meta` 的约定 |
| `ArtifactService.update(artifact_id, *, content, meta)` | 仅当 M75 没有改内容的方法时新增 |
| 待你处理的来源 `spend`；前端 `features/spend/` | 11 §3.2、02 §4.2 |
| `scripts/brief_spend_smoke.py` | 02 §4.3 冒烟脚本 |
