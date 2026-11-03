# M71 设备租约与确认冒泡（规格）

> 阶段：P1　依赖：M70　分支：`device-lease-bubbling`　迁移：`0027_device_lease`
> 本文是 M71 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在新分支上，工作区干净 | `git status -sb` | `## device-lease-bubbling`，没有未提交改动 |
| 2 | M70 已合并 | `git tag -l 'v3-m70'` | `v3-m70` |
| 3 | 迁移 head | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0026_tickets.py`；不同时按 [03 §2](../03-数据模型.md) 取下一个编号并记台账 |
| 4 | 确认服务的四个回调还在，执行前回调还没有 | `rg -c "on_audit_prepare" maa_api/agent/confirmation.py; rg -n "on_before_execute" maa_api` | 第一条大于 0；第二条没有输出 |
| 5 | `ToolContext` 还是 v2 的五个字段 | `rg -n -A 7 "^class ToolContext" maa_api/agent/registry.py` | 字段为 caller、session_id、request_id、request、db_session，没有 origin |
| 6 | M65 的工具设备级别 | `rg -n "device: Literal" maa_api/agent/registry.py` | 一行 |
| 7 | 三个中间件与链的构造处 | `rg -n "class PolicyMiddleware\|class ClarificationMiddleware\|class MandateMiddleware" maa_api/agent; rg -n "ClarificationMiddleware\(" maa_api/agent` | 第一条三行；第二条至少一行（链的构造处） |
| 8 | PolicyMiddleware 的待确认分支与 SSE 事件 | `rg -n "awaiting_confirmation\|confirmation_required\|question_required" maa_api/agent/harness/builtin_middleware.py` | 至少三行 |
| 9 | `ToolOutcome` 是 dataclass | `rg -n -B 1 "^class ToolOutcome" maa_api/agent/harness/context.py` | 上一行是 `@dataclass` |
| 10 | M70 的上限检查用了 09 §2.3 的原子授权规则 | `rg -n "ceiling_risk" maa_api/agent` | 至少一行在 `MandateMiddleware` 里；没有就停下来报告 |
| 11 | M69 的接口 | `rg -n "def add_end_listener\|async def await_human\|async def human_resolved\|async def to_orchestrator\|async def titles" maa_api/agent/tickets maa_api/db/repositories/ticket.py` | 五行 |
| 12 | 子代理的策略身份还没改 | `rg -n "parent_session_id" maa_api/agent/harness/builtin_middleware.py` | 没有输出；有输出说明 M70 已做，T7 第 4 步跳过并记台账 |
| 13 | 工单结束时还没撤销授权 | `rg -n "revoke_session_grant" maa_api/agent/workers maa_api/agent/orchestrator maa_api/agent/tickets` | 没有输出；有输出时 T11 不再注册撤销监听者，记台账 |
| 14 | 通用 Worker 定义 | `rg -n "^name: general" maa_api/agent/specs/builtin/general.md` | 一行 |
| 15 | 队列领取路径 | `rg -n "async def _claim_next\|async def claim_next\|async def _next_pending_id" maa_api/services/pipeline_runner.py maa_api/db/repositories/pipeline.py` | 三行 |
| 16 | 本里程碑的名字还不存在 | `rg -n "DeviceLease\|device_lease\|DEVICE_LEASE\|hold_device\|lease_changed\|ToolOrigin" maa_api web/src tests --glob '!**/node_modules/**'` | 没有输出 |
| 17 | 装配顺序 | `rg -n "app.state.boot_id = \|app.state.ticket_service = \|app.state.worker_service = \|await pipeline_runner.start" maa_api/main.py` | 四行 |
| 18 | 代理定义端点与前端 key | `rg -c '"/api/agent/specs"' web/openapi.json; rg -n "specs:" web/src/api/keys.ts` | 第一条大于 0；第二条的结果记下（T10 按有无处理） |
| 19 | 基线 | `.venv/bin/python -m pytest -q > /tmp/m71-base.log 2>&1; echo $?` 与 `.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m71-eval-base.log 2>&1; echo $?` | 记下两个结果与 capability 通过率作基线 |

## 1. 背景与目标

M70 之后可以有多个 Worker 与子代理同时运行，但设备只有一台，确认也只出现在各自的工单里。M71 做三件事：设备租约、确认与提问冒泡、子代理最小权限。做完后：

1. `DeviceLeaseService` 以 `device_lease` 表的一行实现容量 1 的硬互斥，先到先得排队，超时 `DEVICE_LEASE_BUSY`；租约有效期、续期、释放、过期、抢占、重启清理都可测。
2. 写设备的工具（`device=exclusive`）在确认通过之后、执行之前取租约：`ConfirmationService.invoke` 新增 `on_before_execute` 回调，`LeaseMiddleware` 经它取租约；等确认、等提问期间不占设备。
3. `hold_device`、`release_device` 两个工具提供跨运行的显式租约；隐式租约在 Worker 的根运行结束时释放。
4. 有代理持有租约时，队列只领手动提交的与持有者工单自己的流水线；手动提交、REST 原子操作、REST 调用写设备的工具、停止运行中的流水线、`DELETE /api/agent/device-lease` 都会抢占。
5. 确认与提问带 `origin`（REST wire、WebSocket、SSE 三处），冒泡到工单状态与总控收件箱；确认面板与提问卡片显示「来自工单「X」（代理名）」。
6. `agent_audit` 记 `run_id`、`ticket_id`、`agent_name`，`GET /api/agent/audits` 可按 `ticket_id` 过滤。
7. 子代理的工具调用以父 Worker 会话的身份经过策略与确认，复用 Worker 的原子操作授权；工单结束时释放租约、撤销 Worker 会话的授权（[09 §2.4](../09-安全与治理.md)）。

## 2. 非目标

- 不做多设备，`device_id` 恒为 `default`。
- 不改 `PolicyEngine` 的判定规则与确认超时；不改 M70 的委托上限检查。
- 抢占不取消已经在跑的流水线与已经在执行的调用。
- 不做指挥台与工单页（M72）；前端只给已有的确认面板与提问卡片加来源一行。
- `daily_ops`、`copilot` 的 `hold_device` 由 M73 随定义一起加。
- 总控的 `stop_pipeline` 工具调用不触发抢占（它是代理动作，用户在网页上点停止才算人工操作）。

## 3. 相关文档

- [02 §3](../02-系统架构.md) 第 4、5 条，§5 第 7 步，§6.2、§6.5 第 4 步、§6.6；[03 §7](../03-数据模型.md)；[04 §2.2](../04-接口与事件协议.md) 的 M71 两行、§3 的 origin、§4.2、§5 的 M71 三行、§6 的 `lease.*`；[05 §2.3](../05-运行时与代理定义.md) 的 `lease_preempted`、§4.2、§5.2 第 16 位、§6、§6.1；[09 §2.4、§3、§4](../09-安全与治理.md)。
- v2：`rg -n "^### 4.3|^### 5.1|^### 5.4" docs/v2/11-Agent模块设计.md`（会话授权、确认时序、中断）。
- 既有代码（行号为 2026-11-02 的 v2 快照，M65–M70 之后会漂移，用符号名定位）：`maa_api/agent/confirmation.py` 的 `invoke`（104）、`_invoke`（127）、`_create_confirmation`（870，WS 事件 925–938）、`_wait_and_execute`（977）、`_execute_and_record`（1071，重建 `ToolContext` 在 1088）、`_create_audit`（1207）、`get`（463）、`list`（471）、`_confirmation_wire`（1530）；`maa_api/agent/registry.py` 的 `ToolContext`（25）；`maa_api/agent/questions.py` 的 `create`（80）、`wait`（166）、`_wire`（472）；`maa_api/services/pipeline_runner.py` 的 `operation_lock`（226）、`wake`（426）、`_claim_next`（866）；`maa_api/db/repositories/pipeline.py` 的 `_next_pending_id`（134）、`claim_next`（152）；`maa_api/api/routers/atomic.py` 的 `click_device`（88）、`back_to_home`（115）；`maa_api/api/routers/device.py` 的 `_run_atomic`（342）；`maa_api/api/routers/pipelines.py` 的 `submit_pipeline`（150）、`cancel_pipeline`（258）、`retry_pipeline`（280）；`maa_api/api/routers/agent.py` 的 `audit_wire`（363）、`invoke_tool`（442）、`list_audits`（915）；`web/src/features/confirmations/ConfirmationsPanel.tsx`（`Confirmation` 15–25、`callerLabel` 70、「调用方」一行 211）；`web/src/features/agent/QuestionCard.tsx`（标题区 121–131）；`web/src/features/agent/types.ts`（`AgentQuestionPrompt` 65）；`tests/services/test_pipeline_runner.py` 的 `runner_env`（208）。

## 4. 设计

### 4.1 数据模型

`maa_api/db/models.py` 追加（`FK`、`TS` 的含义同 M69 spec §4.1，写进代码时展开）：

```python
class DeviceLease(SQLModel, table=True):
    """设备的独占使用权；主键 device_id 保证容量 1，释放即删除行。"""
    __tablename__ = "device_lease"
    device_id: str = Field(primary_key=True, max_length=32)
    lease_id: str = Field(default_factory=new_uuid, max_length=36)
    ticket_id: str | None = FK("ticket.id", "SET NULL")
    run_id: str | None = FK("agent_run.id", "SET NULL")       # 隐式租约：负责释放它的根运行
    holder_agent: str = Field(max_length=32)                   # 第一次取得租约的代理定义名
    purpose: str = Field(max_length=120)
    explicit: bool = Field(default=False)
    acquired_at: datetime = TS("acquired_at")
    expires_at: datetime = Field(sa_column=datetime_column("expires_at"))
    heartbeat_at: datetime = TS("heartbeat_at")
    boot_id: str = Field(max_length=36)
    __table_args__ = (UniqueConstraint("lease_id", name="uq_device_lease_lease_id"),)
