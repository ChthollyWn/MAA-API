# M69 工单领域模型（规格）

> 阶段：P1　依赖：M68　分支：`ticket-domain`　迁移：`0026_tickets`
> 本文是 M69 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在新分支上，工作区干净 | `git status -sb` | `## ticket-domain`，没有未提交改动 |
| 2 | M68 已合并 | `git tag -l 'v3-m68'` | `v3-m68` |
| 3 | 迁移 head | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0025_trace_spans.py`；名字不同时按 [03 §2](../03-数据模型.md) 取下一个编号并记台账，不算失败 |
| 4 | M66 的运行表与枚举 | `rg -n "class AgentRun\(SQLModel\|class AgentSessionKind\|class AgentRunStatus" maa_api/db/models.py maa_api/domain/enums.py` | 三行匹配 |
| 5 | 0023 给已有表加外键列的写法 | `rg -n "batch_alter_table\|copy_from" maa_api/db/migrations/versions/0023_agent_runs.py` | 有匹配（T2 照这个写法给三张表加外键列） |
| 6 | 代理定义注册表 | `rg -n "def get\(self, name" maa_api/agent/specs/registry.py && rg -n "risk_ceiling" maa_api/agent/specs/schema.py` | 各至少一行 |
| 7 | 注册表已挂到 app.state | `rg -n "app.state.agent_spec_registry = \|app.state.span_recorder = " maa_api/main.py` | 两行 |
| 8 | 重启恢复 | `rg -n "recovered_after_restart" maa_api/agent/runs/recovery.py` | 至少一行 |
| 9 | v3 现行接口清单 | `rg -n "RUN_NOT_FOUND" docs/v3/14-现行接口清单.md` | 错误码表里有一行 |
| 10 | 工单相关名字还不存在 | `rg -n "class Ticket\|TICKET_NOT_FOUND\|ticket_status\|get_active_orchestrator" maa_api web/src tests --glob '!**/node_modules/**'` | 没有输出 |
| 11 | 基线测试 | `.venv/bin/python -m pytest -q > /tmp/m69-base.log 2>&1; echo $?; tail -n 5 /tmp/m69-base.log` | 退出码 0；不为 0 时把失败用例记进台账作基线 |

## 1. 背景与目标

M70 起总控建工单、Worker 执行并汇报。M69 先把工单这一层的数据与服务做完，并在没有任何代理的情况下单独测通：

1. 迁移 `0026_tickets` 建 `ticket`、`mandate`、`ticket_report`、`thread_subscription`、`agent_inbox` 五张表，给 `agent_session`、`agent_run` 加 `ticket_id`，给 `agent_message` 加 `inbox_id`；升级、降级、离线渲染都通过。
2. 七个枚举进 `maa_api/domain/enums.py`；`TICKET_NOT_FOUND`、`TICKET_ENDED` 两个错误码。
3. `TicketService`、`ThreadRouter`、`InboxService` 三个服务：建单与委托、工单状态机、汇报的校验、状态映射与转发、话题订阅与路由、收件箱的写入与批量领取。委托仓储没有更新方法。
4. 三个只读端点 `GET /api/agent/tickets`、`GET /api/agent/tickets/{ticket_id}`、`GET /api/agent/tickets/{ticket_id}/reports`，Pydantic 响应模型，进 OpenAPI 与前端生成类型。
5. WebSocket 新频道 `ticket_status`：后端 `CHANNELS`、前端 `events.ts` 与 `query-bridge.ts` 同步。
6. 收件箱的保留策略；服务重启时把被中断运行所属的工单改成 `blocked` 并补转汇报（[02 §6.5](../02-系统架构.md) 第 3 步）。

## 2. 非目标

- 不建总控与 Worker 会话，不起任何代理运行（M70）。`create_ticket`、`report` 等工具、SSE、收件箱转消息、`FinishHookFanout` 都是 M70。
- 不做工单相关的写端点（发消息、中断、取消端点是 M70）。
- 不做前端页面（M72）；前端只改实时通道与 query key。
- 不做设备租约；`TicketDetail.lease` 由 M71 加。
- 不建事件表（M81）；`ticket.source_trigger_id` 由 M81 加。

## 3. 相关文档

- [02 §2.2](../02-系统架构.md)（三个服务的职责）、§3（并发约束 6：数据库为准）、§5（装配第 6 步）、§6.1、§6.3、§6.5。
- [03 §1、§1.1](../03-数据模型.md)（约定与必改测试）、§6（五张表与改动列）、§13（枚举）、§14（保留策略）、§15（话题键与来源）。
- [04 §1](../04-接口与事件协议.md)、§2.2 的 M69 三行、§4.1 的 `ticket_status`、§5 的 M69 两行、§7。
- [05 §2.4](../05-运行时与代理定义.md)（收件箱转消息，M70 消费本里程碑的数据）、§4.1（汇报契约）。
- [09 §2.1–§2.3](../09-安全与治理.md)（委托组成与有效上限）。
- 既有代码：`maa_api/db/models.py` 文件头的约定、`AgentQuestion`（约 920 行，新表写法的参照）；`maa_api/db/repositories/base.py` 的 `BaseRepository`（40 行）；`maa_api/db/repositories/agent.py` 的 `AgentSessionRepository`（31 行）；`maa_api/db/migrations/versions/0015_agent_question.py`（建表迁移的参照）；`maa_api/services/retention_service.py` 的 `run_retention`（143 行）；`maa_api/api/ws.py` 的 `CHANNELS`（26 行）与 `ConnectionManager.broadcast`（204 行）；`maa_api/api/routers/agent.py` 的 `_page`、`_iso`（236–246 行）；`tests/api/test_questions_router.py` 的 `_setup`（只装一个路由的测试 app 写法）。

## 4. 设计

### 4.1 数据模型

#### 枚举（`maa_api/domain/enums.py`，追加在文件末尾）

七个 `StrEnum`，成员名是取值的大写（`IM_TELEGRAM = "im_telegram"`），按下表的顺序定义成员：

| 枚举 | 取值（按顺序） |
|---|---|
| `TicketStatus` | open、waiting、blocked、done、failed、cancelled |
| `TicketOrigin` | user、trigger、goal、watchdog、system |
| `MandateStrength` | explicit、inferred |
| `EventSource` | web、im_telegram、im_feishu、im_dingtalk、im_qq、pipeline、core、device、schedule、system、watchdog、goal |
| `ReportStatus` | progress、waiting、ready、blocked、failed、dropped |
| `InboxKind` | user_message、report、thread_event、system、trigger |
| `InboxStatus` | pending、consumed、dropped |

`TicketStatus` 另有一个属性，写法照 `PipelineStatus.is_terminal`：

```python
    @property
    def is_terminal(self) -> bool:
        return self in (TicketStatus.DONE, TicketStatus.FAILED, TicketStatus.CANCELLED)
```

#### 表（`maa_api/db/models.py`，追加在 M68 的 `TraceSpan` 之后；import 补 `TicketStatus`、`InboxStatus`）

下面的代码里 `FK(t, d)` 是 `Field(default=None, foreign_key=t, ondelete=d, max_length=36)` 的简写（非空外键去掉 `default=None`，`agent_message.inbox_id` 指向整数主键，不写 `max_length`），`TS(name)` 是 `Field(default_factory=utcnow, sa_column=datetime_column(name))`，`TS0(name)` 是 `Field(default=None, sa_column=datetime_column(name, nullable=True))`。写进 models.py 时展开成完整写法。

```python
class Ticket(SQLModel, table=True):
    """一份授权工作；状态机见 M69 规格 §4.5。"""
    __tablename__ = "ticket"
    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    title: str = Field(max_length=64)
    kind: str = Field(max_length=32)                 # Worker 代理定义的 name
    status: str = Field(default=TicketStatus.OPEN, max_length=16)
    origin: str = Field(max_length=16)
    priority: int = Field(default=1)
    orchestrator_session_id: str | None = FK("agent_session.id", "SET NULL")
    worker_session_id: str | None = FK("agent_session.id", "SET NULL")
    summary: str | None = Field(default=None, sa_column=text_column("summary"))
    last_report_status: str | None = Field(default=None, max_length=16)
    human_action: dict[str, Any] | None = Field(default=None, sa_column=json_column("human_action"))
    result: dict[str, Any] | None = Field(default=None, sa_column=json_column("result"))
    created_at: datetime = TS("created_at")
    updated_at: datetime = TS("updated_at")
    ended_at: datetime | None = TS0("ended_at")
    __table_args__ = (
        Index("ix_ticket_status_updated_at", "status", "updated_at"),
        Index("ix_ticket_kind_created_at", "kind", "created_at"),
    )


class Mandate(SQLModel, table=True):
    """工单的委托：建单时写入，此后不可变（仓储不提供更新方法）。"""
    __tablename__ = "mandate"
    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    ticket_id: str = FK("ticket.id", "CASCADE")
    scope: str = Field(sa_column=text_column("scope", nullable=False))
    strength: str = Field(max_length=16)
    source: str = Field(max_length=16)
    sender: str | None = Field(default=None, max_length=128)
    rule_id: str | None = Field(default=None, max_length=48)
    risk_ceiling: str = Field(max_length=16)
    policy_version: str | None = Field(default=None, max_length=16)
    created_at: datetime = TS("created_at")
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_mandate_ticket_id"),)


