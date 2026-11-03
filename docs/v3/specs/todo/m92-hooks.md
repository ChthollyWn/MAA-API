> 状态：TODO（先搁置，不要照此实施）
> 阶段：P5。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再决定是否升为可执行规格。
> 本文件正文是提前写好的草稿，执行者不要从这里开工。

# M92 Hooks（规则类动作）（规格）

> 阶段：P5　依赖：M91　分支：`hooks`　迁移：`0042_hooks`
> 本文是 M92 的唯一规格；实施偏离记在 [13 §8](../../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 `hooks` 分支，基点是 dev | `git status -sb && git merge-base --is-ancestor dev HEAD && echo ok` | `## hooks`、`ok`，工作区干净 |
| 2 | M91 已合并 | `git tag -l 'v3-m91'` | `v3-m91` |
| 3 | 当前迁移 head | `ls maa_api/db/migrations/versions/ \| rg '^00[0-9]{2}_' \| sort \| tail -n 1` | `0041_mcp_servers.py` |
| 4 | 中间件链的组装处 | `rg -n "InjectionGuardMiddleware\|LoopGuardMiddleware" maa_api/agent/harness/ maa_api/agent/guard/` | 有匹配；找到按 05 §5.2 顺序实例化中间件的那段代码，位置记进台账 |
| 5 | 策略中间件（M65） | `rg -n "class PolicyMiddleware" maa_api/agent/harness/builtin_middleware.py` | 一行；它的 `wrap_tool` 调 `ConfirmationService.invoke` |
| 6 | 委托上限中间件（M70） | `rg -n "class MandateMiddleware\|mandate.ceiling" maa_api/agent/workers/middleware.py` | 有匹配 |
| 7 | 工单结束监听（M69） | `rg -n "def add_end_listener\|class TicketEnded" maa_api/agent/tickets/service.py` | 两行 |
| 8 | 给总控写系统项（M70） | `rg -n "async def notify" maa_api/agent/orchestrator/service.py` | 一行，签名含 `passive`、`ref_id`、`ticket_id` |
| 9 | span 记录接口（M68） | `rg -n "def " maa_api/agent/trace/spans.py` | 记下「开始与结束一个 span」的方法名，T6 用它 |
| 10 | 审计的来源列（M71） | `rg -n "run_id\|ticket_id\|agent_name" maa_api/db/models.py \| rg -i audit` | 有匹配；记下 M71 填这三列的位置，T5 的拒绝审计照同样方式填 |
| 11 | `HOOK_*` 错误码还没有 | `rg -n "HOOK_" maa_api/domain/errors.py` | 无输出 |
| 12 | `submit_pipeline` 参数里任务名的字段 | `rg -n "class PipelineCreate" -A 50 maa_api/domain/task.py \| rg -n "tasks:"` | 一行 `tasks: list[TaskInput]`；任务对象用 `name` 区分类型（`Fight`、`Mall`） |
| 13 | 「更多」导航与面板组件 | `rg -n "secondaryNav" web/src/components/layout/nav.ts && rg -n "export function ResponsiveSheet" web/src/components/ui/sheet.tsx` | 都有匹配 |

## 1. 背景与目标

Hooks 让用户用规则收紧代理的行为，不必改提示词或代理定义。信任模型已在 [09 §10.2](../../09-安全与治理.md) 定下：只能收紧，不能放宽；拒绝发生在确认之前；出错按拒绝处理。

做完之后：

1. 用户在「更多 › Hooks」（`/more/hooks`）新建、修改、排序、启用或停用 Hook；规则存在 `agent_hook` 表里。
2. 五个事件点都能挂规则：`before_tool`、`after_tool`、`before_model`、`after_model`、`ticket_end`。
3. 匹配器支持工具名（fnmatch）、代理名、时间窗口、参数条件、结果条件、回复文字、工单终态。
4. `deny` 在确认之前拦下工具调用，模型收到 `HOOK_BLOCKED`，审计里有一条 `rejected` 记录；`require_confirmation` 让本来免确认的调用走 v2 确认链路（同一张确认卡、同样的超时与审计）；`annotate` 把一段提示附进下一次模型请求，或在工单结束时写给总控。
5. 「凌晨 2–7 点禁止刷图」「对 Mall 任务一律要求确认」两个例子能一键从示例新建，并有测试钉死行为。
6. `script` 动作只保留枚举，保存时返回 `HOOK_INVALID`；由 M93 实现。
7. `hooks.enabled=false` 时 `HookMiddleware` 直接放行，行为与 M91 一致。

## 2. 非目标

- `script` 动作的执行（M93）。
- 改工具参数、批准确认、跳过策略判定或上限检查、放大授权（09 §10.2 明确禁止）。
- 约束代理之外的入口：REST 工具调用、前端手动提交、定时任务到点提交都不经过 `HookMiddleware`，Hook 管不到它们。界面要写明这一点。
- 正则匹配（用 fnmatch 与固定的比较运算，避免回溯爆炸）。
- 新的 SSE 或 WebSocket 事件；Hook 的判定只落在工具结果、确认卡、审计、span 上。
- 单条查询端点（04 §2.5 只有列表、新建、修改、删除）。

## 3. 相关文档

- [04 §2.5、§5、§6](../../04-接口与事件协议.md)：M92 的四个端点、`HOOK_*` 错误码、`hooks.enabled`。
- [05 §2.1、§2.2、§5](../../05-运行时与代理定义.md)：`RunContext`、`ToolCall`、`ToolOutcome`、AgentLoop 算法、中间件接口与顺序（`HookMiddleware` 第 12 位）。
- [09 §1、§2.3、§10.2](../../09-安全与治理.md)：v2 确认口径、上限比较只看 `decision.risk`、Hooks 的信任模型。
- [11 §2.2、§2.3、§9](../../11-前端指挥台.md)：`/more/hooks` 页与「新建、排序、启用」。
- M69 spec §4.2：`TicketService.add_end_listener`、`TicketEnded(ticket_id, status, worker_session_id)`。
- M70 spec §4.2.6、§4.5.5、§4.2.10：`MandateMiddleware`、`ctx.state["mandate.ceiling"]`、`OrchestratorService.notify(text, *, passive, ref_id, ticket_id)`。
- v2 既有代码：
  - `maa_api/agent/policy.py`：`PolicyDecision`（约 108 行，frozen dataclass，`requires_confirmation`、`risk`、`reasons`、`confirmation_action`、`authorized_by`、`window_seconds`、`extra_payload`）、原子操作的 `grant_atomic_ops`（约 376–437 行）。
  - `maa_api/agent/confirmation.py`：`invoke`（约 104 行，参数 `mode`、`on_audit_created`、`on_audit_terminal`、`on_audit_prepare`、`on_audit_committed`）、`_invoke` 里 `decision = await self.policy.evaluate(...)`（约 143 行）、`_create_confirmation`（约 870 行，`reason="；".join(decision.reasons)`，payload 合并 `extra_payload`，WS `confirm_request` 的 `category=decision.risk.value`）、`_create_audit`（约 1207 行）。
  - `maa_api/db/models.py` 的 `AgentAudit`（约 545 行：`status`、`error_code`、`result_summary`、`risk_level`）。
  - `maa_api/domain/task.py`：`PipelineCreate.tasks`（约 1634 行）、`TASK_LABELS`（约 1539 行）；`maa_api/services/schedule_service.py` 的 `ScheduleWrite.template`（约 48 行，定时任务的任务列表字段名是 `template`）。

## 4. 设计

### 4.1 数据模型

表 `agent_hook`（[03 §12](../../03-数据模型.md)），加在 `maa_api/db/models.py` 末尾：

```python
class AgentHook(SQLModel, table=True):
    """用户配置的 Hook 规则；内置规则不存在，表空即没有 Hook。"""

    __tablename__ = "agent_hook"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    name: str = Field(max_length=64)
    event: str = Field(max_length=24)
    matcher: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("matcher", nullable=False, server_default="'{}'")
    )
    action: str = Field(max_length=16)
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("config", nullable=False, server_default="'{}'")
    )
    ord: int = Field(default=0)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))
```

不建索引（至多 100 行，进程内缓存）。枚举（`maa_api/domain/enums.py`，同步 `tests/domain/test_enums.py`）：

```python
class HookEvent(StrEnum):
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    TICKET_END = "ticket_end"


class HookAction(StrEnum):
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    ANNOTATE = "annotate"
    SCRIPT = "script"      # M93 实现；M92 保存时拒绝
```

迁移 `maa_api/db/migrations/versions/0042_hooks.py`（`down_revision="0041_mcp_servers"`）：

```python
def upgrade() -> None:
    op.create_table(
        "agent_hook",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=24), nullable=False),
        sa.Column("matcher", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_hook"),
    )


def downgrade() -> None:
    op.drop_table("agent_hook")
```

### 4.2 模块与文件

新包 `maa_api/agent/hooks/`（与 M70 的 `maa_api/agent/hooks_fanout.py` 无关，那是流水线终态钩子的扇出）：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 导出 `HookService`、`HookMiddleware` |
| `models.py` | 匹配器与配置的 Pydantic 模型、校验 |
| `matcher.py` | 路径取值、条件运算、时间窗口、整条规则的匹配 |
| `service.py` | CRUD、进程内缓存、各事件点的判定、`on_ticket_end` |
| `middleware.py` | `HookMiddleware` |
| `examples.py` | 两个示例的定义（前端与测试共用同一份 JSON，见 §4.5） |

`models.py`：

```python
PATH_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[(\*|\d{1,3})\]|\.[A-Za-z_][A-Za-z0-9_]*)*$")
HOOK_EXEMPT_TOOLS = frozenset({"report", "ask_user", "stop_pipeline"})
MAX_HOOKS = 100

class Condition(BaseModel):                       # extra="forbid"
    path: str = Field(min_length=1, max_length=120)               # 例如 tasks[*].name
    op: Literal["eq", "ne", "in", "contains", "gt", "gte", "lt", "lte", "exists", "glob"]
    value: Any = None

class AnyOf(BaseModel):                           # extra="forbid"；一层，不能嵌套
    any: list[Condition] = Field(min_length=2, max_length=5)

class TimeWindow(BaseModel):                      # extra="forbid"
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")    # 服务器本地时间
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7], min_length=1)

class HookMatcher(BaseModel):                     # extra="forbid"；缺省字段表示不限
    tools: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=20)
    agents: list[str] = Field(default_factory=list, max_length=20)
    time_window: TimeWindow | None = None
    args: list[Condition | AnyOf] = Field(default_factory=list, max_length=10)
    result: list[Condition | AnyOf] = Field(default_factory=list, max_length=10)
    text_contains: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=10)
    ticket_status: list[Literal["done", "failed", "cancelled"]] = Field(default_factory=list)

class HookConfig(BaseModel):                      # extra="forbid"
    message: str | None = Field(default=None, min_length=1, max_length=200)   # deny、require_confirmation
    note: str | None = Field(default=None, min_length=1, max_length=500)      # annotate
    script: str | None = Field(default=None, max_length=8000)                 # M93

def validate_hook(
    *, name: str, event: HookEvent, action: HookAction, matcher: HookMatcher, config: HookConfig,
    tool_names: Collection[str], spec_names: Collection[str],
) -> None:
    """不合法时抛 AppError(HOOK_INVALID, …, {"issues": [{"field", "reason", "message"}]})，规则见 §4.5。"""
```

`matcher.py`：

```python
@dataclass(frozen=True, slots=True)
class HookSubject:
    """一次判定的输入；各事件只填自己有的字段。"""
    event: HookEvent
    agent: str
    agent_kind: str
    now: datetime                                  # 服务器本地时间，带时区
    tool: str | None = None
    arguments: Mapping[str, Any] | None = None
    result: Any = None                             # after_tool：ToolOutcome.result；出错时为 {"error": ToolOutcome.error}
    text: str | None = None                        # after_model：本次回复的文字
    response_tools: tuple[str, ...] = ()           # after_model：本次回复里要调的工具名
    ticket_status: str | None = None               # ticket_end

def resolve_path(data: Any, path: str) -> list[Any]:
    """按 PATH_PATTERN 逐段取值；[*] 展开列表；取不到的分支丢弃。返回全部取到的值。"""

def condition_holds(cond: Condition | AnyOf, data: Any) -> bool: ...
def in_time_window(window: TimeWindow, now: datetime) -> bool: ...
def matches(matcher: HookMatcher, subject: HookSubject) -> bool: ...
```

`service.py`：

```python
@dataclass(frozen=True, slots=True)
class HookHit:
    hook_id: str
    hook_name: str
    message: str            # deny、require_confirmation 的 message；annotate 的 note

@dataclass(frozen=True, slots=True)
class ToolVerdict:
    deny: HookHit | None                 # 按顺序第一条命中的 deny
    confirm: tuple[HookHit, ...]         # 命中的 require_confirmation
    notes: tuple[str, ...]               # 命中的 annotate，已渲染成「Hook「名」：note」

class HookService:
    def __init__(
        self, session_factory, *, settings_provider: Callable[[], Settings],
        spec_registry: AgentSpecRegistry, tool_names: Callable[[], Collection[str]],
        orchestrator: OrchestratorService | None = None,
        ticket_service: TicketService | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None: ...
    async def start(self) -> None:                     # 读全表进缓存
    def enabled(self) -> bool:                         # settings.hooks.enabled
    def now(self) -> datetime:                         # clock()，测试注入固定时间
    async def list(self, *, event: HookEvent | None = None) -> list[AgentHook]: ...
    async def create(self, data: "HookCreate") -> AgentHook: ...
    async def update(self, hook_id: str, data: "HookUpdate") -> AgentHook: ...   # HOOK_NOT_FOUND
    async def delete(self, hook_id: str) -> None: ...                           # HOOK_NOT_FOUND
    async def before_tool(self, subject: HookSubject) -> ToolVerdict: ...
    async def after_tool(self, subject: HookSubject) -> tuple[str, ...]: ...
    async def before_model(self, subject: HookSubject) -> tuple[str, ...]: ...
    async def after_model(self, subject: HookSubject) -> tuple[str, ...]: ...
    async def on_ticket_end(self, event: TicketEnded) -> None: ...
```

判定方法都是 async：M93 的脚本动作要在里面等沙箱，签名不再改。缓存是 `dict[HookEvent, tuple[AgentHook, ...]]`，只含 `enabled=true` 的行，按 `(ord, created_at, id)` 排序；`create`、`update`、`delete` 提交后重建。

`middleware.py`：

```python
class HookMiddleware(Middleware):
    name = "hook"
    applies_to = {"orchestrator", "worker", "subagent", "background"}   # 05 §5.2「全部」
    def __init__(self, service: Callable[[], HookService | None]) -> None: ...
    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest: ...
    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse: ...
    async def wrap_tool(self, ctx: RunContext, call: ToolCall, call_next) -> ToolOutcome: ...
```

`service` 是取 `ctx.app.state.hook_service` 的函数（服务没装配时返回 None，中间件放行）。

修改的文件：

| 文件 | 改什么 |
|---|---|
| `maa_api/agent/policy.py` | 新增 `ForcedConfirmation` 与 `apply_forced_confirmation`（§4.5） |
| `maa_api/agent/confirmation.py` | `invoke`、`_invoke` 加关键字参数 `forced: ForcedConfirmation \| None = None`；新增 `record_rejected(...)`（§4.5） |
| `maa_api/agent/harness/builtin_middleware.py` | `PolicyMiddleware.wrap_tool` 把 `ctx.state.get("hook.forced", {}).get(call.id)` 作为 `forced=` 传给 `invoke` |
| 前置检查 #4 的中间件组装处 | 在 `InjectionGuardMiddleware` 之后、`LoopGuardMiddleware` 之前插入 `HookMiddleware(lambda: getattr(app.state, "hook_service", None))` |
| `maa_api/main.py` | lifespan 装配（§4.5 末尾）、`include_router(hooks.router)` |
| `maa_api/db/repositories/hooks.py`（新） | `AgentHookRepository(BaseRepository)`：`list_all()`、`get(id)`、`create(row)`、`delete(row)`、`count()` |

### 4.3 接口

路由文件 `maa_api/api/routers/hooks.py`，`APIRouter(prefix="/api/agent/hooks", tags=["agent"])`，挂 `require_auth`，Pydantic 响应模型写在本文件。路径参数 `{hook_id}`（04 §2.5 写作 `{id}`）。

| 方法与路径 | 函数名 | 请求 | 响应与状态码 |
|---|---|---|---|
| GET `/api/agent/hooks` | `list_hooks` | query `event?`（`HookEvent`） | 200 `{items: [HookOut]}`，按事件在枚举里的顺序、`ord`、`created_at` 排序 |
| POST `/api/agent/hooks` | `create_hook` | `HookCreate` | 201 `HookOut`（没有单条查询端点，不带 `Location`）；400 `HOOK_INVALID` |
| PUT `/api/agent/hooks/{hook_id}` | `update_hook` | `HookUpdate` | 200 `HookOut`；404 `HOOK_NOT_FOUND`；400 `HOOK_INVALID` |
| DELETE `/api/agent/hooks/{hook_id}` | `delete_hook` | — | 204；404 `HOOK_NOT_FOUND` |

```python
class HookCreate(BaseModel):                       # extra="forbid"
    name: str = Field(min_length=1, max_length=64)
    event: HookEvent
    matcher: HookMatcher = Field(default_factory=HookMatcher)
    action: HookAction
    config: HookConfig = Field(default_factory=HookConfig)
    ord: int = Field(default=0, ge=0, le=9999)
    enabled: bool = True

class HookUpdate(BaseModel):
    """全部可选，省略表示不改。前端调整顺序时只传 ord。合并后的整条规则重新校验。"""
    # 字段同 HookCreate，类型都加 `| None = None`

class HookOut(BaseModel):
    id: str; name: str; event: HookEvent; matcher: HookMatcher; action: HookAction
    config: HookConfig; ord: int; enabled: bool
    created_at: str; updated_at: str               # ISO 8601 带 Z
```

`HOOK_INVALID` 的 `details`：`{"issues": [{"field": "matcher.tools[0]", "reason": "unknown_tool", "message": "没有叫 submit_pipelin 的工具"}]}`。

工具调用被拦下时，模型收到的工具结果（`ToolOutcome.error`）：

```json
{
  "code": "HOOK_BLOCKED",
  "message": "被 Hook「凌晨不刷图」拦下：凌晨 2–7 点不刷图，7 点以后再提交",
  "details": {"hook_id": "5a0c…", "hook_name": "凌晨不刷图", "tool": "submit_pipeline"}
}
```

### 4.4 配置项

| 键 | 类型 | 默认 | 分组 | config-only | 热生效 |
|---|---|---|---|---|---|
| `hooks.enabled` | bool | true | Agent | 否 | none（每次判定现读） |

- `maa_api/settings.py`：`class HooksSettings(BaseModel)`（`extra="ignore"`，`enabled: bool = True`）；`Settings.hooks`；`SETTING_KEYS["hooks.enabled"] = ("hooks", "enabled")`。
- `_FIELDS`：`"hooks.enabled": dict(label="启用 Hooks", group="Agent", type="boolean", hot_action="none")`。
- `config.template.yaml`：

```yaml
hooks:
  # 关闭后所有 Hook 规则都不生效（规则本身保留）。
  enabled: true
```

- `tests/test_settings.py` 的 `ENV_OVERRIDES` 加 `"hooks.enabled": "MAA_HOOKS_ENABLED"`。

### 4.5 行为与算法

事件、匹配字段与动作的组合（不在表里的组合保存时 `HOOK_INVALID`）：

| 事件 | 何时判定 | 可用的匹配字段 | 可用的动作 |
|---|---|---|---|
| `before_tool` | `wrap_tool` 里，调用继续向内传递之前 | tools、agents、time_window、args | deny、require_confirmation、annotate |
| `after_tool` | `call_next` 返回之后 | tools、agents、time_window、args、result | annotate |
| `before_model` | 每次模型请求之前 | agents、time_window | annotate |
| `after_model` | 每次模型回复之后 | tools（回复里要调的工具）、agents、time_window、text_contains | annotate |
| `ticket_end` | 工单进入终态（`TicketService` 的结束监听） | agents（工单的 Worker 定义名）、time_window、ticket_status | annotate |

`script` 在所有事件上都保存失败：`HOOK_INVALID`，`reason="script_not_supported"`，message「脚本动作需要代码沙箱，现在还不能用」。

匹配语义（`matches`）：

- 各字段之间是「且」；字段为空或缺省表示不限。
- `tools`：fnmatch（区分大小写），任一模式匹配即可。`HOOK_EXEMPT_TOOLS`（`report`、`ask_user`、`stop_pipeline`）永远不匹配：前两个是运行的控制流，`stop_pipeline` 是免确认的救援动作（09 §1.4），任何 Hook 都不能拦它。
- `agents`：精确匹配代理定义名。为空时匹配种类为 `orchestrator`、`worker`、`subagent` 的代理；后台代理（摘要、标题、研判、记忆整理、Watchdog）只有被显式写进 `agents` 才会匹配，免得一条通用的提示污染摘要或标题。
- `time_window`：`start < end` 时匹配 `[start, end)`；`start > end` 跨零点，匹配 `[start, 24:00) ∪ [00:00, end)`，`days` 按窗口开始那天算（周五 23:00–02:00 覆盖周六 01:00）。用服务器本地时间（`datetime.now().astimezone()`，与流水线执行器 `maa_api/services/pipeline_runner.py` 约 122 行同一口径）。
- `args`、`result`：每一项都要成立。`Condition` 先 `resolve_path` 取出全部值，再按下表判；`AnyOf` 里任一条件成立即成立。

| op | value | 成立条件（对取出的值逐个判断，任一满足即成立） |
|---|---|---|
| `eq`、`ne` | 任意 JSON | `v == value`、`v != value`（`ne` 在一个值都取不到时不成立） |
| `in` | 列表 | `v in value` |
| `contains` | 任意 | `v` 是字符串时 `str(value) in v`；是列表时 `value in v` |
| `gt`、`gte`、`lt`、`lte` | 数字 | `v` 是数字（排除 bool）且比较成立 |
| `exists` | bool | `true`：至少取到一个值；`false`：一个都取不到（这一条不逐值判断） |
| `glob` | 字符串 | `v` 是字符串且 `fnmatch.fnmatchcase(v, value)` |

- `text_contains`：回复文字包含任一子串。`ticket_status`：终态在列表里。

校验（`validate_hook`，每条违反都进 `issues`）：

| 规则 | reason |
|---|---|
| 事件与动作组合不在上表 | `action_not_allowed_for_event` |
| 匹配字段不属于该事件 | `matcher_field_not_allowed` |
| `tools` 的模式含 `*?[` 以外的部分要能匹配到至少一个已注册工具；以 `mcp__` 开头的模式不检查（MCP 工具随连接出现与消失） | `unknown_tool` |
| `tools` 显式写了豁免工具（不含通配符且在 `HOOK_EXEMPT_TOOLS`） | `exempt_tool` |
| `agents` 不在 `AgentSpecRegistry` | `unknown_agent` |
| `time_window.start == end` | `time_window_empty` |
| `path` 不匹配 `PATH_PATTERN`；`in` 的值不是列表；比较运算的值不是数字；`exists` 的值不是 bool；`glob` 的值不是字符串或超过 100 字 | `condition_invalid` |
| deny、require_confirmation 缺 `config.message`；annotate 缺 `config.note`；给了与动作无关的配置字段 | `config_invalid` |
| 动作为 `script` | `script_not_supported` |
| 新建后总数超过 `MAX_HOOKS` | `too_many_hooks` |

before_tool 的判定（`HookService.before_tool`）：

```
if not enabled(): return ToolVerdict(None, (), ())
for hook in cache[BEFORE_TOOL]:                       # 已按 ord 排好
    try:
        hit = matches(hook.matcher, subject)
    except Exception:                                 # 规则里的数据形状出乎意料
        记 warning；deny、require_confirmation 类按命中处理（09 §10.2 fail closed，
        message 换成「Hook「名」判定出错，这次调用按拦截处理」并且一律当 deny），annotate 类跳过
        continue
    if not hit: continue
    deny 且还没有 deny：记下（之后的 deny 只看第一条）
    require_confirmation：加进 confirm
    annotate：加进 notes
return ToolVerdict(deny, tuple(confirm), tuple(notes))
```

`HookMiddleware.wrap_tool`：

```
svc = service()
if svc is None or not svc.enabled(): return await call_next(call)
subject = HookSubject(BEFORE_TOOL, ctx.spec.name, ctx.spec.kind, svc.now(), tool=call.name, arguments=call.arguments)
verdict = await svc.before_tool(subject)
记一个 span（name="hook.before_tool"，kind=policy，属性 hook.ids、hook.decision=deny|confirm|annotate|none）
if verdict.deny:
    audit_id = await confirmation_service.record_rejected(call.name, call.arguments, tool_ctx,
                   error_code="HOOK_BLOCKED", reason=<message>)   # tool_ctx 照 MandateMiddleware 的写法构造
    return ToolOutcome(status="error", audit_id=audit_id,
                       error={"code": "HOOK_BLOCKED", "message": f"被 Hook「{name}」拦下：{message}",
                              "details": {"hook_id", "hook_name", "tool": call.name}})
if verdict.confirm:
    ctx.state.setdefault("hook.forced", {})[call.id] = ForcedConfirmation(hooks=verdict.confirm)
try:
    outcome = await call_next(call)
finally:
    ctx.state.get("hook.forced", {}).pop(call.id, None)
after = await svc.after_tool(HookSubject(AFTER_TOOL, …, result=outcome.result if outcome.status == "ok" else {"error": outcome.error}))
notes = verdict.notes + after
if notes: ctx.state.setdefault("hook.pending_notes", []).extend(f"（{call.name}）{n}" for n in notes)
return outcome
```

接进 v2 确认链路（`maa_api/agent/policy.py` 新增）：

```python
@dataclass(frozen=True, slots=True)
class ForcedConfirmation:
    hooks: tuple[HookHit, ...]          # HookHit 定义在 hooks/service.py；policy.py 只用 TYPE_CHECKING 导入

def apply_forced_confirmation(decision: PolicyDecision, name: str, forced: ForcedConfirmation) -> PolicyDecision:
    reasons = tuple(f"Hook「{h.hook_name}」要求确认：{h.message}" for h in forced.hooks) + decision.reasons
    extra = {**dict(decision.extra_payload or {}),
             "hooks": [{"id": h.hook_id, "name": h.hook_name} for h in forced.hooks]}
    if decision.requires_confirmation:
        return replace(decision, reasons=reasons, extra_payload=extra)
    return replace(decision, requires_confirmation=True, reasons=reasons, confirmation_action=name,
                   authorized_by=None, window_seconds=None, extra_payload=extra)
```

`ConfirmationService._invoke` 在 `decision = await self.policy.evaluate(...)` 之后加一行：`if forced is not None: decision = apply_forced_confirmation(decision, definition.name, forced)`。之后的建确认、超时、拒绝、过期、审计、执行全部走 v2 原路径，所以：

- 确认卡是同一张，理由里有 Hook 名与 message；`category` 仍是 `decision.risk`（免确认工具被 Hook 要求确认时是 `none`）。
- 原子操作在会话授权窗口内本来免确认（`authorized_by` 指向授权），命中 Hook 后改成单次确认（`confirmation_action` 为工具名），批准只放行这一次，不新建会话授权。
- 本来就要确认的调用（例如带理智药的 `submit_pipeline`、申请 `grant_atomic_ops`）只出一张卡，理由合并，`confirmation_action` 不变。
- `MandateMiddleware`（第 14 位）比较的是 `decision.risk`（09 §2.3）。Hook 不改风险等级，所以推断委托（上限 none）的工单里，被 Hook 要求确认的免确认调用照常弹确认，与账号写入同口径。

拒绝的审计（`ConfirmationService.record_rejected`）：

```python
async def record_rejected(
    self, name: str, arguments: Mapping[str, Any], context: ToolContext, *,
    error_code: str, reason: str,
) -> int:
    """写一条 status=rejected 的 agent_audit：risk_level 取一次只读的 policy.evaluate 结果（评估出错时为 none），
    result_summary=reason（截到 2 KB），duration_ms=0；run_id、ticket_id、agent_name 按前置检查 #10 的方式填。返回 id。"""
```

annotate 的投递：

- `before_tool`、`after_tool`、`after_model` 命中的提示放进 `ctx.state["hook.pending_notes"]`，下一次 `before_model` 取出并清空。`before_model` 命中的提示每次请求都重新判定，不进这个列表。
- `HookMiddleware.before_model` 把「待投递的提示 + 本次 before_model 命中的提示」拼成一段：

```
【系统】Hook 提示（来自用户配置的规则）：
- Hook「凌晨提醒」：现在是凌晨，提交前先确认用户在不在
- （submit_pipeline）Hook「理智提醒」：……
```

  追加位置：`req.messages` 最后一条的 `role` 是 `user` 或 `system` 且 `content` 是字符串时，在它末尾加两个换行再接这一段；否则追加一条 `{"role": "user", "content": <这一段>}`。只改本次请求，不落库，前缀缓存不受影响（06 §2：动态内容在尾部）。
- 回复里没有工具调用时运行就结束了，这时 `after_model` 的提示没有下一次请求可投递，只记在 span 上。
- 共用约定已经说明 `【系统】` 开头的内容不是用户本人说的话（05 §2.4），不改提示词。

工单结束（`on_ticket_end`，由 `ticket_service.add_end_listener(hook_service.on_ticket_end)` 注册）：

```
if not enabled(): return
ticket = await ticket_service.get(event.ticket_id)
subject = HookSubject(TICKET_END, agent=ticket.kind, agent_kind="worker", now=now(), ticket_status=str(event.status))
for hook in cache[TICKET_END] 中命中的 annotate:
    await orchestrator.notify(
        f"工单「{ticket.title}」已结束（{STATUS_LABELS[event.status]}）。Hook「{hook.name}」：{hook.config['note']}",
        passive=False, ref_id=f"ticket:{ticket.id}:hook:{hook.id}"[:64], ticket_id=ticket.id)
```

`STATUS_LABELS = {"done": "已完成", "failed": "失败", "cancelled": "已取消"}`（11 §11）。`passive=False`：工单的终态汇报已经在唤醒总控，这条提示要保证这一轮或下一轮能看到。异常只记日志，不影响工单结束。

两个示例（`maa_api/agent/hooks/examples.py` 的 `EXAMPLE_HOOKS`，前端 `web/src/features/hooks/examples.ts` 是同一份 JSON 的拷贝，测试比对两边一致）：

```json
[
  {
    "name": "凌晨不刷图",
    "event": "before_tool",
    "matcher": {
      "tools": ["submit_pipeline"],
      "time_window": {"start": "02:00", "end": "07:00"},
      "args": [{"path": "tasks[*].name", "op": "eq", "value": "Fight"}]
    },
    "action": "deny",
    "config": {"message": "凌晨 2–7 点不刷图，7 点以后再提交"}
  },
  {
    "name": "Mall 任务要确认",
    "event": "before_tool",
    "matcher": {
      "tools": ["submit_pipeline", "create_schedule", "update_schedule"],
      "args": [{"any": [
        {"path": "tasks[*].name", "op": "eq", "value": "Mall"},
        {"path": "template[*].name", "op": "eq", "value": "Mall"}
      ]}]
    },
    "action": "require_confirmation",
    "config": {"message": "含 Mall 任务的提交都要你确认"}
  }
]
```

| 输入 | 期望行为 |
|---|---|
| 03:00，Worker 调 `submit_pipeline`，tasks 含 `Fight` | `HOOK_BLOCKED`；审计一条 `rejected`；没有确认卡 |
| 03:00，tasks 只有 `Infrast`、`Award` | 放行 |
| 07:00 整 | 不在窗口内（右开），放行 |
| 03:00，`run_copilot_attempt` | 不匹配（示例只列了 `submit_pipeline`）；用户要拦作业就把它加进 tools |
| 定时任务 03:00 到点提交含 Fight 的流水线 | 不经过代理，Hook 管不到 |
| tasks 含 `{"name": "Mall", "shopping": false}` | 本来免确认，现在弹确认卡，理由「Hook「Mall 任务要确认」要求确认：…」，风险 `none` |
| tasks 含 `{"name": "Mall", "shopping": true}` | 本来就要确认（消耗类），只出一张卡，理由合并，风险 `consume` |
| `create_schedule` 的 template 含 Mall | 弹确认卡（定时任务口径本来是否确认不影响，Hook 让它一定确认） |
| 用户拒绝这张卡 | 与 v2 拒绝同口径：`CONFIRMATION_REJECTED`，本轮以 `confirmation_rejected` 结束 |

生命周期装配（`maa_api/main.py`，在 M91 的 `mcp_manager` 之后）：

```python
hook_service = HookService(
    db_session.session_factory,
    settings_provider=get_settings,
    spec_registry=app.state.agent_spec_registry,
    tool_names=lambda: [d.name for d in app.state.confirmation_service.registry.list()],
    orchestrator=app.state.orchestrator_service,
    ticket_service=app.state.ticket_service,
)
await hook_service.start()
app.state.hook_service = hook_service
app.state.ticket_service.add_end_listener(hook_service.on_ticket_end)
```

启动失败分支的 `delattr` 清单加 `"hook_service"`；`HookService` 没有后台任务，不需要关闭。

### 4.6 前端

管理页 `/more/hooks`（11 §2.3、§9），`Page` + `back="/more"`；编辑器用 `?hook=<id>` 或 `?hook=new`（可带 `&example=0|1`）打开 `ResponsiveSheet`（`mobile="full"`）。

| 文件 | 内容 |
|---|---|
| `web/src/components/layout/nav.ts` | `secondaryNav` 的 `agent` 组按 11 §2.2 的顺序加 `{ to: '/more/hooks', label: 'Hooks', icon: ShieldCheck, description: '按条件拦截、要求确认或附加提示', group: 'agent' }` |
| `web/src/routes/router.tsx` | `{ path: 'more/hooks', element: page(<HooksPage />) }`，懒加载 `@/features/hooks/HooksPage` |
| `web/src/features/hooks/api.ts` | `hooksQueryOptions`（key `['api','agent','hooks']`）、`useCreateHook`、`useUpdateHook`、`useDeleteHook`；成功后 invalidate 列表 |
| `web/src/features/hooks/format.ts` | 事件、动作的中文标签，匹配器的一行摘要（`summarizeMatcher`） |
| `web/src/features/hooks/examples.ts` | 两个示例（与后端 `EXAMPLE_HOOKS` 一致） |
| `web/src/features/hooks/HooksPage.tsx` | 按事件分组的列表；每行：启用开关、名字、动作徽标、匹配摘要、上移下移按钮 |
| `web/src/features/hooks/HookEditor.tsx` | 面板里的编辑器 |
| `web/src/features/hooks/hooks.test.tsx`、`format.test.ts` | 见 §6 |

标签：事件 before_tool「调用工具前」、after_tool「工具返回后」、before_model「请求模型前」、after_model「模型回复后」、ticket_end「工单结束时」；动作 deny「拒绝调用」、require_confirmation「要求确认」、annotate「附加提示」、script「运行脚本」。

编辑器：

- 字段：名字；事件（`NativeSelect`）；动作（`SegmentedControl`，只列当前事件允许的；「运行脚本」显示但禁用，`Field description`「需要代码沙箱，暂时不能用」）；工具（多值输入，回车成块，支持 `*`）；代理（多选，来自 `GET /api/agent/specs`，显示 `title`，后台代理排在最后并带说明「后台代理只在这里选了才会匹配」）；时间窗口（开关 + 开始、结束两个 `type="time"` 输入 + 星期多选）；参数条件、结果条件（CodeMirror JSON 编辑器，`@codemirror/lang-json` 已在依赖里，占位示例 `[{"path": "tasks[*].name", "op": "eq", "value": "Fight"}]`）；回复包含的文字；工单终态（多选）；提示语（deny 与 require_confirmation 是「拦截时的说明」，annotate 是「提示内容」）。
- 只显示当前事件可用的匹配字段（§4.5 的表）。
- 页头操作「新建 Hook」与「从示例新建」（菜单两项「凌晨 2–7 点不刷图」「Mall 任务要确认」）。
- 保存失败时把 `details.issues` 按 `field` 显示在对应字段下（`Field error`），找不到对应字段的列在顶部 `InlineAlert`。
- 排序：上移、下移交换相邻两条的 `ord`（两次 `PUT`，只传 `ord`），组内 `ord` 相同时先把整组重新编号为 0、10、20…。
- 页面顶部 `Section hint`：「Hook 只约束代理的工具调用与模型请求。定时任务、手动提交与开放 API 不受影响。」`hooks.enabled=false` 时 `InlineAlert`「Hooks 已关闭，规则不会生效」，带「打开设置」链接到 `sectionPath('agent')`。
- 按钮「新建 Hook」「保存」「删除 Hook」；删除确认「删除 Hook「名字」？」，描述「删除后这条规则立即失效」；Toast「已保存」「已删除」「已停用」「已启用」；空状态「还没有 Hook」+「从示例新建一条试试」+ 按钮「从示例新建」。

### 4.7 提示词与代理定义

无。`【系统】` 前缀的含义已在共用约定里（05 §2.4、§7）。

## 5. 实施任务

### T1 表、枚举与迁移

- 写范围：`maa_api/db/models.py`、`maa_api/domain/enums.py`、`maa_api/db/migrations/versions/0042_hooks.py`、`maa_api/db/repositories/hooks.py`、`tests/db/test_models.py`、`tests/domain/test_enums.py`、`tests/db/test_hooks_migration.py`
- 步骤：按 §4.1。`test_models.py`：`BUSINESS_TABLES`、`UUID_PK_TABLES` 加 `agent_hook`；`EXPECTED_JSON_COLUMNS` 加 matcher、config；表数断言加 1。
- 验证：`.venv/bin/python -m pytest -q tests/db tests/domain/test_enums.py > /tmp/m92-t1.log 2>&1; echo $?` → 0。
- 完成判据：迁移升级、降级、元数据比对通过。

### T2 设置项

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`
- 步骤：按 §4.4。
- 验证：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py > /tmp/m92-t2.log 2>&1; echo $?` → 0。
- 完成判据：设置 schema 里有 `hooks.enabled`。

### T3 匹配器与校验

- 写范围：`maa_api/agent/hooks/__init__.py`、`models.py`、`matcher.py`、`examples.py`、`tests/agent/hooks/test_hook_matcher.py`、`tests/agent/hooks/test_hook_validation.py`
- 步骤：按 §4.2、§4.5 实现；纯函数，不碰数据库。
- 验证：`.venv/bin/python -m pytest -q tests/agent/hooks/test_hook_matcher.py tests/agent/hooks/test_hook_validation.py > /tmp/m92-t3.log 2>&1; echo $?` → 0。
- 完成判据：§6 中这两个文件的用例全部通过；两个示例 `validate_hook` 不报错。

### T4 HookService

- 写范围：`maa_api/agent/hooks/service.py`、`tests/agent/hooks/test_hook_service.py`、`tests/agent/hooks/test_hook_ticket_end.py`
- 步骤：按 §4.2、§4.5；`clock` 注入固定时间做时间窗口测试。
- 验证：`.venv/bin/python -m pytest -q tests/agent/hooks/test_hook_service.py tests/agent/hooks/test_hook_ticket_end.py > /tmp/m92-t4.log 2>&1; echo $?` → 0。
- 完成判据：缓存随 CRUD 更新；判定顺序与 fail closed 符合 §4.5。

### T5 确认链路接入与拒绝审计

- 写范围：`maa_api/agent/policy.py`、`maa_api/agent/confirmation.py`、`maa_api/agent/harness/builtin_middleware.py`（只改 `PolicyMiddleware`）、`tests/agent/test_confirmation_forced.py`
- 步骤：`ForcedConfirmation`、`apply_forced_confirmation`；`invoke`、`_invoke` 的 `forced` 参数；`record_rejected`；`PolicyMiddleware` 传 `forced`。不传 `forced` 时行为与改动前逐字节一致。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_confirmation_forced.py tests/agent/test_confirmation_service.py tests/agent/test_registry_policy.py > /tmp/m92-t5.log 2>&1; echo $?` → 0。
- 完成判据：v2 的确认测试全部不变地通过。

### T6 HookMiddleware 与装配

- 写范围：`maa_api/agent/hooks/middleware.py`、前置检查 #4 的中间件组装处、`maa_api/main.py`、`tests/agent/hooks/test_hook_middleware.py`、`tests/agent/hooks/test_hook_examples.py`
- 步骤：按 §4.5；span 用前置检查 #9 记下的方法。`test_hook_examples.py` 用 M70 的 `tests/fakes/scripted_llm.py`（`ScriptedLLM`、`install_llm`）驱动一个 `general` Worker：第一步调 `submit_pipeline`（含 Fight），第二步 `report`。
- 验证：`.venv/bin/python -m pytest -q tests/agent/hooks > /tmp/m92-t6.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/agent_smoke.py 2>&1 | tail -n 1` → `SMOKE OK`。
- 完成判据：链里 `HookMiddleware` 位于 `InjectionGuardMiddleware` 与 `LoopGuardMiddleware` 之间（`test_hook_middleware_position_in_chain` 断言）。

### T7 REST 端点与错误码

- 写范围：`maa_api/api/routers/hooks.py`、`maa_api/main.py`、`maa_api/domain/errors.py`、`docs/v3/14-现行接口清单.md`、`tests/domain/test_errors.py`、`tests/api/test_hooks_api.py`、`web/openapi.json`、`web/src/types/api.d.ts`
- 步骤：
  1. 四个端点（§4.3）。
  2. `ErrorCode`：`HOOK_NOT_FOUND` 404、`HOOK_INVALID` 400、`HOOK_BLOCKED` 403（各带上一行中文注释）。
  3. `14-现行接口清单.md` §4 加三行、§6 加四行，同步计数；`test_errors.py` 两个计数常量各加 3。
  4. `.venv/bin/python scripts/dump_openapi.py && cd web && pnpm gen:api && cd ..`。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_hooks_api.py tests/domain/test_errors.py tests/domain/test_route_inventory.py > /tmp/m92-t7.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0。
- 完成判据：operationId `agent_list_hooks` 等四个。

### T8 前端 Hooks 页

- 写范围：§4.6 表中的文件；`web/src/components/layout/layout.test.tsx`（只在它枚举 `secondaryNav` 时）
- 步骤：按 §4.6。
- 验证：`cd web && pnpm typecheck > /tmp/m92-tc.log 2>&1 && pnpm test --run > /tmp/m92-vt.log 2>&1 && pnpm build > /tmp/m92-build.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的前端用例通过。

### T9 冒烟与文档

- 写范围：`scripts/hooks_smoke.py`（新）、`docs/v3/02-系统架构.md`、`docs/v3/05-运行时与代理定义.md`（只改 §5.2 第 12 行的「做什么」一栏，写明三种动作）、`docs/v3/13-决策记录.md`（有偏离时）
- 步骤：
  1. `scripts/hooks_smoke.py`：真实 lifespan + 临时目录（照 `scripts/orchestrator_smoke.py` 的写法），`install_llm` 换成脚本模型。经 REST 建「凌晨不刷图」的变体（时间窗口取当前本地时间前 1 分钟到后 2 分钟）→ `worker_service.open_ticket(kind="general", …)` → 脚本模型先调 `submit_pipeline`（含 Fight）再 `report` → 等工单汇报 → 断言该工具消息含 `HOOK_BLOCKED`、`agent_audit` 有一行 `error_code=HOOK_BLOCKED` 且 `status=rejected`、队列里没有新流水线 → 删 Hook。每步 `[ok]`，末行 `SMOKE OK`。
  2. `02 §2.2` 服务表加 `HookService`；`02 §4.1` 的 `hooks/` 写上文件清单；`02 §5` 装配表加 `hook_service`。
- 验证：`.venv/bin/python scripts/hooks_smoke.py 2>&1 | tail -n 1` → `SMOKE OK`。
- 完成判据：§7 全部通过。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_hooks_migration.py` | `test_upgrade_creates_agent_hook_and_downgrade_drops_it` | 默认 `matcher={}`、`config={}`；降级后表不存在 |
| `tests/agent/hooks/test_hook_matcher.py` | `test_resolve_path_expands_wildcards` | `tasks[*].name` 对两条任务返回两个值；`tasks[5].name` 越界返回 `[]` |
| 同上 | `test_condition_ops` | 表中每个 op 的正例与反例；`gt` 对 bool 不成立；`ne` 取不到值时不成立；`exists: false` |
| 同上 | `test_any_of_group` | 两个条件任一成立即成立 |
| 同上 | `test_time_window_plain_and_cross_midnight` | 02:00–07:00：06:59 命中、07:00 不命中；23:00–02:00 且 `days=[5]`：周五 23:30 与周六 01:00 命中，周六 23:30 不命中 |
| 同上 | `test_empty_agents_skip_background_agents` | `agents=[]` 匹配 worker，不匹配 `summarizer`；显式写 `summarizer` 时匹配 |
| 同上 | `test_exempt_tools_never_match` | `tools=["*"]` 不匹配 `report`、`ask_user`、`stop_pipeline` |
| `tests/agent/hooks/test_hook_validation.py` | `test_examples_are_valid` | 两个示例通过 |
| 同上 | `test_invalid_combinations` | 参数化：after_tool + deny、before_model + args、ticket_end + tools、未知工具、显式 `report`、未知代理、`start==end`、`in` 值非列表、deny 缺 message、`script`，各得到对应 reason |
| 同上 | `test_mcp_patterns_are_not_checked_against_registry` | `mcp__weather__*` 在没有 MCP 工具时也能保存 |
| `tests/agent/hooks/test_hook_service.py` | `test_cache_follows_crud_and_order` | 新建、改 `ord`、停用、删除后 `before_tool` 的结果随之变化；同 ord 按 created_at |
| 同上 | `test_first_deny_wins_and_confirms_accumulate` | 两条 deny 取 ord 小的；两条 require_confirmation 都在 `confirm` 里 |
| 同上 | `test_disabled_setting_short_circuits` | `hooks.enabled=false` 时三类动作都不生效 |
| 同上 | `test_matcher_error_fails_closed` | 让 `matches` 抛异常：deny 类当拦截，annotate 类跳过 |
| `tests/agent/hooks/test_hook_ticket_end.py` | `test_ticket_end_annotate_notifies_orchestrator` | 假 `OrchestratorService.notify` 收到含工单标题与 note 的文字，`passive=False` |
| 同上 | `test_ticket_status_filter` | `ticket_status=["failed"]` 时 done 不通知 |
| `tests/agent/test_confirmation_forced.py` | `test_forced_confirmation_on_safe_tool` | SAFE 工具带 `forced` 时 `mode="async"` 返回 `awaiting_confirmation`；确认行 reason 以「Hook「」开头，payload 有 `hooks` |
| 同上 | `test_forced_merges_with_existing_confirmation` | 带理智药的 `submit_pipeline` 只建一条确认，reason 同时含 Hook 与消耗理由，risk 为 consume |
| 同上 | `test_forced_overrides_atomic_grant_once` | 会话有有效原子授权时 `click` 带 `forced` 仍建单次确认，`action="click"`，批准后会话授权不变 |
| 同上 | `test_record_rejected_writes_audit` | `status=rejected`、`error_code=HOOK_BLOCKED`、`result_summary` 为理由 |
| 同上 | `test_no_forced_is_unchanged` | 不传 `forced` 时与改动前的确认行、审计行字段一致 |
| `tests/agent/hooks/test_hook_middleware.py` | `test_deny_short_circuits_before_inner_middlewares` | `call_next` 没被调用；结果 `HOOK_BLOCKED`；有审计 id |
| 同上 | `test_require_confirmation_sets_and_clears_forced_flag` | `call_next` 内能读到 `ctx.state["hook.forced"][call.id]`，返回后被清掉 |
| 同上 | `test_after_tool_notes_reach_next_request_tail` | 下一次 `before_model` 的最后一条消息含「Hook 提示」与工具名；再下一次不再出现 |
| 同上 | `test_before_model_note_merges_into_tail_message` | 最后一条是 user 字符串时拼进去，不新增消息；最后一条是 tool 时追加一条 user |
| 同上 | `test_after_model_text_contains` | 回复含「源石」时下一次请求带提示 |
| 同上 | `test_hook_middleware_position_in_chain` | worker 的链里 `hook` 在 `injection_guard` 之后、`loop_guard` 之前 |
| `tests/agent/hooks/test_hook_examples.py` | `test_night_farming_is_denied_end_to_end` | 固定时钟 03:00：脚本模型驱动的 Worker 收到 `HOOK_BLOCKED`，队列没有新流水线，汇报照常提交 |
| 同上 | `test_night_hook_allows_after_seven` | 固定时钟 07:30：流水线提交成功 |
| 同上 | `test_mall_requires_confirmation_even_without_shopping` | 建出一条 pending 确认，origin 指向该工单 |
| 同上 | `test_backend_and_frontend_examples_match` | 读 `web/src/features/hooks/examples.ts` 里的 JSON 段，与 `EXAMPLE_HOOKS` 相等 |
| `tests/api/test_hooks_api.py` | `test_crud_and_ordering` | 新建 201；列表按事件与 ord 排序；PUT 只传 ord 生效；DELETE 204 |
| 同上 | `test_invalid_returns_issues` | 400 `HOOK_INVALID`，`details.issues[0].field` 与 reason 正确 |
| 同上 | `test_script_is_rejected` | 400，`reason=script_not_supported` |
| 同上 | `test_not_found` | PUT、DELETE 不存在的 id 为 404 `HOOK_NOT_FOUND` |
| `web/src/features/hooks/format.test.ts` | 「匹配摘要」 | 示例一摘要为「submit_pipeline · 02:00–07:00 · 1 个参数条件」 |
| `web/src/features/hooks/hooks.test.tsx` | 「从示例新建并保存」 | 选示例一后保存，请求体与示例一致 |
| 同上 | 「只显示当前事件可用的字段」 | 事件切到「请求模型前」后，工具与参数条件字段消失，动作只剩「附加提示」 |
| 同上 | 「保存失败按字段显示原因」 | 假后端回 `issues`，对应字段下出现 message |
| 同上 | 「上移交换顺序」 | 发出两个只含 `ord` 的 PUT |

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/pytest.log 2>&1; echo $?; tail -n 30 /tmp/pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py 2>&1 | tail -n 1
.venv/bin/python scripts/agent_smoke.py 2>&1 | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py 2>&1 | tail -n 1
.venv/bin/python scripts/hooks_smoke.py 2>&1 | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay; echo $?
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 不新增失败；三个冒烟末行 `SMOKE OK`；评测回放全部通过（没有 Hook 时链路行为不变）；`BROKEN 0`。

手动验收（补充，记进台账）：

1. 「更多 › Hooks」→「从示例新建」→「Mall 任务要确认」，保存。
2. 在指挥台说「把日常做了」：日常工单提交含 Mall 的流水线时，「待你处理」出现确认卡，理由第一句是 Hook 的说明。
3. 把「凌晨不刷图」的时间窗口改成覆盖当前时间，让总控「刷 3 次 1-7」：Worker 的工具结果显示被 Hook 拦下，汇报说明原因，队列里没有新流水线；在「Agent 审计」里能看到这条被拒绝的调用。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 规则写错把代理卡死（例如 `tools=["*"]` + deny） | 豁免 `report`、`ask_user`、`stop_pipeline`；`LoopGuardMiddleware` 照常判卡死；`hooks.enabled` 一键关闭 |
| 改确认服务引入回归 | `forced` 是可选参数；T5 要求不传时行为逐字节一致，v2 的确认测试全部跑 |
| 用户以为 Hook 能管定时任务 | 页面 hint 与非目标写明；例子表里列出 |
| 每次工具调用多一次规则判定 | 进程内缓存、纯函数判定；拒绝时才多一次只读策略评估 |

回滚：`git revert -m 1 <merge>`；数据库 `alembic downgrade 0041_mcp_servers`（删 `agent_hook` 表，规则随表删除）。

## 9. 文档同步

- `docs/v3/02-系统架构.md` §2.2、§4.1、§5；`docs/v3/05-运行时与代理定义.md` §5.2 第 12 行的说明（T9）。
- `docs/v3/14-现行接口清单.md`：4 条端点、3 个错误码。
- `config.template.yaml`：`hooks` 段。
- 计数：表 +1；新枚举 2 个；新配置键 1 个；工具目录不变。

## 10. 停下来问

- 前置检查 #4 显示中间件不是按 05 §5.2 的顺序在一处组装。
- `PolicyMiddleware` 不经过 `ConfirmationService.invoke`，或 M71 之后 `invoke` 的签名与 §3 列的不同到无法只加一个关键字参数。
- `MandateMiddleware` 比较的不是 `decision.risk`（与 09 §2.3 不一致），使「推断委托下被 Hook 要求确认」的口径需要另定。
- 用户要求 Hook 约束定时任务、手动提交或开放 API。
- 需要新的 SSE、WS 事件来展示 Hook 判定。

## 需要补进契约的名字

| 名字 | 用途 | 建议补进 |
|---|---|---|
| `HookEvent`、`HookAction` | `agent_hook.event`、`action` 的取值 | 03 §13 |
| `HookService`（`maa_api/agent/hooks/service.py`，`app.state.hook_service`） | CRUD 与判定 | 02 §2.2、§5 |
| `HookMiddleware` 的文件位置 `maa_api/agent/hooks/middleware.py` | 中间件实现 | 02 §4.1、05 §5.2 |
| `ctx.state["hook.forced"]`、`ctx.state["hook.pending_notes"]` | 中间件之间的共享状态 | 05 §5.1 |
| `ForcedConfirmation`、`apply_forced_confirmation`（`policy.py`）；`ConfirmationService.invoke(..., forced=)`；`ConfirmationService.record_rejected` | 接进 v2 确认链路与拒绝审计 | 05 §2、09 §10.2 |
| `HOOK_EXEMPT_TOOLS`（report、ask_user、stop_pipeline） | Hook 永远不匹配的工具 | 09 §10.2 |
| 路由文件 `maa_api/api/routers/hooks.py`；路径参数 `{hook_id}`；模型 `HookCreate`、`HookUpdate`、`HookOut`、`HookMatcher`、`HookConfig` | OpenAPI 形状 | 04 §2.5 |
| `EXAMPLE_HOOKS`（`maa_api/agent/hooks/examples.py`）与 `web/src/features/hooks/examples.ts` | 两个示例 | 11 §9 |
| `/more/hooks` 的 `?hook=<id>\|new&example=` 查询参数 | 编辑面板 | 11 §2.3 |
| `scripts/hooks_smoke.py` | 冒烟 | 02 §4.3 |