```

改动列（全部可空）：

| 表 | 新列 | 说明 |
|---|---|---|
| `confirmation` | `origin_run_id` FK→agent_run SET NULL；`origin_ticket_id` FK→ticket SET NULL；`origin_agent` str32 | 发起确认的代理运行 |
| `agent_question` | 同上三列 | 发起提问的代理运行 |
| `agent_audit` | `run_id` FK→agent_run SET NULL；`ticket_id` FK→ticket SET NULL；`agent_name` str32 | `__table_args__` 加 `Index("ix_agent_audit_ticket_id_created_at", "ticket_id", "created_at")`，放在 `{"sqlite_autoincrement": True}` 之前；这张表的索引因此正好 3 条 |

迁移 `maa_api/db/migrations/versions/0027_device_lease.py`（`down_revision = "0026_tickets"`）：

1. `op.create_table("device_lease", ...)`：`sa.PrimaryKeyConstraint("device_id", name="pk_device_lease")`、`sa.UniqueConstraint("lease_id", name="uq_device_lease_lease_id")`、FK `fk_device_lease_ticket_id_ticket`、`fk_device_lease_run_id_agent_run`（都 SET NULL）。
2. 三张已有表加列与外键，写法同 0026：`batch_alter_table(<表>, copy_from=<0026 时的表定义>)` 里 `add_column` 后 `create_foreign_key`，外键名按约定（`fk_confirmation_origin_run_id_agent_run`、`fk_confirmation_origin_ticket_id_ticket`、`fk_agent_question_origin_run_id_agent_run`、`fk_agent_question_origin_ticket_id_ticket`、`fk_agent_audit_run_id_agent_run`、`fk_agent_audit_ticket_id_ticket`）；`agent_audit` 在同一个 batch 里 `create_index("ix_agent_audit_ticket_id_created_at", ["ticket_id", "created_at"])`。三个 `copy_from` helper（`_confirmation_table`、`_agent_question_table`、`_agent_audit_table`，各带 `with_origin: bool`）照 models.py 里这三个类改动之前的定义逐列抄写，含主键名、全部外键与 ondelete、索引、`sqlite_autoincrement`（`agent_audit`）。
3. downgrade：三个 batch 删外键、索引与列（`copy_from` 传 `with_origin=True` 的定义），再删 `device_lease`。

### 4.2 模块与文件

#### `maa_api/agent/registry.py`（改）

```python
@dataclass(frozen=True, slots=True)
class ToolOrigin:
    """发起这次调用的代理运行；REST 与不经代理运行的调用没有它。"""
    run_id: str
    ticket_id: str | None
    agent: str


@dataclass(frozen=True, slots=True)
class ToolContext:
    caller: CallerType
    session_id: str | None
    request_id: str | None
    request: Any
    db_session: Any
    origin: ToolOrigin | None = None          # 新增，放在最后，位置参数构造的旧调用点不受影响
```

#### `maa_api/agent/origin.py`（新）

```python
async def ticket_titles(session: AsyncSession, ticket_ids: Iterable[str | None]) -> dict[str, str]:
    """去掉空值后调用 M69 的 TicketRepository(session).titles(...)。"""

def origin_wire(*, run_id: str | None, ticket_id: str | None, agent: str | None,
                titles: Mapping[str, str]) -> dict[str, Any] | None:
    """run_id 为空返回 None，否则 {ticket_id, ticket_title, agent, run_id}（04 §4.2）。"""

async def bubble_human_request(state: Any, origin: ToolOrigin | None, *, root_run_id: str,
                               kind: Literal["confirmation", "question"], ref_id: str, prompt: str) -> None:
    """确认或提问建好之后调用（§4.5.6）；任何异常只记日志，不影响确认或提问本身。"""

async def settle_human_request(state: Any, origin: ToolOrigin | None, *, ref_id: str) -> None:
    """确认或提问进入终态后调用：origin 带工单时 ticket_service.human_resolved(ticket_id, ref_id)；异常只记日志。"""
```

`state` 是 `app.state`，用到 `device_lease_service`、`ticket_service`、`thread_router` 三个属性，缺哪个就跳过对应步骤。

#### `maa_api/agent/confirmation.py`（改）

1. 新类型 `BeforeExecuteCallback = Callable[[int], Any]`（参数是审计 id；返回协程时等它；抛出 `AppError` 即中止执行）。
2. `invoke` 与 `_invoke` 加关键字参数 `on_before_execute: BeforeExecuteCallback | None = None`，原样传给 `_wait_and_execute` 与 `_execute_and_record`（同步、异步、需确认、免确认四条路径都传）。
3. `_execute_and_record` 在 `try:` 里、`async with self.session_factory()` 之前调用它：`await _call_before_execute(on_before_execute, audit_id)`，返回后重新取 `started = time.monotonic()`（耗时只算 handler）。它抛的 `AppError` 走已有的 `except AppError` 分支落 `failed` 审计；被取消走已有的 `CancelledError` 分支。新的模块级函数 `_call_before_execute` 照 `_notify_audit_created` 的写法。
4. 重建执行上下文改成 `execution_context = dataclasses.replace(context, db_session=db_session)`，保留 `origin`。
5. `_create_audit` 与 `_create_confirmation` 构造 `AgentAudit` 时写 `run_id`、`ticket_id`、`agent_name`（取 `context.origin`，没有就是 None）；`Confirmation` 写 `origin_run_id`、`origin_ticket_id`、`origin_agent`。
6. `_create_confirmation` 在同一个会话里、`commit` 之前取 `titles = await ticket_titles(session, [origin_ticket_id])`，WS `confirm_request` 的事件字典加 `"origin": origin_wire(...)`。
7. `_confirmation_wire(cls, row, audit=None, titles=None)` 加 `"origin": origin_wire(run_id=row.origin_run_id, ticket_id=row.origin_ticket_id, agent=row.origin_agent, titles=titles or {})`；`get` 与 `list` 在各自的会话里先取 titles 再组装。

#### `maa_api/agent/questions.py`（改）

1. `create` 加关键字参数 `origin: ToolOrigin | None = None`：写进 `AgentQuestion` 的三列与 `ask_user` 审计行的三列。
2. `_wire(row, titles: Mapping[str, str] | None = None)` 加 `"origin"`（同上）。所有调用点在同一个会话里取 titles 后传入：`create`、`get`、`list`、`cancel_pending_for_session`、`expire_due`、`_expire_all`；`wait` 里两处 `return self._wire(...)` 改成 `return await self.get(question_id)`。

#### `maa_api/db/repositories/lease.py`（新）

```python
class DeviceLeaseRepository(BaseRepository):
    async def get(self, device_id: str) -> DeviceLease | None: ...                 # populate_existing
    async def insert(self, lease: DeviceLease) -> DeviceLease: ...                 # add + flush；主键冲突抛 IntegrityError
    async def delete_lease(self, lease_id: str) -> bool: ...
    async def update_expiry(self, lease_id: str, *, expires_at: datetime, heartbeat_at: datetime,
                            explicit: bool, purpose: str) -> bool: ...
    async def list_expired(self, now: datetime) -> list[DeviceLease]: ...          # expires_at <= now
    async def list_orphaned_implicit(self) -> list[DeviceLease]: ...
        # explicit=false 且（run_id 为空，或对应 agent_run 的 status 不在 running/waiting_human）
    async def delete_other_boots(self, boot_id: str) -> int: ...
    async def session_ids_for_ticket(self, ticket_id: str) -> set[str]: ...       # agent_session.ticket_id = ?
    async def session_id_for_run(self, run_id: str) -> str | None: ...
```

#### `maa_api/agent/lease.py`（新）

```python
LEASE_DEVICE_ID = "default"
PreemptReason = Literal["manual_pipeline", "rest_atomic", "rest_tool", "stop_pipeline", "manual_release"]
ReleaseReason = Literal["run_ended", "waiting_human", "released", "ticket_ended", "expired", "orphaned"]
PREEMPT_TEXT: Mapping[str, str] = {
    "manual_pipeline": "用户手动提交了流水线", "rest_atomic": "用户直接操作了设备",
    "rest_tool": "用户通过开放 API 调用了操作设备的工具", "stop_pipeline": "用户停止了正在运行的流水线",
    "manual_release": "用户在网页上释放了设备",
}


@dataclass(frozen=True, slots=True)
class LeaseHolder:
    ticket_id: str | None
    run_id: str                  # Worker 的根运行（子代理用父 Worker 的根运行）
    agent: str
    @property
    def key(self) -> str: ...    # f"ticket:{ticket_id}"，没有工单时 f"run:{run_id}"
    def owns(self, row: DeviceLease) -> bool: ...   # 有工单比 ticket_id，没有工单比 run_id