class TicketReport(SQLModel, table=True):
    __tablename__ = "ticket_report"
    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = FK("ticket.id", "CASCADE")
    run_id: str | None = FK("agent_run.id", "SET NULL")
    status: str = Field(max_length=16)
    summary: str = Field(sa_column=text_column("summary", nullable=False))
    human_action: dict[str, Any] | None = Field(default=None, sa_column=json_column("human_action"))
    details: dict[str, Any] | None = Field(default=None, sa_column=json_column("details"))
    forwarded: bool = Field(default=False)
    created_at: datetime = TS("created_at")
    __table_args__ = (
        Index("ix_ticket_report_ticket_id_created_at", "ticket_id", "created_at"),
        {"sqlite_autoincrement": True},
    )


class ThreadSubscription(SQLModel, table=True):
    __tablename__ = "thread_subscription"
    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = FK("ticket.id", "CASCADE")
    thread_key: str = Field(max_length=160)
    active: bool = Field(default=True)
    created_at: datetime = TS("created_at")
    ended_at: datetime | None = TS0("ended_at")
    __table_args__ = (
        UniqueConstraint("ticket_id", "thread_key", name="uq_thread_subscription_ticket_id_thread_key"),
        Index("ix_thread_subscription_thread_key_active", "thread_key", "active"),
        {"sqlite_autoincrement": True},
    )


class AgentInbox(SQLModel, table=True):
    __tablename__ = "agent_inbox"
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = FK("agent_session.id", "CASCADE")
    kind: str = Field(max_length=24)
    thread_key: str | None = Field(default=None, max_length=160)
    ref_id: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("payload", nullable=False, server_default="'{}'")
    )
    status: str = Field(default=InboxStatus.PENDING, max_length=16)
    created_at: datetime = TS("created_at")
    consumed_at: datetime | None = TS0("consumed_at")    # 进入终态（消费或丢弃）的时间，保留策略按它算
    consumed_run_id: str | None = FK("agent_run.id", "SET NULL")
    __table_args__ = (
        Index("ix_agent_inbox_session_id_status_id", "session_id", "status", "id"),
        {"sqlite_autoincrement": True},
    )
```

改动列：`AgentSession` 与 `AgentRun` 各加 `ticket_id: str | None = Field(default=None, foreign_key="ticket.id", ondelete="SET NULL", max_length=36)`；`AgentMessage` 加 `inbox_id: int | None = Field(default=None, foreign_key="agent_inbox.id", ondelete="SET NULL")`。三处都不加索引。

`mandate.source` 写成 16 位：[03 §6.2](../03-数据模型.md) 写的是 str24，但 `tests/db/test_models.py` 的 `test_string_columns_declare_documented_widths` 把所有名为 `source` 的字符串列钉死为 16 位，`EventSource` 最长的取值 `im_telegram` 只有 11 个字符。已在 §11 申请改 03。

#### 迁移 `maa_api/db/migrations/versions/0026_tickets.py`

`revision = "0026_tickets"`，`down_revision = "0025_trace_spans"`（前置检查 3 的实际 head）。模块 docstring 说明五张新表与三处外键列，降级只删结构、不涉及数据回填。

upgrade 顺序：

1. `op.create_table("ticket", ...)`：列与上面的模型一一对应；`sa.ForeignKeyConstraint(["orchestrator_session_id"], ["agent_session.id"], name="fk_ticket_orchestrator_session_id_agent_session", ondelete="SET NULL")`，`worker_session_id` 同理（名字 `fk_ticket_worker_session_id_agent_session`）；`sa.PrimaryKeyConstraint("id", name="pk_ticket")`；随后 `op.create_index` 建两条索引。
2. `op.create_table("mandate", ...)`：FK `fk_mandate_ticket_id_ticket`（CASCADE）、`sa.UniqueConstraint("ticket_id", name="uq_mandate_ticket_id")`、`pk_mandate`。
3. `op.create_table("ticket_report", ..., sqlite_autoincrement=True)`：FK `fk_ticket_report_ticket_id_ticket`（CASCADE）、`fk_ticket_report_run_id_agent_run`（SET NULL）、`pk_ticket_report`；索引 `ix_ticket_report_ticket_id_created_at`。
4. `op.create_table("thread_subscription", ..., sqlite_autoincrement=True)`：FK `fk_thread_subscription_ticket_id_ticket`（CASCADE）、`uq_thread_subscription_ticket_id_thread_key`、`pk_thread_subscription`；索引 `ix_thread_subscription_thread_key_active`。
5. `op.create_table("agent_inbox", ..., sqlite_autoincrement=True)`：`payload` 列 `sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))`；FK `fk_agent_inbox_session_id_agent_session`（CASCADE）、`fk_agent_inbox_consumed_run_id_agent_run`（SET NULL）、`pk_agent_inbox`；索引 `ix_agent_inbox_session_id_status_id`。
6. 三张已有表加外键列，用 0023 加 `agent_session.parent_session_id`、`agent_message.run_id` 的同一写法（前置检查 5）：`with op.batch_alter_table("<表>", copy_from=<本迁移视角的表定义>) as batch_op:` 里先 `batch_op.add_column(sa.Column("ticket_id", sa.String(length=36), nullable=True))`，再 `batch_op.create_foreign_key("fk_agent_session_ticket_id_ticket", "ticket", ["ticket_id"], ["id"], ondelete="SET NULL")`。`agent_run` 用 `fk_agent_run_ticket_id_ticket`；`agent_message` 加 `inbox_id`（`sa.Integer()`）与 `fk_agent_message_inbox_id_agent_inbox`。`copy_from` 用三个 helper：`_agent_session_table()`、`_agent_run_table()`、`_agent_message_table()`，各自返回 0025 时的完整 `sa.Table`（列、主键名、外键名与 ondelete、索引、唯一约束、`sqlite_autoincrement`）；0023 已经写过同名 helper 的，复制过来；没写过的，照 models.py 里该表本迁移之前的定义逐列抄。

downgrade 顺序：先用 batch（`copy_from` 传「含新列」的表定义：helper 加一个 `with_ticket: bool` 参数）删掉三处外键与列，再按 `agent_inbox`、`thread_subscription`、`ticket_report`、`mandate`、`ticket` 的顺序先删索引后删表。

### 4.2 模块与文件

#### `maa_api/db/repositories/ticket.py`（新）

五个仓储，都继承 `BaseRepository`，不 commit；`create` 用 `merge()+flush()`，`get` 带 `populate_existing=True`（与 `AgentSessionRepository` 同口径）。

```python
class TicketRepository(BaseRepository):
    async def create(self, ticket: Ticket) -> Ticket: ...
    async def get(self, ticket_id: str) -> Ticket | None: ...
    async def list(self, *, statuses: Collection[str] | None = None, kind: str | None = None,
                   origin: str | None = None, page: int = 1, size: int = 20) -> Page[Ticket]: ...
        # 按 updated_at DESC, id DESC
    async def update_if_status(self, ticket_id: str, *, allowed: Collection[str],
                               values: Mapping[str, Any]) -> bool: ...
        # UPDATE ticket SET ... WHERE id=? AND status IN allowed；返回是否改到行
    async def titles(self, ticket_ids: Iterable[str]) -> dict[str, str]: ...
    async def worker_turn_running(self, worker_session_id: str | None) -> bool: ...
        # 该会话有 status IN ('running','waiting_human') 的 agent_run；会话为空返回 False


class MandateRepository(BaseRepository):          # 委托不可变：没有 update / delete（09 §2.1）
    async def create(self, mandate: Mandate) -> Mandate: ...
    async def get_by_ticket(self, ticket_id: str) -> Mandate | None: ...


class TicketReportRepository(BaseRepository):
    async def create(self, report: TicketReport) -> TicketReport: ...
    async def recent(self, ticket_id: str, *, limit: int = 5) -> list[TicketReport]: ...   # id DESC
    async def list(self, ticket_id: str, *, page: int = 1, size: int = 20) -> Page[TicketReport]: ...
        # created_at DESC, id DESC
    async def mark_forwarded(self, report_id: int) -> bool: ...
    async def list_unforwarded(self, *, limit: int = 50) -> list[TicketReport]: ...
        # forwarded=false，id ASC；status IN (waiting, ready, blocked, failed)，
        # 或 status=dropped 且所属工单 origin=user（join ticket）


class ThreadSubscriptionRepository(BaseRepository):
    async def upsert_active(self, ticket_id: str, thread_key: str, *, now: datetime) -> ThreadSubscription: ...
        # 没有就插入；已有（含已停用）就置 active=true、ended_at=NULL
    async def deactivate(self, ticket_id: str, thread_key: str, *, now: datetime) -> bool: ...
    async def deactivate_for_ticket(self, ticket_id: str, *, now: datetime) -> int: ...
    async def list_for_ticket(self, ticket_id: str) -> list[ThreadSubscription]: ...
    async def active_ticket_ids(self, thread_key: str) -> list[str]: ...   # 订阅 id ASC，去重


class AgentInboxRepository(BaseRepository):
    async def create(self, item: AgentInbox) -> AgentInbox: ...
    async def pending_ids(self, session_id: str, *, limit: int) -> list[int]: ...   # pending，id ASC
    async def mark_consumed(self, ids: Sequence[int], *, run_id: str, now: datetime) -> int: ...
        # SET status='consumed', consumed_at=now, consumed_run_id=run_id WHERE id IN ids AND status='pending'
    async def list_consumed_by(self, ids: Sequence[int], *, run_id: str) -> list[AgentInbox]: ...
        # id IN ids AND consumed_run_id=run_id，id ASC，populate_existing
    async def drop_pending(self, session_id: str, *, now: datetime) -> int: ...
    async def count_pending(self, session_id: str) -> int: ...
    async def purge_finished_before(self, cutoff: datetime) -> int: ...
        # DELETE WHERE status IN ('consumed','dropped') AND consumed_at < cutoff