@dataclass(frozen=True, slots=True)
class ClaimGate:
    lease_id: str
    allowed_session_ids: frozenset[str]


def holder_for(ctx: RunContext) -> LeaseHolder:
    """LeaseHolder(ticket_id=ctx.ticket_id, run_id=ctx.root_run_id, agent=ctx.spec.name)。"""


class DeviceLeaseService:
    def __init__(self, session_factory, *, boot_id: str, thread_router: Any = None,
                 broadcast: Callable[[str, dict[str, Any]], None] | None = None,
                 settings_provider: Callable[[], Settings] = get_settings,
                 on_released: Callable[[], None] | None = None,
                 clock: Callable[[], datetime] = utcnow, poll_seconds: float = 1.0) -> None: ...
    @property
    def waiters(self) -> int: ...
    async def start(self, *, scan_interval_seconds: float = 10.0) -> None: ...   # 起过期扫描任务
    async def close(self) -> None: ...                                           # 停扫描，不删行
    async def current(self) -> DeviceLease | None: ...                           # 顺手清掉已过期的行
    async def acquire(self, holder: LeaseHolder, *, purpose: str, explicit: bool = False,
                      wait_seconds: float | None = None) -> DeviceLease: ...      # §4.5.1
    async def renew(self, holder: LeaseHolder) -> DeviceLease | None: ...        # 不是持有者时返回 None
    async def release(self, holder: LeaseHolder) -> bool: ...
    async def release_run(self, run_id: str, *, reason: ReleaseReason = "run_ended") -> bool: ...
    async def release_ticket(self, ticket_id: str) -> bool: ...
    async def preempt(self, reason: PreemptReason, *, detail: str | None = None) -> DeviceLease | None: ...
    def clear_preempted(self, key: str) -> None: ...
    async def claim_gate(self) -> ClaimGate | None: ...
    async def expire_due(self) -> int: ...
    async def on_ticket_ended(self, event: TicketEnded) -> None: ...   # M69 的结束监听者
    async def wire(self, row: DeviceLease | None) -> dict[str, Any] | None: ...  # §4.3 的 DeviceLeaseOut 形状


class LeaseMiddleware(Middleware):
    name = "lease"
    applies_to = {"worker", "subagent"}
    def __init__(self, lease_service: DeviceLeaseService | None, registry: ToolRegistry) -> None: ...
    async def wrap_tool(self, ctx: RunContext, call: ToolCall, call_next) -> ToolOutcome: ...   # §4.5.5
    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None: ...
```

#### `maa_api/agent/tools/lease.py`（新）

```python
class HoldDeviceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=120)

class ReleaseDeviceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

async def hold_device(params: HoldDeviceParams, ctx: ToolContext) -> dict[str, Any]: ...
async def release_device(params: ReleaseDeviceParams, ctx: ToolContext) -> dict[str, Any]: ...
def lease_tools() -> list[ToolDefinition]: ...
```

两个工具：`group="lease"`、`risk=ToolRisk.SAFE`、`read_only=False`、`rest_visible=False`、`device="none"`（它们自己管理租约，不能再被 `LeaseMiddleware` 包一层）。描述写「什么时候用」：

- `hold_device`：「需要连续占用设备做多步操作（先导航再提交、连续几次点击）时先调用，写明用途。设备在 release_device、工单结束、超时或被用户接管时释放。」
- `release_device`：「连续的设备操作做完，或者要长时间等用户时调用，把设备让出来。」

handler：`ctx.origin` 为空或 `origin.ticket_id` 为空时抛 `AppError(INVALID_PARAMETER, "只有工单里的代理能占用设备")`；`holder = LeaseHolder(ticket_id=origin.ticket_id, run_id=origin.run_id, agent=origin.agent)`；`hold_device` 调 `acquire(holder, purpose=params.purpose, explicit=True)`，返回 `{"held": true, "lease_id", "expires_at", "explicit": true}`；`release_device` 调 `release(holder)`，没有持有时抛 `DEVICE_LEASE_NOT_HELD`，否则返回 `{"released": true}`。服务从 `ctx.request.app.state.device_lease_service` 取，取不到抛 `DEVICE_LEASE_BUSY`（details `{"reason": "lease_service_unavailable"}`）。在 `maa_api/agent/tools/__init__.py` 的 `build_registry()` 里接在 M70 新工具之后注册。

#### 其他改动

| 文件 | 改什么 |
|---|---|
| `maa_api/agent/harness/builtin_middleware.py` | `PolicyMiddleware`：组合执行前钩子、写 origin、冒泡、子代理策略身份（§4.5.5–§4.5.7）；`ClarificationMiddleware`：origin 与冒泡；链的构造处在 `ClarificationMiddleware(...)` 之前插入 `LeaseMiddleware(app.state 上的 device_lease_service, 与 PolicyMiddleware 相同的 ToolRegistry)` |
| `maa_api/db/repositories/pipeline.py` | `_next_pending_id(core_id, allowed_session_ids=None)`、`claim_next(core_id, *, core_epoch=None, allowed_session_ids: frozenset[str] \| None = None)`（§4.5.4） |
| `maa_api/services/pipeline_runner.py` | `bind_lease_gate(gate)`；`_claim_next` 的闸门与抢占（§4.5.4） |
| `maa_api/api/routers/pipelines.py`、`atomic.py`、`device.py`、`agent.py` | 人工抢占（§4.5.3）；`agent.py` 另改审计 |
| `maa_api/api/routers/device_lease.py`（新） | 两个端点（§4.3） |
| `maa_api/api/routers/tickets.py` | `TicketDetail` 加 `lease: DeviceLeaseOut \| None = None` |
| `maa_api/db/repositories/audit.py` | `AuditRepository.list` 加 `ticket_id: str \| None = None` 过滤 |
| `maa_api/agent/runs/recovery.py` | 第 4 步（§4.5.8） |
| `maa_api/domain/errors.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml` | §4.3、§4.4 |
| `maa_api/main.py` | 装配与关闭（§4.5.9） |
| `maa_api/agent/specs/builtin/general.md` | §4.7 |

### 4.3 接口

#### 端点（`maa_api/api/routers/device_lease.py`，`APIRouter(prefix="/api/agent/device-lease", tags=["agent"])`）

| 方法与路径 | 函数名 | 响应 | 错误码 |
|---|---|---|---|
| GET `/api/agent/device-lease` | `get_device_lease` | 200 `DeviceLeaseState` | `AGENT_DISABLED`、`UNAUTHORIZED` |
| DELETE `/api/agent/device-lease` | `release_device_lease` | 204（`status_code=204, response_class=Response`）；调用 `preempt("manual_release")`，没有租约也是 204 | `AGENT_DISABLED`、`UNAUTHORIZED` |

两条都挂 `require_auth`；服务取不到时 `AppError(AGENT_DISABLED, "设备租约服务还在启动中，请稍后重试")`。路径写 `""`（与 `routers/confirmations.py` 的列表端点同写法）。

| 模型 | 字段 |
|---|---|
| `DeviceLeaseOut` | `device_id`、`lease_id`、`ticket_id: str \| None`、`ticket_title: str \| None`、`run_id: str \| None`、`holder_agent`、`purpose`、`explicit: bool`、`acquired_at`、`expires_at`、`heartbeat_at`（时间带 `Z`） |
| `DeviceLeaseState` | `held: bool`、`lease: DeviceLeaseOut \| None`、`waiters: int` |

`TicketDetail.lease`：当前租约的 `ticket_id` 等于该工单时填，否则为 `null`；由 `get_ticket` 路由调用 `device_lease_service.current()` 与 `wire()` 组装。

`GET /api/agent/audits` 加查询参数 `ticket_id`；`audit_wire` 加 `run_id`、`ticket_id`、`agent_name` 三个键。

#### origin

`{"ticket_id": "0f8e…", "ticket_title": "日常", "agent": "daily_ops", "run_id": "5c1d…"}`；REST 调用与 v2 门面不经代理运行的调用为 `null`。出现在：

| 位置 | 改动 |
|---|---|
| `GET /api/confirmations`、`GET /api/confirmations/{id}` | 每条确认加 `origin` |
| WS `confirm_request` | 事件字典加 `origin`，其余字段（confirmation_id、category、title、summary、caller、tool、params、expires_at）不变 |
| `GET /api/agent/questions`、`/{id}`；WS `question_request`、`question_resolved` | 提问 wire 加 `origin` |
| SSE `confirmation_required` | 加 `origin`，取 `confirmation_service.get(confirmation_id)["origin"]` |
| SSE `question_required` | 加 `origin`，取 `question_service.create` 返回的 wire |

#### WS `agent_event` 的 `lease_changed`

```json
{"session_id": null, "event": "lease_changed",
 "payload": {"change": "preempted", "reason": "manual_pipeline", "held": false,
             "lease": {"device_id": "default", "lease_id": "…", "ticket_id": "0f8e…", "ticket_title": "日常",
                       "run_id": "…", "holder_agent": "daily_ops", "purpose": "click", "explicit": false,
                       "acquired_at": "…Z", "expires_at": "…Z", "heartbeat_at": "…Z"}}}
```

`change` 取 `acquired`、`released`、`preempted`、`expired`；`released` 与 `expired` 的 `reason` 是 `ReleaseReason`，`preempted` 的是 `PreemptReason`，`acquired` 为 `null`；续期不广播。`lease` 是变化前后有意义的那份：`acquired` 为新租约，其余为被移除的租约。

#### 错误码（[04 §5](../04-接口与事件协议.md)）

| 码 | 状态码 | `ErrorCode` 上一行的注释 | 抛出处与 message |
|---|---|---|---|
| `DEVICE_LEASE_BUSY` | 409 | 设备正被其他工单使用 | `acquire` 等待超时：「设备正被其他工单使用，稍后再试」，details `{holder_ticket_id, holder_agent, expires_at, waited_seconds}` |
| `DEVICE_LEASE_PREEMPTED` | 409 | 设备已被用户接管 | 持有者被抢占后第一次取租约：「设备已被用户接管，先停下设备操作」，details `{reason}` |
| `DEVICE_LEASE_NOT_HELD` | 409 | 没有持有设备租约 | `release_device` 没有可释放的租约：「当前没有占用设备」 |

### 4.4 配置项

| 键 | 类型 | 默认 | 范围 | `_FIELDS` |
|---|---|---|---|---|
| `lease.ttl_seconds` | int | 600 | 60–3600 | `dict(label="设备租约有效期（秒）", group="Agent", type="integer", minimum=60, maximum=3600, hot_action="none")` |
| `lease.wait_seconds` | int | 30 | 0–600 | `dict(label="设备租约排队等待（秒）", group="Agent", type="integer", minimum=0, maximum=600, hot_action="none")` |

`settings.py`：新类 `LeaseSettings(BaseModel)`（`model_config = ConfigDict(extra="ignore")`，两个 `Field(default=..., ge=..., le=...)`），`Settings` 加 `lease: LeaseSettings = Field(default_factory=LeaseSettings)`，`SETTING_KEYS` 加两项。都不是 config-only。服务每次用到时现读 `settings_provider()`，改完立即生效。`config.template.yaml` 在 `agent:` 段之后加：

```yaml
# 设备租约：代理写设备前要先拿到租约，同一时刻只有一个工单能用设备
lease:
  # 租约有效期（秒，60-3600）；持有者操作设备时自动续期，到期自动释放
  ttl_seconds: 600
  # 设备被占用时排队等待的时长（秒，0-600）；等不到就报设备忙
  wait_seconds: 30
```

### 4.5 行为与算法

#### 4.5.1 取租约

```
async def acquire(holder, *, purpose, explicit=False, wait_seconds=None):
    if holder.key in self._preempted:                      # 被抢占后的第一次：一次性
        reason = self._preempted.pop(holder.key)
        raise AppError(DEVICE_LEASE_PREEMPTED, ..., {"reason": reason})
    settings = self.settings_provider().lease
    wait = settings.wait_seconds if wait_seconds is None else wait_seconds
    deadline = loop.time() + wait
    token = object()
    async with self._cond:                                 # asyncio.Condition
        self._queue.append(token)                          # collections.deque，先到先得
        try:
            while True:
                row = await self._load_fresh()             # 读行；已过期的删掉并广播 expired
                if row is not None and holder.owns(row):   # 同一工单（含子代理）重入：续期
                    return await self._extend(row, settings.ttl_seconds, explicit=explicit, purpose=purpose)
                if row is None and self._queue[0] is token:
                    inserted = await self._try_insert(holder, purpose, explicit, settings.ttl_seconds)
                    if inserted is not None:               # IntegrityError 时返回 None，继续等
                        broadcast("acquired"); return inserted
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise AppError(DEVICE_LEASE_BUSY, ..., {...})
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=min(remaining, self.poll_seconds))
                except TimeoutError:
                    pass                                   # 定时重读，过期的行能被发现
        finally:
            self._queue.remove(token)
            self._cond.notify_all()
```

- 新行：`device_id=LEASE_DEVICE_ID`、`lease_id=new_uuid()`、`ticket_id`、`run_id=holder.run_id`、`holder_agent=holder.agent`、`purpose=purpose[:120]`、`explicit`、`acquired_at=heartbeat_at=now`、`expires_at=now+ttl`、`boot_id`。
- `_extend`：`expires_at=now+ttl`、`heartbeat_at=now`、`explicit = row.explicit or explicit`；`explicit=True` 时用新的 `purpose`。
- 所有数据库操作都在 `self._cond` 的锁里完成，并各自开会话、提交。

#### 4.5.2 续期、释放与过期

- `renew(holder)`：是持有者就做 `_extend`（不改 explicit 与 purpose），不广播。
- `release(holder)`：是持有者就删行，广播 `released`（reason `released`）。`release_run(run_id)`：行的 `run_id == run_id` 且 `explicit=false` 才删（reason 用参数）。`release_ticket(ticket_id)`：行的 `ticket_id` 相同就删（reason `ticket_ended`），不论显式隐式。
- 每次删行之后：`self._cond.notify_all()`（在锁内）、调用 `on_released()`（`PipelineRunner.wake`，锁外，异常只记日志）。
- `expire_due()`：删 `list_expired(now)` 的行（reason `expired`）与 `list_orphaned_implicit()` 的行（reason `orphaned`），返回删除数。`start()` 起的后台任务每 `scan_interval_seconds` 秒调用一次，写法照 `ConfirmationService._expiry_loop`。
- `on_ticket_ended(event)`：`release_ticket(event.ticket_id)`，再 `clear_preempted(f"ticket:{event.ticket_id}")`。

#### 4.5.3 人工抢占

```
async def preempt(reason, *, detail=None):
    async with self._cond:
        row = await self._load_fresh()
        if row is None: return None
        删行并提交
        self._preempted[key_of(row)] = reason             # key_of：有工单 ticket:<id>，否则 run:<run_id>
        self._cond.notify_all()
    on_released()；broadcast("preempted", reason, row)
    if row.ticket_id and self.thread_router is not None:
        await self.thread_router.publish(f"ticket:{row.ticket_id}", kind=InboxKind.SYSTEM, ref_id=row.lease_id,
            payload={"event": "lease_preempted", "reason": reason, "detail": detail,
                     "text": f"设备被用户接管：{PREEMPT_TEXT[reason]}。不要再操作设备；需要继续时用 report 汇报 waiting，等用户回复。"})
    return row