```

`maa_api/db/repositories/agent.py` 的 `AgentSessionRepository` 加一个方法：

```python
async def get_active_orchestrator(self) -> AgentSession | None:
    """kind='orchestrator' AND status='active' AND archived_at IS NULL，按 created_at DESC 取一条。"""
```

#### `maa_api/agent/tickets/mandates.py`（新；同目录另建只有一行 docstring 的 `__init__.py`）

```python
RISK_ORDER: Mapping[RiskLevel, int] = {RiskLevel.NONE: 0, RiskLevel.CONSUME: 1, RiskLevel.DESTRUCTIVE: 2}
MAX_SCOPE_CHARS = 2000
#: 工单来源允许的事件源：USER → web 与四个 im_*；TRIGGER → 除 web 外全部；GOAL → goal；
#: WATCHDOG → watchdog；SYSTEM → pipeline、core、device、schedule、system。TRIGGER 还要求 rule_id。
ORIGIN_SOURCES: Mapping[TicketOrigin, frozenset[EventSource]] = {...}

def strength_for(origin: TicketOrigin) -> MandateStrength:
    """user → explicit，其余 → inferred（09 §2.1）。"""

def ceiling_for(strength: MandateStrength, spec_ceiling: RiskLevel | str) -> RiskLevel:
    """explicit 取代理定义上限，inferred 固定 none。"""

def lower_ceiling(*ceilings: RiskLevel | str) -> RiskLevel:
    """按 RISK_ORDER 取最低；至少一个参数。"""

def ceiling_risk(risk: RiskLevel | str, confirmation_action: str | None) -> RiskLevel:
    """参与上限比较的风险：grant_atomic_ops 按 consume 计，其余就是 risk（09 §2.3、§2.4）。"""

def exceeds_ceiling(risk: RiskLevel | str, ceiling: RiskLevel | str) -> bool:
    """risk 为 consume/destructive 且序号高于 ceiling 时为 True；risk 为 none 永远 False。"""

def policy_version() -> str:
    """策略规则集的哈希：对 {safe: SAFE_TOOL_NAMES, atomic: ATOMIC_TOOL_NAMES,
    account_write: ACCOUNT_WRITE_TOOLS, risk_fields: RISK_FIELDS}（集合排序后）做
    json.dumps(sort_keys=True) 再 sha256，取前 12 位十六进制。"""

def build_mandate(
    *, ticket_id: str, scope: str, origin: TicketOrigin, source: EventSource,
    sender: str | None, rule_id: str | None, spec_ceiling: RiskLevel | str,
) -> Mandate:
    """校验并返回一个未落库的 Mandate；校验失败抛 AppError(INVALID_PARAMETER)。"""
```

`build_mandate` 的校验：`scope` 去首尾空白后 1–2000 字；`source` 在 `ORIGIN_SOURCES[origin]` 里；`origin=user` 时 `sender` 必填且为 `web` 或以 `im:` 开头；`origin=trigger` 时 `rule_id` 必填；`sender` 不超过 128 字且不含空白；`rule_id` 不超过 48 字。

#### `maa_api/agent/tickets/reports.py`（新）

```python
MAX_SUMMARY_CHARS = 600
MAX_PROMPT_CHARS = 200
MAX_DETAILS_BYTES = 8192
REPORT_TO_TICKET_STATUS: Mapping[ReportStatus, TicketStatus] = {
    ReportStatus.PROGRESS: TicketStatus.OPEN, ReportStatus.WAITING: TicketStatus.WAITING,
    ReportStatus.READY: TicketStatus.DONE, ReportStatus.BLOCKED: TicketStatus.BLOCKED,
    ReportStatus.FAILED: TicketStatus.FAILED, ReportStatus.DROPPED: TicketStatus.CANCELLED,
}
FORWARDED_STATUSES = frozenset({ReportStatus.WAITING, ReportStatus.READY,
                                ReportStatus.BLOCKED, ReportStatus.FAILED})


class HumanAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["confirmation", "question", "decision"]
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    ref_id: str | None = Field(default=None, max_length=64)