```

调用点（都在执行人工操作之前调用；服务取不到就跳过；`preempt` 的异常只记日志，不影响人工操作本身）：

| 路由 | 条件 | reason |
|---|---|---|
| `routers/pipelines.py` 的 `submit_pipeline`、`retry_pipeline` | `queue_service.submit` 返回且 `reused` 为假之后 | `manual_pipeline` |
| `routers/atomic.py` 的 `click_device`、`back_to_home`；`routers/device.py` 的 `_run_atomic` | 进入 `async with runner.operation_lock` 之前 | `rest_atomic` |
| `routers/pipelines.py` 的 `cancel_pipeline` | 先读该流水线，状态为 `running` 时，在 `request_cancel` 之后 | `stop_pipeline` |
| `routers/agent.py` 的 `invoke_tool`（两处 `service.invoke` 都传） | 工具定义 `device == "exclusive"` 时传 `on_before_execute`，即确认通过之后、执行之前；`registry.get(name)` 抛 `AppError` 时不传钩子，让 `invoke` 照旧报错 | `rest_tool` |
| `DELETE /api/agent/device-lease` | 总是 | `manual_release` |

这几个路由需要 `request: Request` 的就加上；`cancel_pipeline` 另加 `session: AsyncSession = Depends(get_session)` 读流水线。新的模块级 helper `async def preempt_device(request, reason, detail=None)` 放在 `maa_api/api/routers/device_lease.py`，其余路由 import 它。

#### 4.5.4 队列领取闸门

`PipelineRepository._next_pending_id(core_id, allowed_session_ids=None)`：`allowed_session_ids` 不为 None 时在现有条件上加 `or_(Pipeline.source == PipelineSource.MANUAL, Pipeline.agent_session_id.in_(allowed_session_ids))`（集合为空时只剩手动提交）；`claim_next` 把参数透传，条件更新部分不变。

`PipelineRunner`：

- `bind_lease_gate(gate)` 存进 `self._lease_gate`（`__init__` 里初始化为 None，写法照 `bind_finish_hook`）。gate 需要两个协程方法 `claim_gate()` 与 `preempt(reason, detail=...)`，`DeviceLeaseService` 正好满足。
- `_claim_next` 在 `async with self.operation_lock:` 里，紧接在 `queue_service.paused` 的判断之后、读 `generation` 与设置 `self._claiming_core_epoch` 之前：`self._lease_gate` 不为 None 时 `gate = await self._lease_gate.claim_gate()`；它抛异常时记日志并 `return None, []`（本轮不领，下一轮再试）。放在这个位置是因为其后的 `try/finally` 负责清理 `_claiming_core_epoch`，提前返回不能越过它。
- `repo.claim_next(..., allowed_session_ids=gate.allowed_session_ids if gate else None)`。
- 领到的流水线 `source == manual` 且 `gate` 不为空时，在 `claim_completed = True` 之后、`return` 之前调用 `await self._preempt_for_manual(pipeline.id)`；该方法内部 `try/except Exception` 只记日志，不让异常进入领取失败的释放分支。

`claim_gate()`：没有租约返回 None；有工单的租约 `allowed = session_ids_for_ticket(ticket_id)`（Worker 与子代理会话）；没有工单的租约 `allowed = {session_id_for_run(run_id)}` 去掉 None。租约释放、过期、抢占都会调 `on_released()` 唤醒队列。

#### 4.5.5 LeaseMiddleware 与执行前钩子

`ctx.state["policy.before_execute"]` 是 `dict[str, list[BeforeExecuteCallback]]`，键是工具调用 id。约定：外层中间件往里追加回调，`PolicyMiddleware` 调 `invoke` 时把该调用的列表组合成一个 `on_before_execute`（按追加顺序逐个 `await`），调用结束后 `pop` 掉。

`LeaseMiddleware.wrap_tool`：

1. `definition = registry.get(call.name)`；未知工具或 `definition.device != "exclusive"` 时直接 `return await call_next(call)`。
2. `holder = holder_for(ctx)`；追加回调 `acquire_hook(audit_id)`：服务为 None 时抛 `AppError(DEVICE_LEASE_BUSY, ..., {"reason": "lease_service_unavailable"})`，否则 `await service.acquire(holder, purpose=call.name)`。
3. 起续期任务：每 `max(ttl_seconds / 3, 5)` 秒 `await service.renew(holder)`，异常只记日志。
4. `try: outcome = await call_next(call) finally:` 取消续期任务、`pop` 掉本调用的钩子列表。
5. `outcome.error` 的 `code == "DEVICE_LEASE_PREEMPTED"` 时返回 `dataclasses.replace(outcome, stop_run=True, stop_reason="lease_preempted")`；其余原样返回。

`on_run_end`：只在根运行（`ctx.run_id == ctx.root_run_id`）上做 `release_run(ctx.root_run_id, reason="run_ended")` 与 `clear_preempted(holder_for(ctx).key)`；子代理运行结束不释放。

#### 4.5.6 冒泡

`PolicyMiddleware` 在 `invoke(mode="async")` 返回 `status == "awaiting_confirmation"` 的分支里：

1. `detail = await confirmation_service.get(confirmation_id)`；发 SSE `confirmation_required` 时带 `origin=detail["origin"]`。
2. `await bubble_human_request(state, origin, root_run_id=ctx.root_run_id, kind="confirmation", ref_id=confirmation_id, prompt=detail["reason"] or "该操作需要人工确认")`。
3. 等待终态的循环包进 `try/finally`，`finally` 里 `await settle_human_request(state, origin, ref_id=confirmation_id)`。

`ClarificationMiddleware`：`create(..., origin=origin)`；SSE `question_required` 带 `origin=wire["origin"]`；`bubble_human_request(..., kind="question", ref_id=question_id, prompt=wire["question"])`；`wait` 包进 `try/finally` 调 `settle_human_request`。

`bubble_human_request` 的步骤：

1. `device_lease_service.release_run(root_run_id, reason="waiting_human")`：等人时让出隐式租约（[09 §3.2](../09-安全与治理.md)）。
2. origin 没有工单就到此为止。
3. `ticket = await ticket_service.await_human(ticket_id, HumanAction(type=kind, prompt=prompt[:200], ref_id=ref_id))`；返回 None 就到此为止。
4. `await thread_router.to_orchestrator(kind=InboxKind.SYSTEM, thread_key=f"ticket:{ticket.id}", ref_id=ref_id, payload={"event": "human_request", "type": kind, "ticket_id": ticket.id, "ticket_title": ticket.title, "agent": origin.agent, "ref_id": ref_id, "prompt": prompt[:200]}, preferred_session_id=ticket.orchestrator_session_id)`。

#### 4.5.7 origin 与子代理的策略身份

`PolicyMiddleware` 构造 `ToolContext` 时：`origin=ToolOrigin(run_id=ctx.run_id, ticket_id=ctx.ticket_id, agent=ctx.spec.name)`；`session_id` 取「策略会话」：`ctx.spec.kind == "subagent"` 时在 `on_run_start` 里读一次子代理会话的 `parent_session_id` 存进 `ctx.state["policy.session_id"]`，其余代理就是 `ctx.session_id`。因此子代理的审计、原子操作授权、作业授权与提交的流水线都挂在父 Worker 会话上，审计的 `run_id`、`agent_name` 记的是子代理自己。`ClarificationMiddleware` 的 origin 同样构造。

#### 4.5.8 恢复与工单结束

- `RunRecovery.recover()` 在 M69 的第 3 步之后加第 4 步：同一事务里 `await DeviceLeaseRepository(session).delete_other_boots(current_boot_id)`，删除数写进恢复日志。
- 工单结束（M69 的结束监听者）：`device_lease_service.on_ticket_ended`；以及撤销 Worker 会话的原子操作授权（前置检查 13 有输出时不加）：

```python
async def revoke_grant_on_ticket_end(event: TicketEnded) -> None:
    if event.worker_session_id:
        await confirmation_service.revoke_session_grant(event.worker_session_id, resolved_by="system")
```

#### 4.5.9 装配（`maa_api/main.py`）

在 `app.state.ticket_service = ...`（M69）之后、`app.state.worker_service = ...`（M70）之前：

```python
from maa_api.agent.lease import DeviceLeaseService
device_lease_service = DeviceLeaseService(
    db_session.session_factory,
    boot_id=app.state.boot_id,
    thread_router=thread_router,
    broadcast=lambda message_type, data: ws_manager.broadcast(message_type, data),
    on_released=pipeline_runner.wake,
)
app.state.device_lease_service = device_lease_service
await device_lease_service.start()
pipeline_runner.bind_lease_gate(device_lease_service)
ticket_service.add_end_listener(device_lease_service.on_ticket_ended)
ticket_service.add_end_listener(revoke_grant_on_ticket_end)   # 本函数定义在 lifespan 里，闭包引用 confirmation_service
```

`device_lease_service` 照 `question_service` 的写法在 lifespan 开头初始化为 None，两条关闭路径（启动失败的 `except BaseException` 与 `yield` 之后的 `finally`）在关闭 `confirmation_service` 之前各加 `if device_lease_service is not None: await device_lease_service.close()`；属性名加进启动失败时清理 `app.state` 的元组。

#### 4.5.10 边界情况

| 输入 | 期望 |
|---|---|
| 同一工单的子代理在父 Worker 持有隐式租约时调写设备工具 | 重入，续期，不排队 |
| 工单 A 持有，工单 B 取租约，`wait_seconds=0` | 立刻 `DEVICE_LEASE_BUSY`，details 带 A 的工单与代理 |
| A、B、C 先后排队 | 按 A→B→C 的顺序拿到；每次只有一行 |
| 租约过期时有人在排队 | 排队者在下一次重读（不超过 `poll_seconds`）时拿到，广播 `expired` 再 `acquired` |
| 确认还在等批准 | 不调用钩子，不占租约；批准后才取 |
| 确认被拒或过期 | 钩子不被调用，什么都不占 |
| 取租约时运行被中断 | `CancelledError` 走 `_execute_and_record` 的中断分支，审计 `AGENT_TURN_INTERRUPTED`，排队记号被移除 |
| 抢占后持有者第一次取租约 | `DEVICE_LEASE_PREEMPTED`，本轮以 `lease_preempted` 结束；下一轮可以重新取 |
| 抢占时持有者没有在跑的运行（显式租约） | 记号保留到它下一轮的第一次取租约或该轮结束 |
| 没有租约时人工操作 | `preempt` 返回 None，不广播，不写收件箱 |
| 有租约时领到手动提交 | 先抢占再执行；持有者工单的流水线照常可领；定时任务等待 |
| 闸门读租约出错 | 本轮不领，下一轮重试 |
| `release_device` 没有持有 | `DEVICE_LEASE_NOT_HELD` |
| `hold_device` 被总控或无工单的运行调用 | `INVALID_PARAMETER`（总控工具集里本来就没有它） |
| 工单结束时持有显式租约 | 删行，广播 `released`（reason `ticket_ended`），队列被唤醒 |
| 重启后库里有上一进程的租约 | 第 4 步删除，`GET /api/agent/device-lease` 返回 `held=false` |
| REST 调 `click` 工具（需逐次确认）时有代理持有租约 | 等确认期间不动租约；批准后、执行前抢占 |

### 4.6 前端

- `web/src/api/keys.ts` 的 `agent` 下加 `deviceLease: () => [...root, 'agent', 'device-lease'] as const`；前置检查 18 没有 `specs:` 时再加 `specs: () => [...root, 'agent', 'specs'] as const`，已有就复用。
- `web/src/realtime/query-bridge.ts` 的 `agent_event` 分支加：`event.data.event === 'lease_changed'` 时失效 `keys.agent.deviceLease()`，`payload.lease.ticket_id` 是字符串时再失效 `keys.agent.ticket(ticketId)`。
- `web/src/features/agent/types.ts`：加 `export interface AgentOrigin { ticket_id: string | null; ticket_title: string | null; agent: string | null; run_id: string | null }`；`AgentQuestionPrompt` 加 `origin?: AgentOrigin | null`。
- `web/src/features/agent/origin.ts`（新）：
  ```ts
  export const agentSpecsQuery = queryOptions({ queryKey: keys.agent.specs(), queryFn: /* api.GET('/api/agent/specs')，错误 throw toApiError */, staleTime: Infinity, retry: false })
  export function useAgentTitle(enabled: boolean): (name: string | null | undefined) => string
  // 读不到或没有这个名字时返回原名；name 为空返回「代理」
  export function originLabel(origin: AgentOrigin | null | undefined, title: (name: string | null | undefined) => string): string | null
  // 有工单：来自工单「{ticket_title ?? '未命名工单'}」（{title(agent)}）；只有代理：来自{title(agent)}；origin 为空：null
  ```
- `ConfirmationsPanel.tsx`：`Confirmation` 加 `origin?: AgentOrigin | null`；在 `const pending = useQuery(...)` 之后、任何提前 `return` 之前调用 `const agentTitle = useAgentTitle(Boolean(pending.data?.items.some((item) => item.origin)))`；「调用方」那一行的 `<p>` 元素与样式不变，内容改为：`originLabel(item.origin, agentTitle)` 有值时显示它，否则仍显示原来的「调用方：」加 `callerLabel(item)`。
- `QuestionCard.tsx`：`const agentTitle = useAgentTitle(Boolean(question.origin))`；标题区 `<p id={questionId}>` 之后，`originLabel` 有值时加 `<p className="text-footnote text-muted-foreground">{label}</p>`。
- 界面文案只有「来自工单「X」（代理名）」「来自代理名」两种，不加句号。

### 4.7 提示词与代理定义

`maa_api/agent/specs/builtin/general.md`：frontmatter 的 `tools` 末尾加 `hold_device`、`release_device`；`version` 加 1；正文末尾加一段：

```markdown
需要连续操作设备（先导航再提交、连续几次点击）时，先调 hold_device 写明用途，做完调 release_device；只点一次或回主界面不用占用。工具结果是 DEVICE_LEASE_BUSY 时，用 report 汇报 waiting，说明在等设备。收到【系统】说设备被用户接管时，停止所有设备操作，用 report 汇报 waiting，等用户回复再继续。
```

## 5. 实施任务

### T1 错误码与配置

- 写范围：`maa_api/domain/errors.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`（§4）、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`
- 步骤：§4.3 的三个错误码（追加到 M69 加 `TICKET_ENDED` 的那张表，总数句 +3，`ALL_CODE_COUNT`、`HTTP_BOUND_CODE_COUNT` 各 +3）；§4.4 的两个配置键；`ENV_OVERRIDES` 整表加 `"lease.ttl_seconds": "MAA_LEASE_TTL_SECONDS"`、`"lease.wait_seconds": "MAA_LEASE_WAIT_SECONDS"`。
- 验证：`.venv/bin/python -m pytest -q tests/domain tests/test_settings.py tests/test_vapid_settings.py > /tmp/m71-t1.log 2>&1; echo $?` → 0。
- 完成判据：设置接口能读写两个新键。

### T2 模型与迁移 0027

- 写范围：`maa_api/db/models.py`、`maa_api/db/migrations/versions/0027_device_lease.py`、`tests/db/test_models.py`、`tests/db/test_device_lease_migration.py`（新）
- 步骤：§4.1。`test_models.py`：`BUSINESS_TABLES` 加 `device_lease`；`EXPECTED_INDEXES` 加 `ix_agent_audit_ticket_id_created_at`；`EXPECTED_UNIQUE_CONSTRAINTS` 加 `uq_device_lease_lease_id`；`EXPECTED_FK_SET_NULL` 加 `(device_lease, ticket_id)`、`(device_lease, run_id)`、`(confirmation, origin_run_id)`、`(confirmation, origin_ticket_id)`、`(agent_question, origin_run_id)`、`(agent_question, origin_ticket_id)`、`(agent_audit, run_id)`、`(agent_audit, ticket_id)`。
- 验证：`.venv/bin/python -m pytest -q tests/db > /tmp/m71-t2.log 2>&1; echo $?; tail -n 5 /tmp/m71-t2.log` → 0（含离线渲染与降级到底）。
- 完成判据：`agent_audit` 的索引加唯一约束合计 3 条，预算测试通过。

### T3 origin、审计与确认来源

- 写范围：`maa_api/agent/registry.py`、`maa_api/agent/origin.py`（新）、`maa_api/agent/confirmation.py`（§4.2 第 4–7 条）、`maa_api/agent/questions.py`、`maa_api/db/repositories/audit.py`、`maa_api/api/routers/agent.py`（只改 `audit_wire` 与 `list_audits`）、`tests/agent/test_confirmation_origin.py`（新）、`tests/api/test_agent_router.py`
- 步骤：§4.2 对应小节；`origin.py` 的两个冒泡函数在本任务一并写好（T7 才接进中间件）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_confirmation_service.py tests/agent/test_question_service.py tests/agent/test_confirmation_origin.py tests/api/test_agent_router.py tests/api/test_questions_router.py > /tmp/m71-t3.log 2>&1; echo $?` → 0。
- 完成判据：v2 的确认与提问测试原样通过；带 origin 与不带 origin 两种调用的落库与 wire 都正确。

### T4 DeviceLeaseService

- 写范围：`maa_api/db/repositories/lease.py`（新）、`maa_api/agent/lease.py`（新，本任务只写服务与数据类）、`tests/agent/lease/__init__.py`、`tests/agent/lease/conftest.py`、`tests/agent/lease/test_device_lease_service.py`（均新）
- 步骤：§4.2、§4.5.1–§4.5.3。`conftest.py` 用 M69 的 `tests/fakes/v3_db.py` 建库，提供 fixture `lease_env`：返回 `(factory, service, events, published)`，`service` 注入可控时钟 `clock`、`poll_seconds=0.02`、记录广播的 `broadcast` 与记录发布的假 `thread_router`；并造好两个工单（各带 Worker 会话与根运行）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/lease > /tmp/m71-t4.log 2>&1; echo $?` → 0。
- 完成判据：§6 中 `test_device_lease_service.py` 的用例全过；串行化用例连续跑 20 次都过。

### T5 执行前回调与 LeaseMiddleware

- 写范围：`maa_api/agent/confirmation.py`（§4.2 第 1–3 条）、`maa_api/agent/lease.py`（`LeaseMiddleware`、`holder_for`）、`maa_api/agent/harness/builtin_middleware.py`（只改 `PolicyMiddleware` 组合钩子与链的构造处）、`tests/agent/test_confirmation_before_execute.py`（新）、`tests/agent/lease/test_lease_middleware.py`（新）
- 步骤：§4.5.5。
- 验证：`.venv/bin/python -m pytest -q tests/agent > /tmp/m71-t5.log 2>&1; echo $?` → 0。
- 完成判据：「等确认期间不占租约」用例通过；v2 的 `tests/agent/` 全部通过。

### T6 hold_device、release_device 与通用 Worker

- 写范围：`maa_api/agent/tools/lease.py`（新）、`maa_api/agent/tools/__init__.py`、`tests/agent/test_tool_catalog.py`、`tests/agent/lease/test_lease_tools.py`（新）、`maa_api/agent/specs/builtin/general.md`
- 步骤：§4.2 工具小节；`EXPECTED_TOOLS` 加两个名字，工具总数与模型可见数各 +2；§4.7。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_tool_catalog.py tests/agent/lease > /tmp/m71-t6.log 2>&1; echo $?` → 0；启动一次 `create_app()` 的 lifespan（T11 的装配用例）确认代理定义加载不报错。
- 完成判据：REST 工具目录里看不到两个新工具，调用返回 `TOOL_NOT_FOUND`。

### T7 冒泡与子代理策略身份

- 写范围：`maa_api/agent/harness/builtin_middleware.py`（`PolicyMiddleware`、`ClarificationMiddleware`）、`tests/agent/harness/test_policy_origin_bubbling.py`（新）、`tests/agent/lease/test_origin_bubbling.py`（新）
- 步骤：
  1. `PolicyMiddleware` 构造 `ToolContext` 时写 `origin`（§4.5.7）。
  2. 待确认分支：SSE 带 origin，冒泡，`finally` 收尾（§4.5.6）。
  3. `ClarificationMiddleware`：同上。
  4. 子代理策略身份（§4.5.7）；前置检查 12 有输出时跳过本步。
- 验证：`.venv/bin/python -m pytest -q tests/agent > /tmp/m71-t7.log 2>&1; echo $?` → 0。
- 完成判据：Worker 发起确认后工单为 `waiting`、总控收件箱有 `human_request` 项；批准后工单回到 `open`。

### T8 队列领取闸门

- 写范围：`maa_api/db/repositories/pipeline.py`、`maa_api/services/pipeline_runner.py`、`tests/db/test_repositories_pipeline.py`、`tests/services/test_pipeline_runner.py`
- 步骤：§4.5.4。
- 验证：`.venv/bin/python -m pytest -q tests/db/test_repositories_pipeline.py tests/services/test_pipeline_runner.py tests/core > /tmp/m71-t8.log 2>&1; echo $?` → 0。
- 完成判据：没有绑定闸门时行为与 v2 完全一致（既有用例不改一行）。

### T9 人工抢占与端点

- 写范围：`maa_api/api/routers/device_lease.py`（新）、`pipelines.py`、`atomic.py`、`device.py`、`agent.py`（`invoke_tool`）、`tickets.py`、`maa_api/main.py`（只改 import 与 `include_router`）、`tests/api/test_device_lease_router.py`（新）、`tests/api/test_lease_preemption_routes.py`（新）、`tests/api/test_tickets_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`（§6）
- 步骤：§4.3、§4.5.3；`dump_openapi.py` 与 `pnpm gen:api`；清单 agent 小节加两行，`GET /api/agent/audits` 那行的请求要点补 `ticket_id`，小节计数 +2，总数句 +2。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/api > /tmp/m71-t9.log 2>&1; echo $?; tail -n 5 /tmp/m71-t9.log
  .venv/bin/python scripts/dump_openapi.py --check; echo $?
  cd web && pnpm gen:api:check; echo $?; cd ..
  ```
  三个退出码都是 0。