class ReportInput(BaseModel):
    """一次汇报（05 §4.1）；M70 的 report 工具直接复用它做参数模型。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: ReportStatus
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    human_action: HumanAction | None = None
    details: dict[str, Any] | None = None
    # model_validator(mode="after")：waiting/blocked 缺 human_action → ValueError；
    # 其余状态带了 human_action 就置 None；details 的 json.dumps(ensure_ascii=False)
    # 编成 UTF-8 超过 MAX_DETAILS_BYTES → ValueError。


def parse_report(data: Mapping[str, Any]) -> ReportInput:
    """ValidationError → AppError(INVALID_PARAMETER, "汇报内容不合法", {"issues": ...})。"""

def ticket_status_for(status: ReportStatus) -> TicketStatus: ...

def should_forward(status: ReportStatus, origin: TicketOrigin | str) -> bool:
    """FORWARDED_STATUSES 转发；dropped 只在 origin=user 时转发；progress 不转发。"""

def report_inbox_payload(ticket: Ticket, report: TicketReport) -> dict[str, Any]:
    """{ticket_id, ticket_title, ticket_kind, report_id, status, summary, human_action}。"""
```

#### `maa_api/agent/tickets/threads.py`（新）

```python
MAX_THREAD_KEY_CHARS = 160
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
THREAD_KEY_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "chat": re.compile(r"^chat:orchestrator$"),
    "ticket": re.compile(rf"^ticket:{_UUID}$"),
    "pipeline": re.compile(rf"^pipeline:{_UUID}$"),
    "core": re.compile(r"^core:[a-z0-9][a-z0-9_-]{0,31}$"),
    "device": re.compile(r"^device:[a-z0-9][a-z0-9_-]{0,31}$"),
    "schedule": re.compile(r"^schedule:[a-z0-9][a-z0-9_-]{0,47}$"),
    "system": re.compile(r"^system:[a-z][a-z0-9_]{0,63}$"),
    "im": re.compile(r"^im:(telegram|feishu|dingtalk|qq):[A-Za-z0-9_@.:=+/-]{1,128}$"),
    "goal": re.compile(rf"^goal:{_UUID}$"),
    "watchdog": re.compile(r"^watchdog:[a-z][a-z0-9_]{0,47}$"),
}
#: 03 §15 的来源与前缀对应，M81 用它校验事件。
SOURCE_PREFIXES: Mapping[EventSource, frozenset[str]] = { ... }   # web→{chat, ticket}，im_*→{im}，其余同名


# 两个 @dataclass(frozen=True, slots=True)：
# ThreadKey(raw: str, prefix: str, value: str)            value 是第一个冒号之后的部分
# RouteResult(routed_to: Literal["ticket", "orchestrator", "none"],
#             inbox_ids: tuple[int, ...], ticket_ids: tuple[str, ...])

def parse_thread_key(key: str) -> ThreadKey:
    """长度超过 160、前缀未知或格式不符 → AppError(INVALID_PARAMETER, "话题键格式不对", {"thread_key": key})。"""


class ThreadRouter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], inbox_service: InboxService, *, clock=utcnow) -> None: ...
    async def subscribe(self, ticket_id: str, thread_key: str) -> ThreadSubscription:
        """校验话题键；工单不存在 TICKET_NOT_FOUND，已结束 TICKET_ENDED；ticket: 与 chat: 前缀不允许订阅（INVALID_PARAMETER）。"""
    async def unsubscribe(self, ticket_id: str, thread_key: str) -> bool: ...
    async def publish(self, thread_key: str, *, kind: InboxKind = InboxKind.THREAD_EVENT,
                      ref_id: str | None = None, payload: Mapping[str, Any] | None = None) -> RouteResult: ...
    async def to_orchestrator(self, *, kind: InboxKind, thread_key: str | None, ref_id: str | None,
                              payload: Mapping[str, Any], preferred_session_id: str | None = None) -> int | None:
        """写进总控收件箱，返回收件箱项 id；没有活动的总控会话返回 None。"""
    async def orchestrator_session_id(self, preferred: str | None = None) -> str | None:
        """preferred 存在、kind=orchestrator、status=active 且未归档就用它，否则 get_active_orchestrator()。"""
```

#### `maa_api/agent/tickets/inbox.py`（新）

```python
MAX_PAYLOAD_BYTES = 16384
InboxListener = Callable[[str], None]          # 参数是收件会话 id


class InboxService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, clock: Callable[[], datetime] = utcnow) -> None: ...
    def add_listener(self, listener: InboxListener) -> None:
        """M70 的 OrchestratorService、WorkerService 用它在有新项时起运行。"""
    async def put(self, session_id: str, kind: InboxKind, *, thread_key: str | None = None,
                  ref_id: str | None = None, payload: Mapping[str, Any] | None = None) -> AgentInbox:
        """校验后写一条 pending 项并提交；提交后按注册顺序同步调用监听者，异常只记日志。"""
    async def claim(self, session_id: str, *, run_id: str, limit: int = 50) -> list[AgentInbox]:
        """批量领取，算法见 §4.5.5；limit 1–200，越界 ValueError。"""
    async def drop_pending(self, session_id: str) -> int: ...
    async def pending_count(self, session_id: str) -> int: ...
```

`put` 的校验：`thread_key` 非空时过 `parse_thread_key`；`ref_id` 不超过 64 字；`payload` 必须是 dict，`json.dumps(ensure_ascii=False)` 编成 UTF-8 不超过 16384 字节；任一不符抛 `AppError(INVALID_PARAMETER)`。

#### `maa_api/agent/tickets/service.py`（新）

```python
TicketEndListener = Callable[["TicketEnded"], Awaitable[None] | None]
BLOCKED_AFTER_RESTART_SUMMARY = "服务重启时这个工单的运行被中断了。继续之前先查看现场，不要直接重复刚才的设备操作。"
BLOCKED_AFTER_RESTART_PROMPT = "服务重启打断了这个工单，要继续还是取消？"


# 三个结果类型都是 @dataclass(frozen=True, slots=True)：
# TicketEnded(ticket_id: str, status: TicketStatus, worker_session_id: str | None)
# TicketDetailData(ticket: Ticket, mandate: Mandate, subscriptions: list[ThreadSubscription],
#                  recent_reports: list[TicketReport], turn_running: bool)
# ReportOutcome(report: TicketReport, ticket: Ticket, forwarded_inbox_id: int | None)


class TicketService:
    def __init__(self, session_factory, *, inbox_service: InboxService, thread_router: ThreadRouter,
                 spec_registry: Any, broadcast: Callable[[str, dict[str, Any]], None] | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None: ...
    def add_end_listener(self, listener: TicketEndListener) -> None: ...
    async def create(self, *, kind: str, title: str, scope: str, origin: TicketOrigin, source: EventSource,
                     sender: str | None = None, rule_id: str | None = None, priority: int = 1,
                     orchestrator_session_id: str | None = None) -> tuple[Ticket, Mandate]: ...
    async def attach_worker_session(self, ticket_id: str, worker_session_id: str, *, brief: str) -> Ticket: ...
    async def get(self, ticket_id: str) -> Ticket: ...                      # TICKET_NOT_FOUND
    async def detail(self, ticket_id: str) -> TicketDetailData: ...
    async def list(self, *, statuses: Collection[TicketStatus] | None = None, kind: str | None = None,
                   origin: TicketOrigin | None = None, page: int = 1, size: int = 20) -> Page[Ticket]: ...
    async def list_reports(self, ticket_id: str, *, page: int = 1, size: int = 20) -> Page[TicketReport]: ...
    async def record_report(self, ticket_id: str, report: ReportInput, *, run_id: str | None = None) -> ReportOutcome: ...
    async def cancel(self, ticket_id: str, *, reason: str | None = None) -> Ticket: ...
    async def await_human(self, ticket_id: str, action: HumanAction) -> Ticket | None: ...
    async def human_resolved(self, ticket_id: str, ref_id: str) -> Ticket | None: ...
    async def forward_pending_reports(self, *, limit: int = 50) -> int: ...


async def block_tickets_after_restart(session: AsyncSession, ticket_ids: Iterable[str], *, now: datetime) -> list[str]:
    """供 RunRecovery 在同一事务里调用：把 open/waiting 的工单改成 blocked 并插一条
    forwarded=false 的 blocked 汇报；返回实际改动的工单 id。不提交、不广播。"""


def ticket_status_payload(event: str, ticket: Ticket) -> dict[str, Any]:
    """WS ticket_status 的 data（§4.3）。"""
```

`spec_registry` 只用 `get(name)`，返回对象要有 `kind` 与 `risk_ceiling` 两个属性；`kind` 不是 `worker` 时 `create` 抛 `AppError(INVALID_PARAMETER, "这个代理定义不能负责工单")`。

#### `maa_api/api/routers/tickets.py`（新）

`router = APIRouter(prefix="/api/agent/tickets", tags=["agent"])`。响应模型写在本文件（§4.3）。`_service(request)` 取 `request.app.state.ticket_service`，取不到抛 `AppError(ErrorCode.AGENT_DISABLED, "工单服务还在启动中，请稍后重试")`。`_page`、`_iso` 两个小函数照 `routers/agent.py` 236–246 行的写法在本文件里各写一份。

#### 其他改动

| 文件 | 改什么 |
|---|---|
| `maa_api/domain/errors.py` | `ErrorCode` 加两个成员（上一行中文注释）；`ERROR_HTTP_STATUS` 加两条 |
| `maa_api/api/ws.py` | `CHANNELS` 加 `"ticket_status"` |
| `maa_api/main.py` | import 与 `include_router(tickets.router)`（加在已有 v3 路由之后、`ws_router` 之前）；lifespan 装配（§4.5.8） |
| `maa_api/services/retention_service.py` | 第 6 阶段：收件箱清理（§4.5.7） |
| `maa_api/agent/runs/recovery.py` | 第 3 步：工单改 `blocked`（§4.5.6） |
| `tests/fakes/v3_db.py`（新） | v3 测试共用的建库与造数函数（§6 开头） |

### 4.3 接口

#### 响应模型（`maa_api/api/routers/tickets.py`）

都是 `pydantic.BaseModel`，字段按下表顺序声明；时间字段是 `str`（`_iso` 输出，带 `Z`），可空的写 `X | None`，枚举字段用枚举类型（这样进 OpenAPI 的是取值列表）。

| 模型 | 字段 |
|---|---|
| `HumanActionOut` | `type: Literal["confirmation", "question", "decision"]`、`prompt: str`、`ref_id: str \| None = None` |
| `TicketSummary` | `id`、`title`、`kind`、`status: TicketStatus`、`origin: TicketOrigin`、`priority: int`、`summary`、`last_report_status: ReportStatus \| None`、`human_action: HumanActionOut \| None`、`created_at`、`updated_at`、`ended_at` |
| `MandateOut` | `id`、`scope`、`strength: MandateStrength`、`source: EventSource`、`sender`、`rule_id`、`risk_ceiling: RiskLevel`、`policy_version`、`created_at` |
| `SubscriptionOut` | `thread_key`、`active: bool`、`created_at`、`ended_at` |
| `TicketReportOut` | `id: int`、`ticket_id`、`run_id`、`status: ReportStatus`、`summary`、`human_action: HumanActionOut \| None`、`details: dict[str, Any] \| None`、`forwarded: bool`、`created_at` |
| `TicketDetail` | `ticket: TicketSummary`、`result: dict[str, Any] \| None`、`mandate: MandateOut`、`subscriptions: list[SubscriptionOut]`、`recent_reports: list[TicketReportOut]`（最近 5 条，id 倒序）、`worker_session_id: str \| None`、`turn_running: bool` |
| `TicketPage` | `items: list[TicketSummary]`、`total`、`page`、`size`（三个 `int`） |
| `TicketReportPage` | `items: list[TicketReportOut]`、`total`、`page`、`size` |

#### 端点

| 方法与路径 | 函数名 | 参数 | 响应 | 错误码（`error_responses`） |
|---|---|---|---|---|
| GET `/api/agent/tickets` | `list_tickets` | `statuses: list[TicketStatus] \| None = Query(None, alias="status")`（可重复，写法同 `routers/agent.py` 的 `audit_status`）、`kind: str \| None`、`origin: TicketOrigin \| None`、`page=1`、`size=20` | 200 `TicketPage` | `INVALID_PAGINATION`、`AGENT_DISABLED`、`UNAUTHORIZED` |
| GET `/api/agent/tickets/{ticket_id}` | `get_ticket` | — | 200 `TicketDetail` | `TICKET_NOT_FOUND`、`AGENT_DISABLED`、`UNAUTHORIZED` |
| GET `/api/agent/tickets/{ticket_id}/reports` | `list_ticket_reports` | `page=1`、`size=20` | 200 `TicketReportPage` | `TICKET_NOT_FOUND`、`INVALID_PAGINATION`、`AGENT_DISABLED`、`UNAUTHORIZED` |

三条都挂 `dependencies=[Depends(require_auth)]`，`response_model=` 对应模型；`summary` 分别写「分页读取工单」「读取工单详情」「分页读取工单汇报」。operationId 为 `agent_list_tickets`、`agent_get_ticket`、`agent_list_ticket_reports`。

`GET /api/agent/tickets/{id}` 的响应示例：

```json
{"ticket": {"id": "0f8e6c52-…", "title": "日常", "kind": "daily_ops", "status": "waiting", "origin": "user",
            "priority": 1, "summary": "等你确认理智药", "last_report_status": "waiting",
            "human_action": {"type": "confirmation", "prompt": "确认用 2 瓶理智药", "ref_id": "c-1"},
            "created_at": "2026-11-02T08:00:00Z", "updated_at": "2026-11-02T08:03:10Z", "ended_at": null},
 "result": null,
 "mandate": {"id": "…", "scope": "把日常做了", "strength": "explicit", "source": "web", "sender": "web",
             "rule_id": null, "risk_ceiling": "consume", "policy_version": "3b9f0c2a71de", "created_at": "…"},
 "subscriptions": [{"thread_key": "pipeline:6d1f…", "active": true, "created_at": "…", "ended_at": null}],
 "recent_reports": [], "worker_session_id": null, "turn_running": false}
```

#### WebSocket `ticket_status`

信封 `{type: "ticket_status", ts, data}`，`data` 由 `ticket_status_payload(event, ticket)` 生成：

```json
{"event": "report_created", "ticket_id": "0f8e…", "title": "日常", "kind": "daily_ops",
 "status": "done", "last_report_status": "ready", "summary": "日常做完了", "human_action": null}
```

`event` 取值：`ticket_created`（建单后）、`report_created`（记汇报后，包括取消写的 dropped 汇报）、`ticket_updated`（`await_human`、`human_resolved` 改了状态后）。`summary`、`human_action` 两个键总是带上，没有值时为 `null`。广播失败只记日志。

### 4.4 配置项

无。

### 4.5 行为与算法

#### 4.5.1 工单状态机

| 当前状态 | 允许的变化 | 由谁触发 |
|---|---|---|
| `open`、`waiting`、`blocked` | 变成任一状态（含不变） | 汇报按 §4.5.2 映射；`cancel` → `cancelled`；`await_human` → `waiting`；`human_resolved` → `open`（仅当当前为 `waiting` 且 `human_action.ref_id` 相同）；重启恢复 → `blocked`（仅 `open`、`waiting`） |
| `done`、`failed`、`cancelled` | 不允许任何变化 | `record_report`、`cancel`、`attach_worker_session`、`ThreadRouter.subscribe` 返回 `TICKET_ENDED`；`await_human`、`human_resolved` 返回 `None` 不抛错 |

所有状态变化都用 `TicketRepository.update_if_status` 做条件更新（`allowed` 为非终态集合）；条件更新没改到行时回滚并按「已结束」处理。进入终态的同一事务里：`ended_at=now`；该工单所有订阅 `active=false`、`ended_at=now`；Worker 会话的 pending 收件箱项改成 `dropped`（`consumed_at=now`）。提交之后依次：转发汇报、广播、调用结束监听者（每个监听者的异常只记日志，不影响其他监听者）。

#### 4.5.2 汇报

`record_report(ticket_id, report, run_id=None)`：

1. 读工单：不存在 `TICKET_NOT_FOUND`，终态 `TICKET_ENDED`。
2. 插入 `ticket_report`（`forwarded=false`，`human_action` 与 `details` 用 `model_dump(mode="json")`）。
3. 条件更新工单：`status=ticket_status_for(report.status)`、`summary=report.summary`、`last_report_status=report.status`、`human_action`（waiting、blocked 时取汇报的，其余置 `None`）、`updated_at=now`；新状态为 `done` 或 `failed` 时 `result=report.details`。
4. 新状态是终态时做 §4.5.1 的收尾，然后提交。
5. `should_forward(report.status, ticket.origin)` 为真时调用 `thread_router.to_orchestrator(kind=InboxKind.REPORT, thread_key=f"ticket:{id}", ref_id=str(report.id), payload=report_inbox_payload(...), preferred_session_id=ticket.orchestrator_session_id)`；拿到收件箱 id 就在新事务里 `mark_forwarded`。没有总控会话时保持 `forwarded=false`，等 `forward_pending_reports` 补转。
6. 广播 `report_created`；终态时调用结束监听者。

`cancel(ticket_id, reason)` 等价于 `record_report(ticket_id, ReportInput(status=dropped, summary="已取消" 或 f"已取消：{reason}"[:600]))`。

`forward_pending_reports(limit=50)`：取 `list_unforwarded(limit)`，逐条 `to_orchestrator` 并 `mark_forwarded`；遇到第一个返回 `None`（没有总控会话）就停；返回补转条数。lifespan 启动时调一次，M70 在总控会话建好后再调。

#### 4.5.3 建单与 Worker 会话

`create(...)`：`title` 去首尾空白后 1–64 字，`priority` 只能是 0、1、2，否则 `INVALID_PARAMETER`；`spec = spec_registry.get(kind)`，`spec.kind != "worker"` 时 `INVALID_PARAMETER`；`strength = strength_for(origin)`；`mandate = build_mandate(..., spec_ceiling=spec.risk_ceiling)`，`policy_version=policy_version()`；工单与委托在同一事务里写入；提交后广播 `ticket_created`。

`attach_worker_session(ticket_id, worker_session_id, brief)`：`brief` 去首尾空白后 1–4000 字；工单为终态时 `TICKET_ENDED`；写 `ticket.worker_session_id` 并提交；然后 `inbox_service.put(worker_session_id, InboxKind.SYSTEM, thread_key=f"ticket:{id}", ref_id=ticket_id, payload={"event": "ticket_brief", "ticket_id": id, "title": title, "brief": brief})`。会话行由调用方（M70）先建好。

#### 4.5.4 话题路由

`publish(thread_key, kind, ref_id, payload)`：

```
key = parse_thread_key(thread_key)
if key.prefix == "chat":                       # chat:orchestrator
    targets = [orchestrator_session_id()]
elif key.prefix == "ticket":                   # 工单自己的话题，隐式订阅
    ticket = get(key.value)
    targets = [ticket.worker_session_id] if ticket 存在、非终态、worker_session_id 非空 else [orchestrator_session_id()]
else:
    ids = active_ticket_ids(thread_key)        # 订阅 id 升序
    tickets = 其中非终态且 worker_session_id 非空的工单
    targets = [t.worker_session_id for t in tickets] or [orchestrator_session_id()]
targets 去掉 None 并去重（保持顺序）
for session_id in targets: inbox_service.put(session_id, kind, thread_key=..., ref_id=..., payload=...)
routed_to = "none"（没有目标）| "orchestrator"（目标是总控）| "ticket"
```

`subscribe` 拒绝 `ticket:` 与 `chat:` 前缀（它们不需要订阅）；同一工单重复订阅同一话题是幂等的（`upsert_active`）。工单结束后订阅都已停用，`publish` 还会再按工单状态过滤一次。

#### 4.5.5 收件箱批量领取与并发安全

```
async def claim(session_id, *, run_id, limit=50):
    async with self._locks.setdefault(session_id, asyncio.Lock()):     # 进程内同一会话串行
        async with session_factory() as s:
            ids = await repo.pending_ids(session_id, limit=limit)     # SELECT，不开写事务
            if not ids: return []
            await repo.mark_consumed(ids, run_id=run_id, now=now)    # 第一条写语句开启写事务
            rows = await repo.list_consumed_by(ids, run_id=run_id)   # 只返回本次真正改到的项
            await s.commit()
            return rows                                              # 按 id 升序
```

两层保证同一项不被两个运行领到：进程内按会话加 `asyncio.Lock`；数据库层 `mark_consumed` 的 `AND status='pending'` 是条件更新，SQLite 只允许一个写者，后到的事务改不到已被领走的行，`list_consumed_by` 按 `consumed_run_id` 过滤，所以输家拿到的是空集或剩下的项。两者冲突时以数据库为准（[02 §3](../02-系统架构.md) 第 6 条）。`claim` 只改状态，转成消息是 M70 的事。

#### 4.5.6 重启恢复（RunRecovery 第 3 步）

在 `RunRecovery.recover()` 把上一进程遗留的运行改成 `interrupted` 的那一步里：执行改动之前，用同一筛选条件（`status IN ('running','waiting_human') AND boot_id != 当前 boot_id`）先 `SELECT DISTINCT ticket_id`（去掉空值）；改动之后、同一事务提交之前调用 `block_tickets_after_restart(session, ticket_ids, now=now)`。它对每个 id 条件更新（`allowed={open, waiting}`）：`status=blocked`、`last_report_status=blocked`、`summary=BLOCKED_AFTER_RESTART_SUMMARY`、`human_action={"type": "decision", "prompt": BLOCKED_AFTER_RESTART_PROMPT, "ref_id": None}`、`updated_at=now`，改到行的再插一条 `ticket_report(status=blocked, summary=..., human_action=..., run_id=None, forwarded=false)`。恢复发生在 `ticket_service` 装配之前，所以这里不广播、不转发；lifespan 装配完 `ticket_service` 后调用 `forward_pending_reports()` 补转。

#### 4.5.7 保留策略

`retention_service.py` 加常量 `INBOX_RETENTION_DAYS = 30`；`run_retention` 的 `result` 初始化加 `"agent_inbox_deleted": 0`；在现有最后一个阶段之后追加一段：`result["agent_inbox_deleted"] = await AgentInboxRepository(db).purge_finished_before(now - timedelta(days=INBOX_RETENTION_DAYS))`、`await db.commit()`、一行 `logger.info("保留策略：已删除 %d 条已处理的 Agent 收件箱项", ...)`、`await _incremental_vacuum(db)`。函数 docstring 里列返回键的那句补上新键。`ticket`、`mandate`、`ticket_report`、`thread_subscription` 不自动删除。

#### 4.5.8 装配（`maa_api/main.py` lifespan）

在 `app.state.span_recorder = ...`（M68）之后：

```python
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter

app.state.inbox_service = inbox_service = InboxService(db_session.session_factory)
app.state.thread_router = thread_router = ThreadRouter(db_session.session_factory, inbox_service)
ticket_service = TicketService(
    db_session.session_factory,
    inbox_service=inbox_service,
    thread_router=thread_router,
    spec_registry=app.state.agent_spec_registry,
    broadcast=lambda message_type, data: ws_manager.broadcast(message_type, data),
)
app.state.ticket_service = ticket_service
await ticket_service.forward_pending_reports()
```

三个属性名同时加进 lifespan 启动失败时清理 `app.state` 的属性元组（main.py 约 1393 行的 `for attribute in (...)`）。

#### 4.5.9 边界情况

| 输入 | 期望 |
|---|---|
| `record_report` 给已结束的工单 | `TICKET_ENDED`，不插汇报 |
| 两个 `record_report(ready)` 并发 | 只有一个成功，另一个 `TICKET_ENDED`；结束监听者只调用一次 |
| `waiting` 汇报缺 `human_action` | `parse_report` 抛 `INVALID_PARAMETER`，`details.issues` 指向 `human_action` |
| `ready` 汇报带了 `human_action` | 忽略该字段，工单 `human_action` 置空 |
| `details` 超过 8 KiB | `INVALID_PARAMETER` |
| `dropped` 汇报，工单 `origin=trigger` | 不转发，`forwarded` 保持 false，`list_unforwarded` 也不返回它 |
| 转发时没有活动的总控会话 | 汇报照常落库，`forwarded=false`，`forward_pending_reports` 之后补转 |
| `ticket.orchestrator_session_id` 指向已归档的会话 | 转发给当前活动的总控会话 |
| `publish("pipeline:<id>")` 有两个工单订阅 | 两个 Worker 收件箱各一条，`routed_to="ticket"` |
| `publish` 的订阅工单还没有 Worker 会话 | 该工单跳过；没有其他目标时交总控 |
| 话题键 `pipeline:ABC` 或 `foo:1` | `INVALID_PARAMETER` |
| `subscribe(ticket_id, "ticket:<id>")` | `INVALID_PARAMETER` |
| `claim(limit=0)` | `ValueError`（调用方用错，不是用户输入） |
| 收件箱 `payload` 超过 16 KiB | `INVALID_PARAMETER`，不落库，不通知监听者 |
| `create(kind="researcher")`（子代理） | `INVALID_PARAMETER` |
| `create(origin=user, source=pipeline)` | `INVALID_PARAMETER` |
| `await_human` 给已结束的工单 | 返回 `None`，不抛错，不广播 |
| `human_resolved` 的 `ref_id` 与当前 `human_action` 不同 | 返回 `None`，状态不变 |

### 4.6 前端

只改实时通道与 query key，不加页面。

- `web/src/api/keys.ts` 的 `agent` 下加：
  ```ts
  tickets: (filters?: Readonly<Record<string, unknown>>) =>
    [...root, 'agent', 'tickets', filters ?? {}] as const,
  ticket: (ticketId: string) => [...root, 'agent', 'tickets', 'detail', ticketId] as const,
  ticketReports: (ticketId: string, filters?: Readonly<Record<string, unknown>>) =>
    [...root, 'agent', 'tickets', ticketId, 'reports', filters ?? {}] as const,
  ```
- `web/src/realtime/events.ts`：加
  ```ts
  export interface TicketStatusEvent {
    event: 'ticket_created' | 'ticket_updated' | 'report_created'
    ticket_id: string; title: string; kind: string; status: string; last_report_status: string | null
    summary?: string | null
    human_action?: { type: string; prompt: string; ref_id?: string | null } | null
  }
  ```
  `ServerEvent` 联合类型加 `| WsEnvelope<'ticket_status', TicketStatusEvent>`；`REALTIME_STATE_CHANNELS` 在 `'agent_event'` 之后加 `'ticket_status'`。后端 `CHANNELS` 必须同时加，否则订阅整体被拒（`ws.py` 约 467 行）。
- `web/src/realtime/query-bridge.ts` 的 `handleServerEvent` 加分支：
  ```ts
  case 'ticket_status':
    invalidateBatched(queryClient, keys.agent.tickets())
    invalidateBatched(queryClient, keys.agent.ticket(event.data.ticket_id))
    invalidateBatched(queryClient, keys.agent.ticketReports(event.data.ticket_id))
    break
  ```

### 4.7 提示词与代理定义

无。

## 5. 实施任务

### T1 枚举与错误码

- 写范围：`maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`
- 步骤：
  1. 按 §4.1 追加七个枚举；`test_enums.py` 的 `EXPECTED_VALUES` 加七项，并加 `test_ticket_status_is_terminal`（只有 done、failed、cancelled 为真）。
  2. `ErrorCode` 加 `TICKET_NOT_FOUND`（注释「没有这个工单」）、`TICKET_ENDED`（注释「工单已结束」），`ERROR_HTTP_STATUS` 绑 404、409。
  3. `14-现行接口清单.md` §4：把两行 ``| `TICKET_NOT_FOUND` | 404 | 没有这个工单 |``、``| `TICKET_ENDED` | 409 | 工单已结束，不能再改 |`` 追加到 `RUN_NOT_FOUND` 所在的那张表；§4 正文的总数句加 2。
  4. `test_errors.py` 的 `ALL_CODE_COUNT`、`HTTP_BOUND_CODE_COUNT` 各加 2，`SECTION_COUNT` 不变。
- 验证：`.venv/bin/python -m pytest -q tests/domain > /tmp/m69-t1.log 2>&1; echo $?; tail -n 5 /tmp/m69-t1.log` → 退出码 0。
- 完成判据：两个测试文件通过，枚举与错误码表一致。

### T2 模型与迁移 0026

- 写范围：`maa_api/db/models.py`、`maa_api/db/migrations/versions/0026_tickets.py`、`tests/db/test_models.py`、`tests/db/test_tickets_migration.py`（新）
- 步骤：
  1. 按 §4.1 加五个表类与三处改动列；文件头 docstring 的表数加 5。
  2. 按 §4.1 写迁移。
  3. `test_models.py`：`BUSINESS_TABLES` 加五张表；`UUID_PK_TABLES` 加 `ticket`、`mandate`；`AUTOINCREMENT_TABLES` 加 `ticket_report`、`thread_subscription`、`agent_inbox`；`EXPECTED_INDEXES` 加五条索引；`EXPECTED_UNIQUE_CONSTRAINTS` 加 `uq_mandate_ticket_id`、`uq_thread_subscription_ticket_id_thread_key`；`EXPECTED_FK_CASCADE` 加 `(mandate, ticket_id)`、`(ticket_report, ticket_id)`、`(thread_subscription, ticket_id)`、`(agent_inbox, session_id)`；`EXPECTED_FK_SET_NULL` 加 `(ticket, orchestrator_session_id)`、`(ticket, worker_session_id)`、`(ticket_report, run_id)`、`(agent_inbox, consumed_run_id)`、`(agent_session, ticket_id)`、`(agent_run, ticket_id)`、`(agent_message, inbox_id)`；`EXPECTED_TEXT_COLUMNS` 加 `(ticket, summary)`、`(mandate, scope)`、`(ticket_report, summary)`；`EXPECTED_JSON_COLUMNS` 加 `(ticket, human_action)`、`(ticket, result)`、`(ticket_report, human_action)`、`(ticket_report, details)`、`(agent_inbox, payload)`。
  4. 新建 `test_tickets_migration.py`，写法照 `tests/db/test_agent_question_migration.py`（§6）。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/db > /tmp/m69-t2.log 2>&1; echo $?; tail -n 5 /tmp/m69-t2.log
  ```
  退出码 0（含 `test_initial_migration.py` 的列、索引、外键、可重复升级、降级到底再升级、离线渲染）。
- 完成判据：迁移升级、降级、离线渲染都通过，模型与迁移一致。

### T3 仓储

- 写范围：`maa_api/db/repositories/ticket.py`（新）、`maa_api/db/repositories/agent.py`、`tests/fakes/v3_db.py`（新）、`tests/db/test_repositories_ticket.py`（新）
- 步骤：按 §4.2 实现五个仓储与 `get_active_orchestrator`；写 `tests/fakes/v3_db.py`（§6 开头）；写仓储测试。
- 验证：`.venv/bin/python -m pytest -q tests/db/test_repositories_ticket.py > /tmp/m69-t3.log 2>&1; echo $?; tail -n 5 /tmp/m69-t3.log` → 0。
- 完成判据：§6 中仓储测试全部通过；`MandateRepository` 上没有任何以 `update`、`delete`、`set` 开头的方法。

### T4 委托、汇报与话题键的纯函数

- 写范围：`maa_api/agent/tickets/__init__.py`、`mandates.py`、`reports.py`、`threads.py`（只写 `ThreadKey`、`parse_thread_key`、两张映射表与 `RouteResult`，`ThreadRouter` 留到 T5）、`tests/agent/tickets/__init__.py`、`tests/agent/tickets/conftest.py`、`test_ticket_mandates.py`、`test_ticket_reports.py`、`test_thread_keys.py`（均新）
- 步骤：按 §4.2 实现；测试见 §6。
- 验证：`.venv/bin/python -m pytest -q tests/agent/tickets > /tmp/m69-t4.log 2>&1; echo $?; tail -n 5 /tmp/m69-t4.log` → 0。
- 完成判据：三个测试文件通过；`policy_version()` 连续两次调用结果相同且是 12 位十六进制。

### T5 InboxService 与 ThreadRouter

- 写范围：`maa_api/agent/tickets/inbox.py`、`maa_api/agent/tickets/threads.py`、`tests/agent/tickets/test_inbox_service.py`、`tests/agent/tickets/test_thread_router.py`
- 步骤：按 §4.2、§4.5.4、§4.5.5 实现；测试见 §6。
- 验证：同 T4 的命令 → 0。
- 完成判据：并发领取用例（两个 `InboxService` 实例、各自的锁）连续跑 20 次都满足「两个结果不相交、并集等于全部 pending」。

### T6 TicketService

- 写范围：`maa_api/agent/tickets/service.py`、`tests/agent/tickets/test_ticket_service.py`
- 步骤：按 §4.2、§4.5.1–§4.5.3 实现；测试见 §6。
- 验证：同 T4 的命令 → 0。
- 完成判据：状态机表与边界情况表里涉及服务的每一行都有用例。

### T7 REST 端点

- 写范围：`maa_api/api/routers/tickets.py`、`maa_api/main.py`（只改 import 与 `include_router`）、`tests/api/test_tickets_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`
- 步骤：
  1. 按 §4.3 实现三个端点。
  2. `.venv/bin/python scripts/dump_openapi.py`，然后 `cd web && pnpm gen:api && cd ..`。
  3. `14-现行接口清单.md` §6 的 agent 小节追加三行（方法、路径、用途、请求要点、成功、主要错误码，与已有行同格式），该小节「（N 条）」加 3，§6 开头的总数句加 3。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/api/test_tickets_router.py tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py > /tmp/m69-t7.log 2>&1; echo $?; tail -n 5 /tmp/m69-t7.log
  .venv/bin/python scripts/dump_openapi.py --check; echo $?
  cd web && pnpm gen:api:check; echo $?; cd ..
  ```
  三个退出码都是 0。
- 完成判据：三个端点的形状进了 `web/openapi.json`，tag 数量仍是 19。

### T8 实时通道与前端

- 写范围：`maa_api/api/ws.py`、`tests/api/test_ws.py`、`web/src/api/keys.ts`、`web/src/realtime/events.ts`、`web/src/realtime/query-bridge.ts`、`web/src/realtime/query-bridge.test.ts`
- 步骤：按 §4.6 改；后端加 `test_ticket_status_channel_can_be_subscribed`，前端加 §6 的用例。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/api/test_ws.py > /tmp/m69-t8.log 2>&1; echo $?
  cd web && pnpm typecheck > /tmp/m69-tc.log 2>&1; echo $?; pnpm test --run > /tmp/m69-vitest.log 2>&1; echo $?; cd ..
  ```
  三个退出码都是 0。
- 完成判据：前端订阅里带 `ticket_status` 时后端回 `subscribed`；`ticket_status` 事件失效三个 query key。

### T9 保留策略、重启恢复与装配

- 写范围：`maa_api/services/retention_service.py`、`maa_api/agent/runs/recovery.py`、`maa_api/main.py`（lifespan）、`tests/services/test_retention_service.py`、`tests/agent/tickets/test_ticket_recovery.py`（新）、`tests/api/test_agent_assembly_wiring.py`
- 步骤：按 §4.5.6–§4.5.8 实现；测试见 §6。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/services/test_retention_service.py tests/agent/tickets tests/api/test_agent_assembly_wiring.py > /tmp/m69-t9.log 2>&1; echo $?; tail -n 5 /tmp/m69-t9.log
  ```
  退出码 0。
- 完成判据：真实 lifespan 里能拿到三个服务；恢复用例里被中断运行的工单变成 `blocked` 并有一条 `forwarded=false` 的汇报。

### T10 全量验证、文档与合并

- 写范围：`docs/v3/03-数据模型.md`（只在 §6.2 的 `source` 列宽经主 agent 同意改动之后，否则不动）、`docs/v3/ledgers/m69-ticket-domain.md`（新）
- 步骤：跑 §7 的全部命令；写台账；`git merge --no-ff` 回 `dev`，打 tag `v3-m69`。
- 验证：§7。
- 完成判据：[12 §4](../12-实施路线与里程碑.md) 的十项都满足。

## 6. 测试清单

`tests/fakes/v3_db.py` 提供：`make_session_factory(db_path) -> tuple[AsyncEngine, async_sessionmaker]`（`make_engine` + `NullPool` + `create_all`，与 `tests/db/conftest.py` 同口径）；`async seed_session(factory, *, kind="worker", status="active", ticket_id=None, archived=False) -> str`；`async seed_run(factory, *, session_id, ticket_id=None, status="running", boot_id="boot-test") -> str`（填齐 `agent_run` 的非空列：`root_run_id` 等于自身 id、`agent_name="test"`、`kind="worker_turn"`、`started_at=utcnow()`）。`tests/agent/tickets/conftest.py` 提供 fixture `v3_factory(tmp_path)`（用完 `engine.sync_engine.dispose()`）与 `spec_registry`（`get(name)` 返回 `SimpleNamespace(kind=..., risk_ceiling=...)`：`daily_ops` 为 worker/consume，`general` 为 worker/destructive，`researcher` 为 subagent/none，其他名字抛 `AppError(AGENT_SPEC_NOT_FOUND)`）。异步用例一律「同步测试函数 + `asyncio.run(scenario())`」。

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/domain/test_enums.py` | `test_ticket_status_is_terminal` | 只有 done、failed、cancelled 为终态 |
| `tests/db/test_tickets_migration.py` | `test_tickets_migration_adds_tables_and_foreign_key_columns` | 在 0025 上插一条 `agent_session`、`agent_run`、`agent_message` 后升级：五张表的列名、类型、可空与 §4.1 一致；三处新列为 NULL；外键 ondelete 与 §4.1 一致；`PRAGMA foreign_keys=ON` 下删 `ticket` 级联删掉 `mandate`、`ticket_report`、`thread_subscription`，并把 `agent_session.ticket_id` 置空 |
| 同上 | `test_tickets_migration_downgrade_restores_0025` | 降级后五张表与三列都不在，旧行完好，`alembic_version` 为 0025；再升级表为空 |
| `tests/db/test_repositories_ticket.py` | `test_mandate_repository_is_append_only` | `MandateRepository` 没有 update、delete、set 开头的公开方法；第二次给同一工单建委托违反 `uq_mandate_ticket_id` |
| 同上 | `test_ticket_list_filters_and_orders_by_updated_at` | 多状态过滤、kind、origin 过滤；`updated_at` 倒序，同刻按 id 倒序 |
| 同上 | `test_update_if_status_is_conditional`、`test_subscription_upsert_reactivates` | 状态不在 `allowed` 里时返回 False 且不改行；停用后再 upsert 变回 active、`ended_at` 为空，行数仍是 1 |
| 同上 | `test_inbox_mark_consumed_skips_non_pending`、`test_get_active_orchestrator_ignores_archived` | 已 consumed 的 id 不会被再次标记，rowcount 只算 pending；归档的与 finished 的总控会话不返回 |
| 同上 | `test_unforwarded_reports_follow_forwarding_rules` | progress 不返回；dropped 只在工单 origin=user 时返回；forwarded=true 不返回 |
| `tests/agent/tickets/test_thread_keys.py` | `test_valid_thread_keys_parse`、`test_invalid_thread_keys_raise` | 03 §15 的每种前缀至少一个正例；大写 UUID、未知前缀、超过 160 字、含空白的都 `INVALID_PARAMETER` |
| 同上 | `test_source_prefix_mapping_covers_every_event_source` | `SOURCE_PREFIXES` 的键集合等于 `EventSource` 全体 |
| `tests/agent/tickets/test_ticket_mandates.py` | `test_strength_and_ceiling_rules` | user→explicit→代理定义上限；其余来源→inferred→none |
| 同上 | `test_lower_ceiling_and_exceeds`、`test_atomic_grant_counts_as_consume` | 取最低；none 风险永不超限；consume 超 none、不超 consume；`ceiling_risk("none", "grant_atomic_ops") == consume`，其余动作原样返回 |
| 同上 | `test_build_mandate_validation` | scope 为空、超过 2000 字、source 与 origin 不匹配、trigger 缺 rule_id、user 缺 sender 都 `INVALID_PARAMETER` |
| `tests/agent/tickets/test_ticket_reports.py` | `test_status_mapping_table` | 六种汇报状态与 §4.2 映射逐一相等 |
| 同上 | `test_forwarding_rules` | 六种状态 × 五种来源的 `should_forward` 结果与 05 §4.1 一致 |
| 同上 | `test_parse_report_validation` | summary 为空或超过 600 字、waiting 缺 human_action、prompt 超过 200 字、details 超过 8 KiB 都报错；ready 带 human_action 被置空 |
| `tests/agent/tickets/test_inbox_service.py` | `test_claim_returns_pending_in_id_order_with_limit`、`test_claim_marks_consumed_with_run_id` | 写 5 条、`limit=3` 领到最早 3 条，再领得到剩下 2 条，第三次为空；领到的项 `status=consumed`、`consumed_run_id` 为传入的运行、`consumed_at` 非空 |
| 同上 | `test_concurrent_claims_never_share_items` | 两个 `InboxService` 实例对同一会话 `asyncio.gather` 各领 `limit=50`，循环 20 次：结果不相交、并集等于写入的全部项 |
| 同上 | `test_listener_called_after_commit` | 监听者被调用时用新会话能读到该项；监听者抛异常不影响 `put` 返回 |
| 同上 | `test_put_rejects_oversized_payload_and_bad_thread_key`、`test_drop_pending` | 超过 16 KiB 与非法话题键 `INVALID_PARAMETER`，表里没有新行；`drop_pending` 把 pending 全改 dropped、`consumed_at` 非空，已 consumed 的不变 |
| `tests/agent/tickets/test_thread_router.py` | `test_publish_routes_to_subscribed_workers` | 两个订阅工单各收到一条 thread_event，`routed_to="ticket"`，`ticket_ids` 顺序与订阅顺序一致 |
| 同上 | `test_publish_without_subscription_goes_to_orchestrator` | 交总控收件箱；没有总控会话时 `routed_to="none"` 且不写库 |
| 同上 | `test_ticket_thread_routes_to_its_worker_directly`、`test_ended_ticket_receives_nothing` | `ticket:<id>` 不需要订阅，工单已结束时交总控；工单结束后发布不再写进它的 Worker 收件箱 |
| 同上 | `test_subscribe_validation` | 非法键、`ticket:`/`chat:` 前缀、已结束工单、不存在的工单分别得到对应错误 |
| 同上 | `test_to_orchestrator_falls_back_from_archived_preferred` | preferred 已归档时写进当前活动的总控会话 |
| `tests/agent/tickets/test_ticket_service.py` | `test_create_writes_ticket_and_mandate_in_one_transaction` | 显式委托上限取代理定义；推断委托上限 none；`policy_version` 非空；广播一次 `ticket_created` |
| 同上 | `test_create_rejects_non_worker_kind` | `researcher` → `INVALID_PARAMETER`，未知名字 → `AGENT_SPEC_NOT_FOUND` |
| 同上 | `test_report_updates_ticket_per_mapping` | 六种汇报各自把工单改成映射状态，`summary`、`last_report_status`、`human_action` 按 §4.5.2 |
| 同上 | `test_report_forwarding_writes_orchestrator_inbox` | waiting、ready、blocked、failed 写总控收件箱（kind=report，payload 字段齐全）并 `forwarded=true`；progress 不写 |
| 同上 | `test_terminal_report_ends_subscriptions_and_drops_worker_inbox` | 订阅全停用，Worker 的 pending 项变 dropped，结束监听者收到一次 `TicketEnded` |
| 同上 | `test_report_on_ended_ticket_raises`、`test_concurrent_terminal_reports_end_once` | 已结束的工单 `TICKET_ENDED`，汇报数不变；两个 ready 并发，一个成功一个 `TICKET_ENDED`，监听者只调用一次 |
| 同上 | `test_cancel_writes_dropped_report` | 工单 cancelled；user 来源转发，trigger 来源不转发 |
| 同上 | `test_await_human_and_resolve` | `await_human` → waiting + human_action；ref_id 不同的 `human_resolved` 不改；相同的改回 open；终态工单两者都返回 None |
| 同上 | `test_attach_worker_session_puts_brief` | Worker 收件箱有一条 system 项，payload.event 为 `ticket_brief`；终态工单 `TICKET_ENDED` |
| 同上 | `test_forward_pending_reports_after_orchestrator_appears` | 先在没有总控时记 ready 汇报，再建总控会话并调用，返回 1，收件箱有该汇报 |
| `tests/agent/tickets/test_ticket_recovery.py` | `test_block_tickets_after_restart` | open、waiting 的工单变 blocked 并各插一条 forwarded=false 的汇报；done 的不变 |
| 同上 | `test_run_recovery_blocks_tickets_of_interrupted_runs` | 按 M66 测试里构造 `RunRecovery` 的写法：旧 boot 的运行带 ticket_id，`recover()` 后该工单 blocked |
| `tests/api/test_tickets_router.py` | `test_list_tickets_filters_and_paginates` | 重复 `status` 参数取并集；`size=0` → 400 `INVALID_PAGINATION`；未知 status → 422 |
| 同上 | `test_get_ticket_detail_shape` | 字段集合与 `TicketDetail` 一致；`recent_reports` 最多 5 条、id 倒序；时间以 `Z` 结尾 |
| 同上 | `test_get_ticket_not_found`、`test_list_ticket_reports_pages` | 404 且 `error.code == "TICKET_NOT_FOUND"`；汇报分页与 404 |
| 同上 | `test_ticket_operation_ids` | `app.openapi()` 里三条路径的 operationId 与 tag `agent` |
| `tests/api/test_ws.py` | `test_ticket_status_channel_can_be_subscribed` | 订阅 `["ticket_status"]` 收到 `subscribed`；`manager.broadcast("ticket_status", ...)` 只发给订阅了它的会话 |
| `tests/services/test_retention_service.py` | `test_agent_inbox_purged_after_30_days` | 31 天前消费的与丢弃的删掉；29 天前消费的、40 天前仍 pending 的保留；`out["agent_inbox_deleted"] == 2` |
| `tests/api/test_agent_assembly_wiring.py` | `test_lifespan_wires_ticket_services` | `app.state` 上三个服务的类型正确，`ticket_service` 持有同一个 `inbox_service` |
| `web/src/realtime/query-bridge.test.ts` | `invalidates ticket list, detail and reports on ticket_status` | 推进 300 ms 后 `invalidateQueries` 收到三个 key；不碰 `pendingConfirmations` |

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/m69-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m69-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m69-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m69-tc.log 2>&1 && pnpm test --run > /tmp/m69-vitest.log 2>&1 && pnpm build > /tmp/m69-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m69-fsmoke.log 2>&1; tail -n 1 /tmp/m69-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m69-asmoke.log 2>&1; tail -n 1 /tmp/m69-asmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m69-eval.log 2>&1; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
```

期望：pytest 与 dev 起点相比不新增失败；openapi 检查 0；前端链路 0；两个冒烟末行 `SMOKE OK`；评测退出码 0；断链 `BROKEN 0`；`git diff --check` 0。

本里程碑特有的手动验收：

1. 启动服务后 `curl -s localhost:<端口>/api/agent/tickets` 返回 `{"items": [], "total": 0, "page": 1, "size": 20}`（有 token 时带 `X-Token`）。
2. 浏览器打开任意页面，开发者工具的 WebSocket 帧里，订阅消息的 `channels` 含 `ticket_status`，服务端回 `subscribed`。

## 8. 风险与回滚

- 三张已有表的 batch 重建漏了列或索引：`test_initial_migration.py` 的列、索引、外键比对会红；按报错补 `copy_from` 的表定义。
- 循环外键（`ticket` 与 `agent_session` 互相引用）：SQLite 建表不校验引用方向，v2 已有 `agent_session`→`confirmation`→`agent_audit`→`agent_session` 的环，测试照常通过；`create_all` 的排序警告不影响结果。
- 回滚：`alembic downgrade 0025_trace_spans`（降级可逆，只删结构），再 `git revert -m 1 <merge>`。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：错误码 +2（总数句）、agent 小节路由 +3、§6 总数句 +3。
- `web/openapi.json`、`web/src/types/api.d.ts` 重新生成。
- 计数：业务表 +5；错误码 +2；HTTP 端点 +3；WebSocket 频道 +1；工具数不变。
- `docs/v3/03-数据模型.md` §6.2：`mandate.source` 的列宽按 §11 的申请由主 agent 改；本里程碑执行者不直接改 03。
- 有偏离时在 [13 §8](../13-决策记录.md) 补条目。

## 10. 停下来问

- 前置检查 5 发现 0023 没有用 `batch_alter_table` + `copy_from` 给已有表加外键列，且看不出它用了什么写法。
- 前置检查 6 的 `AgentSpecRegistry.get(name)` 不存在，或返回对象没有 `kind`、`risk_ceiling`。
- `RunRecovery.recover()` 里找不到「把运行改成 interrupted」的那一步，或它不在一个可以追加调用的事务里。
- 03 §6.2 仍写 `mandate.source str24`（本 spec §11 的申请没有被合并）。
- v2 已有与 `TICKET_NOT_FOUND`、`TICKET_ENDED` 同义的错误码。
- 加了 `ticket_status` 之后 v2 的 WebSocket 或前端实时测试出现与频道无关的失败。

## 11. 需要补进契约的名字

| 名字 | 用途 | 建议补进 |
|---|---|---|
| `mandate.source` 列宽改为 str16 | 与 v2 列宽约定一致（见 §4.1） | 03 §6.2 |
| `maa_api/db/repositories/ticket.py`：`TicketRepository`、`MandateRepository`、`TicketReportRepository`、`ThreadSubscriptionRepository`、`AgentInboxRepository` | 五张表的仓储 | 02 §4.1 |
| `AgentSessionRepository.get_active_orchestrator()` | 找活动的总控会话；M70 复用 | 02 §2.2 |
| `TicketService` 的方法：`create`、`attach_worker_session`、`get`、`detail`、`list`、`list_reports`、`record_report`、`cancel`、`await_human`、`human_resolved`、`forward_pending_reports`、`add_end_listener`；`TicketEnded`；`block_tickets_after_restart` | M70、M71 调用的服务接口 | 02 §2.2 |
| `ThreadRouter` 的方法：`subscribe`、`unsubscribe`、`publish`、`to_orchestrator`、`orchestrator_session_id`；`RouteResult`；`parse_thread_key`、`ThreadKey`、`THREAD_KEY_PATTERNS`、`SOURCE_PREFIXES` | 话题路由与校验 | 02 §2.2、03 §15 |
| `InboxService` 的方法：`put`、`claim`、`drop_pending`、`pending_count`、`add_listener` | 收件箱接口；监听者用于 M70 起运行 | 02 §2.2 |
| `mandates.py`：`RISK_ORDER`、`strength_for`、`ceiling_for`、`lower_ceiling`、`ceiling_risk`、`exceeds_ceiling`、`policy_version`、`build_mandate`、`ORIGIN_SOURCES` | 委托规则；M70 的 `MandateMiddleware` 复用 | 09 §2 |
| `reports.py`：`HumanAction`、`ReportInput`、`parse_report`、`ticket_status_for`、`should_forward`、`report_inbox_payload`、`REPORT_TO_TICKET_STATUS`、`FORWARDED_STATUSES` | 汇报契约的实现；M70 的 `report` 工具复用 `ReportInput` | 05 §4.1 |
| 收件箱 system 项 `payload.event = "ticket_brief"` | Worker 的任务说明 | 05 §2.4 |
| `TicketDetail` 的字段名 `recent_reports`、`result`；响应模型 `TicketSummary`、`HumanActionOut`、`MandateOut`、`SubscriptionOut`、`TicketReportOut`、`TicketPage`、`TicketReportPage` | 04 只写了「最近 5 条汇报」 | 04 §2.2 |
| 前端 `keys.agent.tickets`、`keys.agent.ticket`、`keys.agent.ticketReports`；类型 `TicketStatusEvent` | M72 页面复用 | 11 |
| `run_retention` 返回键 `agent_inbox_deleted`、常量 `INBOX_RETENTION_DAYS` | 保留策略 | 03 §14 |
| `tests/fakes/v3_db.py` | v3 测试共用的建库与造数 | 02 §4.3 |