- 完成判据：五类人工操作都抢占；没有租约时它们的行为与 v2 一致。

### T10 前端

- 写范围：`web/src/api/keys.ts`、`web/src/realtime/query-bridge.ts`、`web/src/realtime/query-bridge.test.ts`、`web/src/features/agent/types.ts`、`web/src/features/agent/origin.ts`（新）、`web/src/features/confirmations/ConfirmationsPanel.tsx`、`web/src/features/confirmations/confirmations.test.tsx`、`web/src/features/agent/QuestionCard.tsx`、`web/src/features/agent/QuestionCard.test.tsx`
- 步骤：§4.6。
- 验证：`cd web && pnpm typecheck > /tmp/m71-tc.log 2>&1; echo $?; pnpm test --run > /tmp/m71-vitest.log 2>&1; echo $?; cd ..` → 两个 0。
- 完成判据：没有 origin 的确认卡片渲染与之前完全一样，也不多发 `/api/agent/specs` 请求。

### T11 恢复、装配与收尾

- 写范围：`maa_api/agent/runs/recovery.py`、`maa_api/main.py`（lifespan）、`tests/agent/tickets/test_ticket_recovery.py`、`tests/api/test_agent_assembly_wiring.py`、`docs/v3/ledgers/m71-device-lease-bubbling.md`（新）
- 步骤：§4.5.8、§4.5.9；跑 §7；写台账；`git merge --no-ff` 回 `dev`，打 tag `v3-m71`。
- 验证：§7。
- 完成判据：[12 §4](../12-实施路线与里程碑.md) 的十项都满足。

## 6. 测试清单

异步用例一律「同步测试函数 + `asyncio.run(scenario())`」；需要时钟的用例给服务注入 `clock`，不 `sleep` 真实的租约时长。

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_device_lease_migration.py` | `test_device_lease_migration_adds_table_and_origin_columns` | 在 0026 上造一条 confirmation、agent_question、agent_audit 后升级：`device_lease` 列与 §4.1 一致；旧行新列为 NULL；八个外键 ondelete 为 SET NULL；第二行同 `device_id` 违反主键 |
| 同上 | `test_device_lease_migration_downgrade_restores_0026` | 降级后表与列都不在，旧行完好；再升级通过 |
| `tests/agent/lease/test_device_lease_service.py` | `test_acquire_inserts_single_row_and_broadcasts` | 行字段与 §4.5.1 一致，`boot_id` 为注入值，广播一次 `acquired` |
| 同上 | `test_two_holders_are_serialized` | A 持有时 B 的 `acquire`（`wait_seconds=5`）挂起；A `release` 后 B 拿到；任何时刻表里最多一行；循环 20 次 |
| 同上 | `test_waiters_are_served_first_come_first_served` | A 持有时 B、C 先后排队，A 释放后 B 先拿到，B 释放后 C 拿到；`waiters` 依次为 2、1、0 |
| 同上 | `test_busy_after_wait_seconds` | `wait_seconds=0` 立即 `DEVICE_LEASE_BUSY`，details 含 A 的 `holder_ticket_id`、`holder_agent` |
| 同上 | `test_same_ticket_is_reentrant_and_renews` | 同一工单的第二个 holder（子代理，同 ticket）直接拿到，`expires_at` 后移，行数仍是 1 |
| 同上 | `test_explicit_survives_run_end_implicit_does_not` | `release_run` 删隐式不删显式；`release_ticket` 两种都删 |
| 同上 | `test_release_requires_holder` | 非持有者 `release` 返回 False 且行还在 |
| 同上 | `test_lease_expires_and_waiter_takes_over` | 时钟推过 `expires_at` 后排队者拿到；`expire_due` 删除过期行并广播 `expired` |
| 同上 | `test_orphaned_implicit_lease_released_by_scan` | 根运行改成 `succeeded` 后 `expire_due` 删掉它的隐式租约（reason `orphaned`） |
| 同上 | `test_preempt_marks_holder_once` | 抢占后行被删、广播 `preempted`、假 `thread_router` 收到 `ticket:<id>` 的 system 项（payload.event 为 `lease_preempted`）；持有者第一次 `acquire` 得 `DEVICE_LEASE_PREEMPTED`，第二次正常拿到 |
| 同上 | `test_preempt_mark_cleared_at_run_end`、`test_ticket_end_releases_lease` | `clear_preempted` 之后第一次 `acquire` 正常；`on_ticket_ended` 删该工单租约并清记号 |
| 同上 | `test_claim_gate_lists_ticket_sessions` | 工单的 Worker 与子代理会话都在 `allowed_session_ids` 里；没有租约返回 None |
| `tests/agent/test_confirmation_before_execute.py` | `test_waiting_for_confirmation_does_not_hold_lease` | 注册一个 `device="exclusive"`、`DANGEROUS` 的假工具，`invoke(mode="async", on_before_execute=取租约)`：待确认期间 `current()` 为 None，另一个工单能取得并释放；批准后 handler 执行时租约属于本工单；`release_run` 后为 None |
| 同上 | `test_before_execute_error_fails_audit_without_running_handler` | 钩子抛 `DEVICE_LEASE_BUSY`：handler 没被调用，审计 `failed` 且 `error_code=DEVICE_LEASE_BUSY` |
| 同上 | `test_before_execute_runs_for_unconfirmed_calls_too`、`test_execution_context_keeps_origin` | 免确认的同步调用也先过钩子；handler 收到的 `ctx.origin` 与传入相同 |
| `tests/agent/lease/test_lease_middleware.py` | `test_exclusive_tool_registers_before_execute_hook`、`test_non_exclusive_tool_passes_through` | 假 `call_next` 能在 `ctx.state["policy.before_execute"][call.id]` 里拿到钩子；`device="none"` 的工具没有钩子；调用结束后键被移除 |
| 同上 | `test_preempted_outcome_stops_run` | `call_next` 返回 `DEVICE_LEASE_PREEMPTED` 时结果 `stop_run=True`、`stop_reason="lease_preempted"` |
| 同上 | `test_root_run_end_releases_implicit_lease` | 根运行 `on_run_end` 释放隐式租约；子代理运行的 `on_run_end` 不释放 |
| `tests/agent/lease/test_lease_tools.py` | `test_hold_device_takes_explicit_lease`、`test_release_device_without_lease_raises`、`test_hold_device_requires_ticket_origin` | 显式租约 `explicit=true`、purpose 为参数；没有持有时 `DEVICE_LEASE_NOT_HELD`；origin 为空或无工单 `INVALID_PARAMETER` |
| `tests/agent/test_confirmation_origin.py` | `test_confirmation_origin_persisted_and_on_wire` | 三列落库；WS `confirm_request` 与 `get`、`list` 的 `origin.ticket_title` 为工单标题；审计三列同值 |
| 同上 | `test_question_origin_persisted_and_on_wire`、`test_rest_calls_have_null_origin` | 提问三列、`ask_user` 审计三列、wire 的 origin；REST 调用 origin 为 null、审计三列为 NULL |
| `tests/agent/lease/test_origin_bubbling.py` | `test_bubble_sets_ticket_waiting_and_notifies_orchestrator` | 工单 `waiting`、`human_action` 的 ref_id 为确认 id；总控收件箱一条 system 项（payload.event 为 `human_request`） |
| 同上 | `test_settle_returns_ticket_to_open`、`test_bubble_releases_implicit_lease` | ref_id 相同才回到 `open`；冒泡前持有的隐式租约被释放，显式租约保留 |
| `tests/agent/harness/test_policy_origin_bubbling.py` | `test_policy_middleware_emits_origin_and_bubbles` | 照 `tests/agent/harness/` 里 M65 的 PolicyMiddleware 用例构造运行：SSE `confirmation_required` 带 origin；批准后工单回到 `open` |
| 同上 | `test_subagent_calls_use_worker_session_identity` | 子代理调用的审计 `session_id` 是 Worker 会话、`agent_name` 是子代理名；Worker 会话已有原子操作授权时子代理的 `click` 不再弹授权 |
| `tests/db/test_repositories_pipeline.py` | `test_claim_next_with_allowed_sessions` | 给定 allowed 集合时只领手动与集合内会话的流水线，优先级与入队顺序照旧；集合为空只领手动 |
| `tests/services/test_pipeline_runner.py` | `test_claim_gate_defers_scheduled_pipeline_while_lease_held` | 用 `runner_env`：定时任务（更早入队）与持有者工单的流水线都在排队，假闸门返回持有者会话时 `_claim_next` 领到工单的；闸门改为返回 None 后领到定时任务 |
| 同上 | `test_claim_gate_preempts_before_manual_pipeline`、`test_claim_gate_error_skips_round` | 领到手动提交时假闸门的 `preempt` 被调用一次（reason `manual_pipeline`）；`claim_gate` 抛异常时返回 `(None, [])`、流水线仍是 pending |
| `tests/api/test_device_lease_router.py` | `test_get_device_lease_shape`、`test_delete_device_lease_preempts_and_returns_204` | 无租约 `{held: false, lease: null, waiters: 0}`；有租约时字段齐全；DELETE 返回 204，之后 `held=false` |
| `tests/api/test_lease_preemption_routes.py` | `test_manual_submit_preempts_lease` | 只装 pipelines 路由的 app，`app.state` 放假 `queue_service`（`submit` 返回新流水线）、假 `pipeline_runner` 与真实租约服务：POST `/api/pipelines` 后租约被删，持有者下一次 `acquire` 得 `DEVICE_LEASE_PREEMPTED` |
| 同上 | `test_retry_and_rest_atomic_preempt_lease` | 重放与 `POST /api/device/click`（假 `device_manager`）都抢占 |
| 同上 | `test_cancel_running_pipeline_preempts_but_pending_does_not` | 停止 running 的抢占；取消 pending 的不抢占 |
| 同上 | `test_rest_tool_invoke_of_exclusive_tool_preempts_after_confirmation` | `POST /api/agent/tools/<假 exclusive 工具>/invoke`（mode async）返回 202 时租约还在；批准后执行前被抢占 |
| `tests/api/test_agent_router.py` | `test_list_audits_filters_by_ticket_and_returns_origin_columns` | `?ticket_id=` 只返回该工单；每项有 `run_id`、`ticket_id`、`agent_name` |
| `tests/api/test_tickets_router.py` | `test_ticket_detail_includes_current_lease` | 本工单持有时 `lease` 有值；别的工单持有时为 null |
| `tests/agent/tickets/test_ticket_recovery.py` | `test_recovery_deletes_leases_of_previous_boot` | 旧 `boot_id` 的租约行在 `recover()` 后不在，本进程的保留 |
| `tests/api/test_agent_assembly_wiring.py` | `test_lifespan_wires_device_lease_service` | `app.state.device_lease_service` 是 `DeviceLeaseService`；`pipeline_runner._lease_gate` 是它 |
| `tests/agent/test_tool_catalog.py` | 既有用例 | `EXPECTED_TOOLS` 含两个新工具，两个计数 +2 |
| `web/src/realtime/query-bridge.test.ts` | `lease_changed invalidates device lease and ticket detail` | 失效 `keys.agent.deviceLease()` 与 `keys.agent.ticket('t-1')` |
| `web/src/features/confirmations/confirmations.test.tsx` | `shows the ticket origin instead of the caller line`、`falls back to the agent name when specs cannot be read` | 假后端给 `/api/agent/specs` 返回 `daily_ops → 执行官` 时显示「来自工单「日常」（执行官）」；specs 404 时显示「来自工单「日常」（daily_ops）」；没有 origin 的卡片仍显示「调用方：内置 Agent」 |
| `web/src/features/agent/QuestionCard.test.tsx` | `labels a question raised by a ticket` | 带 origin 时显示来源一行；不带时没有这一行 |

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/m71-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m71-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m71-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m71-tc.log 2>&1 && pnpm test --run > /tmp/m71-vitest.log 2>&1 && pnpm build > /tmp/m71-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m71-fsmoke.log 2>&1; tail -n 1 /tmp/m71-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m71-asmoke.log 2>&1; tail -n 1 /tmp/m71-asmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m71-eval.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m71-cap.log 2>&1; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
```

期望：pytest 与 dev 起点相比不新增失败；openapi 检查 0；前端链路 0；两个冒烟末行 `SMOKE OK`；regression 退出码 0；capability 通过率不低于前置检查 19 记下的基线（`general.md` 改了提示词）；断链 `BROKEN 0`；`git diff --check` 0。

手动验收（依赖真机，只作补充证据，记进台账）：

1. 让通用 Worker 连续点击时，在网页上手动提交一条流水线：`GET /api/agent/device-lease` 变成 `held=false`；Worker 本轮以 `lease_preempted` 结束，工单收到「设备被用户接管」的系统项。
2. 让 Worker 提交带理智药的流水线：确认面板上的卡片显示「来自工单「X」（通用执行）」；确认等待期间 `GET /api/agent/device-lease` 为 `held=false`。

## 8. 风险与回滚

- 执行前回调改动了确认服务的核心路径：`tests/agent/test_confirmation_service.py` 等 v2 用例必须原样通过；钩子为 None 时代码路径与改动前一致。
- 闸门让定时任务在租约期间推迟：租约有 TTL，过期即恢复；显式租约跨运行时最长占用 `lease.ttl_seconds`。
- 子代理改用 Worker 会话作策略身份后，子代理提交的流水线 `agent_session_id` 变成 Worker 会话，闸门按工单判断，不受影响。
- 如果 v2 会话仍走 `runtime.py` 的旧路径（不经过 AgentLoop），它们的设备调用不受租约约束，只受 `operation_lock` 与「有流水线在跑就拒绝」两道 v2 约束；M72 起 v2 会话只读，这个窗口只有一个里程碑，记进台账。
- 回滚：`alembic downgrade 0026_tickets`（可逆，只删结构），再 `git revert -m 1 <merge>`。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：错误码 +3；agent 小节路由 +2、审计端点的请求要点；总数句。
- `web/openapi.json`、`web/src/types/api.d.ts` 重新生成；`config.template.yaml` 加 `lease` 段。
- 计数：业务表 +1；错误码 +3；HTTP 端点 +2；工具 +2（模型可见 +2）；配置键 +2。
- 05 §5.2 的「`on_prepare` 回调」改为 `on_before_execute`（§11）；有偏离时在 [13 §8](../13-决策记录.md) 补条目。

## 10. 停下来问

- 前置检查 10 没有输出：M70 的 `MandateMiddleware` 没按 09 §2.3 把 `grant_atomic_ops` 计为 consume。
- 前置检查 8 找不到 PolicyMiddleware 的待确认分支，或它不经过 `ConfirmationService.invoke(mode="async")`。
- `ToolOutcome` 不是 dataclass，或没有 `stop_run`、`stop_reason` 字段。
- 加了 `on_before_execute` 之后 v2 的确认、中断、幂等用例出现失败，且原因不是本里程碑的改动方式。
- 抢占某个 v2 路由需要改变它已有的响应形状或状态码。
- v2 已有与三个 `DEVICE_LEASE_*` 同义的错误码。

## 11. 需要补进契约的名字

| 名字 | 用途 | 建议补进 |
|---|---|---|
| `ConfirmationService.invoke(..., on_before_execute=)`、`BeforeExecuteCallback` | 确认通过后、执行前的回调；05 §5.2 写的 `on_prepare` 不存在，现有 `on_audit_prepare` 在执行之后调用，不能用于取租约 | 05 §5.2、02 §6.2 |
| `ctx.state["policy.before_execute"]` | 外层中间件向 `PolicyMiddleware` 登记执行前钩子的约定（M84 的执行前消耗复查也用它） | 05 §5.1 |
| `ToolOrigin`、`ToolContext.origin` | 工具调用的代理来源，写进审计、确认、提问 | 05 §2.1 |
| `maa_api/agent/origin.py`：`ticket_titles`、`origin_wire`、`bubble_human_request`、`settle_human_request` | origin 组装与冒泡 | 02 §4.1 |
| 收件箱 system 项 `payload.event`：`human_request`、`lease_preempted` | 总控与 Worker 读到的两类系统项 | 05 §2.4 |
| `DeviceLeaseService` 的方法与 `LeaseHolder`、`ClaimGate`、`LEASE_DEVICE_ID`、`PreemptReason`、`ReleaseReason`、`holder_for` | 租约服务接口 | 02 §2.2 |
| `maa_api/db/repositories/lease.py`：`DeviceLeaseRepository` | 租约表仓储 | 02 §4.1 |
| `PipelineRunner.bind_lease_gate`；`claim_next(..., allowed_session_ids=)` | 队列领取闸门 | 02 §3 第 5 条 |
| `preempt_device(request, reason, detail=None)`（`routers/device_lease.py`） | 路由层的抢占入口 | 02 §6.6 |
| 响应模型 `DeviceLeaseOut`、`DeviceLeaseState`；`TicketDetail.lease` 的形状 | 04 只写了 `{held, lease?, waiters}` | 04 §2.2 |
| `lease_changed` 的 payload 字段 `change`、`reason`、`held`、`lease` | 04 只给了事件名 | 04 §4.2 |
| `GET /api/agent/audits` 的 `ticket_id` 参数；`audit_wire` 的 `run_id`、`ticket_id`、`agent_name` | 用上新索引；审计页与工单页按工单看调用 | 04 §2.2 |
| `HoldDeviceParams.purpose`（1–120 字）；两个工具的返回形状 | 工具契约 | 05 §6.1 |
| 子代理策略身份取父 Worker 会话（`ctx.state["policy.session_id"]`） | 子代理复用 Worker 的授权与审计归属 | 05 §4.2、09 §2.4 |
| 前端 `keys.agent.deviceLease`、`keys.agent.specs`、`AgentOrigin`、`useAgentTitle`、`originLabel`、`agentSpecsQuery` | M72 复用 | 11 |
