# M68 Trace 与评测底座（规格）

> 阶段：P0　依赖：M67　分支：`trace-evals`　迁移：`0025_trace_spans`
> 本文是 M68 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支与工作区 | `git status -sb` | `## trace-evals`，没有未提交改动 |
| 2 | M67 已合并 | `git tag -l 'v3-m67'` | `v3-m67` |
| 3 | 迁移 head | `ls maa_api/db/migrations/versions/ \| rg '^002[3-9]_'` | 只有 `0023_agent_runs.py`、`0024_model_profiles.py` |
| 4 | M65 的 Trace 空实现 | `rg -n "class TraceMiddleware" maa_api/agent/harness/builtin_middleware.py` | 1 行 |
| 5 | 核心类型 | `rg -n "^class (RunContext\|ModelRequest\|ModelResponse\|ToolCall\|ToolOutcome\|RunOutcome)\b" maa_api/agent/harness/context.py` | 6 行 |
| 6 | v2 门面走 AgentLoop | `rg -n "AgentLoop" maa_api/agent/runtime.py` | 至少 1 行 |
| 7 | 代理定义带版本哈希 | `rg -n "spec_version" maa_api/agent/specs/schema.py` | 至少 1 行 |
| 8 | 运行服务与端点 | `rg -n "class RunService" maa_api/agent/runs/service.py && rg -n "async def (list_runs\|get_run)\(" maa_api/api/routers/runs.py` | 1 行 + 2 行 |
| 9 | `agent_run` 表 | `rg -n '__tablename__ = "agent_run"' maa_api/db/models.py` | 1 行 |
| 10 | 模型层与注入点 | `rg -n "class OpenAICompatibleClient" maa_api/agent/llm/client.py && rg -n "client_factory_provider" maa_api/agent/llm/roles.py maa_api/main.py` | 前者 1 行，后者至少 2 行 |
| 11 | 用量、成本与档案 | `rg -n "^class Usage" maa_api/agent/llm/usage.py && rg -n "def compute_cost_micros" maa_api/agent/llm/cost.py && rg -n "class ResolvedProfile" maa_api/agent/llm/profiles.py && rg -n 'ctx.state\["model"\]' maa_api/agent/harness/builtin_middleware.py` | 每条至少 1 行 |
| 12 | 成本端点在 OpenAPI | `rg -n '"/api/agent/costs"' web/openapi.json` | 1 行 |
| 13 | 本里程碑的名字还不存在 | `rg -n "trace_span\|SpanKind\|list_run_spans\|agent_eval\|MAA_EXTRAS" maa_api scripts tests web/src Dockerfile` | 无输出 |
| 14 | v2 能力探查与 SSE 事件名仍在 | `rg -n '"(capability_probe\|tool_start\|confirmation_required\|question_required)"' maa_api/agent` | 四个名字都出现 |
| 15 | 前端已有运行与定义的类型 | `rg -n "RunSummary\|RunDetail\|SpecSummary" web/src/types/api.d.ts` | 三个名字都出现 |
| 16 | 现行接口清单 | `rg -n "^## 6\. 全量路由清单" docs/v3/14-现行接口清单.md` | 1 行 |
| 17 | 包管理形态与 extra 约定 | `rg -n "^\[project\]\|^\[tool\.poetry\]" pyproject.toml && rg -n "^## 12\. 可选依赖" docs/v3/16-部署指南.md` | 记下是哪一种（T7 按它写 extra）；16 有 §12 |
| 18 | 基线测试 | `.venv/bin/python -m pytest -q > /tmp/m68-base.log 2>&1; echo $?; tail -n 5 /tmp/m68-base.log` | 记下失败数写进台账 |

## 1. 背景与目标

v2 没有 trace、成本统计和评测集，模型行为的回归靠实机复盘（01 §1）。M68 补齐这三样，之后每个里程碑都用评测把关（12 §4 第 6 条）。

1. 每次运行写一个 `agent.run` span，每次模型调用一个 `llm.chat`，每次工具调用一个 `tool.execute`（委派是 `delegate.subagent`），等过确认的工具另有 `confirmation.wait`；名字与属性键按 10 §2。
2. span 由 `SpanRecorder` 在后台批量写入 `trace_span`；运行的关键路径上没有数据库写入，写入失败不影响运行。
3. `GET /api/agent/runs/{run_id}/spans` 返回整条 trace。
4. 配了 `trace.otlp_endpoint` 并安装 extra `otlp` 时，span 同步导出到 Langfuse 或 Phoenix。
5. `trace_span` 按 `trace.retention_days` 清理，接进 `run_retention`。
6. 前端「运行追踪」（`/more/runs`、`/more/runs/:runId`）与「成本」（`/more/costs`）两页可用。
7. `scripts/agent_eval.py` 在 replay 模式下跑完 12 个初始场景，regression 套件全部通过，结果写进 `resource/evals/`；record 与 live 模式在提供真实 key 时可用。
8. 台账记下两个套件的 replay 基线。

## 2. 非目标

- span 与 OTLP 导出都不带消息正文、推理内容、工具参数与结果；不做采样，不做 metrics 与 logs 信号，不用 Langfuse、Phoenix 的 SDK。
- 不实现 `orchestrator`、`ticket` 两个评测入口的驱动（M70 加），schema 先定下。
- 不改 v2 的 SSE 事件，不加 WebSocket 频道（运行追踪页靠轮询）；服务进程里不跑评测；Docker 镜像默认不装 extra。

## 3. 相关文档

- [02 §2.2、§4.1、§4.3、§5](../02-系统架构.md)；[03 §5、§13、§14](../03-数据模型.md)；[04 §2.1、§6](../04-接口与事件协议.md)；[05 §2、§5](../05-运行时与代理定义.md)；[10 全文](../10-可观测与评测.md)；[11 §2.2、§2.3、§9、§11](../11-前端指挥台.md)。
- 已写的前序 spec：[M64](./m64-engineering-baseline.md) §4.2.4 与 16 §12（extra 约定、`importorskip`、`MAA_EXTRAS`）；[M67](./m67-model-layer.md) §4.2（`Usage`、`ResolvedProfile`、`compute_cost_micros`、`ModelRouter` 的 `client_factory_provider`、`ctx.state["model"]`、`run_retention` 第 6 步）；[M72](./m72-console-frontend.md) §4.6.2（query key 形状）。
- v2 实机复盘（场景来源）：`rg -n "^### 8\.(7[5-9]|8[01]) " docs/v2/13-决策记录.md`，对应 [v2/13](../../v2/13-决策记录.md) 的 §8.75–8.81。
- 既有代码入口：
  - `scripts/agent_smoke.py`：`_SmokeCoreSupervisor`（约 146 行）、`_SmokeCoreClient`（约 178）、`_SmokeDeviceManager`（约 227）、`_SmokePipelineRunner`（约 257）、`run()` 里的隔离步骤（约 624–716 行，收尾在约 1000 行后）。
  - `maa_api/main.py`：`create_app()`（约 1501）；lifespan 的 `app.state.*_factory` 读取点（约 639、647、658、699）；正常收尾段（`yield` 之后，约 1423）与启动失败收尾段（约 1355），两段都在 `await stop_logging(tailer_task, hub)` 前结束服务。
  - `maa_api/agent/runtime.py`：`acquire_session`（约 613）、`release_session`（620）、`interrupt_turn`（634）、`ensure_configured`（705）、`stream_message`（718）、`QUESTION_EXPIRED_TEXT`（101）、`STUCK_REPEAT_LIMIT`（124）、`INTERRUPTED_TOOL_STUB`（约 168）。调用顺序照 `maa_api/api/routers/agent.py` 的 `stream_session_message`（约 733）。
  - `maa_api/agent/confirmation.py` 的 `ConfirmationService.resolve(confirmation_id, *, approved, resolved_by, reason=None, ...)`（约 259）；`maa_api/agent/questions.py` 的 `QuestionService.answer(question_id, *, selected, answered_by)`（约 206）、`skip(question_id, *, answered_by)`（约 252）。
  - `maa_api/services/external/http.py` 的 `_DEFAULT`（约 525）、`default_external_client()`、`reset_default_external_client()`；`maa_api/services/device_service.py` 的 `ScreenshotResult(image, backend)`（约 79）、`DeviceManager.snapshot()`（约 432）。
  - `maa_api/services/retention_service.py` 的 `run_retention`（约 143）；分批删除照 `maa_api/db/repositories/log.py` 的 `LogRepository.purge`（约 233）。
  - `tests/fakes/fake_asst.py` 的 `FakeAsst`（约 192）与 `SCRIPTS`（约 169）；`tests/fakes/fake_llm.py` 的 `text_response`、`tool_call`、`tool_response`（约 80–125，录制文件简写的展开照它们的分片形状）。
  - 前端：`web/src/features/audit/AuditPage.tsx`（列表页写法）、`web/src/features/agent/chart/EChartSurface.tsx`（约 49，图表）、`web/src/features/settings/use-settings.ts` 的 `settingsQueryOptions`、`web/src/components/layout/nav.ts` 的 `secondaryNav`、`web/src/routes/router.tsx` 的 `routeConfig`、`web/src/api/keys.ts`。

## 4. 设计

### 4.1 数据模型

`maa_api/domain/enums.py` 加：

```python
class SpanKind(StrEnum):
    """``trace_span.kind``（v3 03 §5.1、10 §2.2）。"""

    LLM = "llm"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    POLICY = "policy"
    CONFIRMATION = "confirmation"
    MEMORY = "memory"
    COMPACTION = "compaction"
    LEASE = "lease"
    DELEGATE = "delegate"
    OTHER = "other"
```

`maa_api/db/models.py` 加（文件头的表数 +1）：

```python
class TraceSpan(SQLModel, table=True):
    """运行追踪的一个 span（v3 03 §5.1）；结束时整行写入，属性键见 v3 10 §2。"""

    __tablename__ = "trace_span"

    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(max_length=36)
    span_id: str = Field(max_length=16)
    parent_span_id: str | None = Field(default=None, max_length=16)
    run_id: str | None = Field(default=None, foreign_key="agent_run.id", ondelete="CASCADE", max_length=36)
    name: str = Field(max_length=64)
    kind: str = Field(max_length=16)
    status: str = Field(max_length=16)
    attributes: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("attributes", nullable=False, server_default="'{}'")
    )
    started_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("started_at"))
    ended_at: datetime | None = Field(default=None, sa_column=datetime_column("ended_at", nullable=True))
    duration_ms: int | None = Field(default=None)

    __table_args__ = (
        UniqueConstraint("trace_id", "span_id", name="uq_trace_span_trace_id_span_id"),
        Index("ix_trace_span_run_id", "run_id"),
        Index("ix_trace_span_started_at", "started_at"),
        {"sqlite_autoincrement": True},
    )
```

迁移 `maa_api/db/migrations/versions/0025_trace_spans.py`（`revision="0025_trace_spans"`，`down_revision="0024_model_profiles"`；实际编号取下一个可用号，台账记差异）。`upgrade()`：`op.create_table("trace_span", …)`，列与上面一一对应（`sa.Integer`、`sa.String(length=N)`、`sa.JSON()` 带 `server_default=sa.text("'{}'")`、`sa.DateTime()`），外键 `sa.ForeignKeyConstraint(["run_id"], ["agent_run.id"], name="fk_trace_span_run_id_agent_run", ondelete="CASCADE")`，`sa.PrimaryKeyConstraint("id", name="pk_trace_span")`，`sa.UniqueConstraint("trace_id", "span_id", name="uq_trace_span_trace_id_span_id")`，`sqlite_autoincrement=True`；然后两条 `op.create_index`。`downgrade()`：删两个索引，再删表。写法照 `0015_agent_question.py` 与 `0008_agent_invoke_idempotency.py`（autoincrement 的位置）。

`tests/db/test_models.py`：`BUSINESS_TABLES` 加 `"trace_span"`；`AUTOINCREMENT_TABLES` 加 `"trace_span"`；`EXPECTED_INDEXES` 加 `"ix_trace_span_run_id": ("trace_span", ("run_id",), False)`、`"ix_trace_span_started_at": ("trace_span", ("started_at",), False)`；`EXPECTED_UNIQUE_CONSTRAINTS` 加 `"uq_trace_span_trace_id_span_id": ("trace_span", ("trace_id", "span_id"))`；`EXPECTED_FK_CASCADE` 加 `("trace_span", "run_id")`；`EXPECTED_JSON_COLUMNS` 加 `("trace_span", "attributes")`。`tests/domain/test_enums.py` 的 `EXPECTED_VALUES` 加 `SpanKind` 整表。

### 4.2 模块与文件

#### 4.2.1 `maa_api/agent/trace/spans.py`（新建；`trace/__init__.py` 重新导出下列公开名字）

```python
FLUSH_INTERVAL_SECONDS = 0.5; FLUSH_BATCH_SIZE = 200; MAX_PENDING_SPANS = 10_000
MAX_WRITE_ATTEMPTS = 3; CLOSE_TIMEOUT_SECONDS = 5.0
MAX_ATTRIBUTES = 48; MAX_ATTRIBUTE_CHARS = 256; MAX_LIST_ITEMS = 16
DELEGATE_TOOLS = frozenset({"delegate", "delegate_recon"})

SpanStatus = Literal["ok", "error"]
AttributeValue = str | int | float | bool | list[str]

def new_span_id() -> str: ...  # secrets.token_hex(8)
def clean_attributes(values: Mapping[str, Any]) -> dict[str, AttributeValue]: ...
    # 去掉 None；bool/int/float/str 原样；其他标量转 str；序列转 list[str] 取前 16 项；
    # 字符串超过 256 字截成 255 字加「…」；超过 48 个键按插入顺序丢弃

@dataclass(slots=True)
class SpanRecord:
    trace_id: str; span_id: str; parent_span_id: str | None; run_id: str | None
    name: str; kind: SpanKind; status: SpanStatus; attributes: dict[str, AttributeValue]
    started_at: datetime; ended_at: datetime; duration_ms: int   # 时间都是 naive UTC

class SpanHandle:
    """进行中的 span。recorder 为 None 时是空操作句柄（追踪关闭时返回它）。"""
    trace_id: str; span_id: str; parent_span_id: str | None; run_id: str | None
    name: str; kind: SpanKind; started_at: datetime
    def set_attribute(self, key: str, value: Any) -> None: ...
    def set_attributes(self, values: Mapping[str, Any]) -> None: ...
    def end(self, *, status: SpanStatus = "ok", attributes: Mapping[str, Any] | None = None) -> None: ...
        # 幂等；时长取 time.monotonic() 差值，ended_at = started_at + 时长；交给 recorder.record()
    @property
    def ended(self) -> bool: ...

def current_span() -> SpanHandle | None: ...                 # 模块级 ContextVar "maa_current_span"
def use_span(handle: SpanHandle) -> contextvars.Token: ...
def reset_span(token: contextvars.Token) -> None: ...

class SpanRecorder:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        exporter: OtlpExporter | None = None,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
        batch_size: int = FLUSH_BATCH_SIZE,
        max_pending: int = MAX_PENDING_SPANS,
    ) -> None: ...
    @property
    def enabled(self) -> bool: ...  # 每次现读 settings.trace.enabled
    def start_span(self, name: str, kind: SpanKind, *, trace_id: str, run_id: str | None,
                   parent: SpanHandle | None = None,
                   attributes: Mapping[str, Any] | None = None) -> SpanHandle: ...
    def record_span(self, name: str, kind: SpanKind, *, trace_id: str, run_id: str | None,
                    parent_span_id: str | None, started_at: datetime, ended_at: datetime,
                    status: SpanStatus = "ok", attributes: Mapping[str, Any] | None = None) -> None: ...
    def record(self, record: SpanRecord) -> None: ...   # 同步入队，不 await；满了丢弃并计数
    def spawn(self, coro: Coroutine[Any, Any, None]) -> None: ...  # 派生 span 的后台小任务，持有引用，异常只记日志
    async def start(self) -> None: ...   # 起写库任务与导出任务；重复调用无副作用
    async def flush(self) -> None: ...   # 立即写完队列（测试与评测收集结果前用）
    async def close(self) -> None: ...   # flush（最多 CLOSE_TIMEOUT_SECONDS）后停任务，exporter.shutdown()
    def stats(self) -> dict[str, int]: ...  # pending、written、dropped、write_errors、exported、export_dropped
```

#### 4.2.2 `TraceMiddleware`（改 `maa_api/agent/harness/builtin_middleware.py` 里 M65 的空实现）

`name = "trace"`，`applies_to` 为全部四种代理。服务从 `ctx.app.state.span_recorder` 取；取不到或 `enabled` 为假时每个钩子直接放行。

```
on_run_start(ctx):
    parent = current_span(); 若 parent 不为空且 parent.trace_id != ctx.root_run_id：parent = None
    h = rec.start_span("agent.run", OTHER, trace_id=ctx.root_run_id, run_id=ctx.run_id, parent=parent,
            attributes={gen_ai.operation.name: invoke_agent, gen_ai.agent.name: spec.name, maa.agent.name: spec.name,
                        maa.agent.kind: spec.kind, maa.spec.version: spec.spec_version,
                        gen_ai.conversation.id: ctx.session_id, maa.ticket.id: ctx.ticket_id})
    ctx.state["trace.run_span"] = h；ctx.state["trace.token"] = use_span(h)
before_model(ctx, req):
    profile = ctx.state["model"]["profile"]（M67 的 ContextLayoutMiddleware 在 on_run_start 放进去的 ResolvedProfile）
    ctx.state["trace.llm"] = {开始墙钟, profile, response_format 是否为空,
                              schema 名（req.response_format["json_schema"]["name"]，没有就不写）}
    return req  # 不改请求
after_model(ctx, resp):
    p = ctx.state.pop("trace.llm")；结束 = utcnow()；开始 = 结束 - resp.latency_ms（latency_ms 为空时用 p 的开始墙钟）
    rec.record_span("llm.chat", LLM, parent_span_id=run_span.span_id, attributes=10 §2.4/§2.5 的 llm 键：
            档案取 profile.name、provider、base_url（只要主机与端口）、model、max_output_tokens；
            用量取 resp.usage 的 prompt/completion/cached/reasoning_tokens；
            maa.cost.micros = compute_cost_micros(resp.usage, profile)（与 CostLedger 同一个函数），maa.cost.currency = profile.currency；
            gen_ai.response.model=resp.model、gen_ai.response.id=resp.response_id、
            gen_ai.response.finish_reasons=[resp.upstream_finish_reason]、maa.llm.latency_ms=resp.latency_ms)
    return resp
wrap_tool(ctx, call, call_next):
    (name, kind) = ("delegate.subagent", DELEGATE) 若 call.name in DELEGATE_TOOLS 否则 ("tool.execute", TOOL)
    h = rec.start_span(name, kind, parent=run_span, attributes={execute_tool, gen_ai.tool.name, gen_ai.tool.call.id: call.id,
                                                              gen_ai.tool.type: function, maa.agent.name})
    token = use_span(h)
    try: outcome = await call_next(call)
    except BaseException as exc: h.end(status="error", attributes={"error.type": "interrupted" 若是 CancelledError 否则类名}); raise
    finally: reset_span(token)
    h.end(status = "error" 若 outcome.status == "error" 否则 "ok",
          attributes={maa.tool.status, maa.audit.id: outcome.audit_id, error.type: outcome.error.code（有错误时）,
                      maa.question.id: outcome.result["question_id"]（ask_user 且有值）,
                      maa.subagent.name: call.arguments.get("agent")、maa.subagent.run_id: outcome.result["run_id"]（委派）})
    若 outcome.audit_id 不为空且 outcome.status in {ok, error, rejected, expired}：
        rec.spawn(_derive_confirmation_span(rec, ctx, audit_id, parent=h))
    return outcome
on_run_end(ctx, outcome):
    若 ctx.state 里还有 "trace.llm"（模型调用抛错）：补记一个 status=error 的 llm.chat，error.type 取 outcome.finish_reason
    h.end(status = "error" 若 outcome.status == failed 否则 "ok",
          attributes={maa.run.status, maa.run.finish_reason, maa.run.steps: ctx.steps, 累计用量与成本（outcome.usage），
                      error.type: outcome.finish_reason（失败时）})
    reset_span(ctx.state.pop("trace.token"))
```

`_derive_confirmation_span` 在后台用 `app.state.session_factory` 查 `SELECT c.id, c.status, c.created_at, c.resolved_at, c.expires_at FROM agent_audit a JOIN confirmation c ON c.id = a.confirmation_id WHERE a.id = :audit_id`；查到就 `record_span("confirmation.wait", CONFIRMATION, started_at=c.created_at, ended_at=c.resolved_at 或 c.expires_at, parent_span_id=h.span_id, attributes={maa.confirmation.id, maa.confirmation.status, maa.audit.id})`，查不到不记。`gen_ai.system` 的判定按 10 §2.4，写成模块函数 `gen_ai_system(provider: str, base_url: str | None) -> str`。

#### 4.2.3 其他运行时改动

- `maa_api/agent/harness/context.py`：`ModelResponse` 末尾加两个带默认值的字段 `model: str | None = None`、`response_id: str | None = None`。
- `maa_api/agent/harness/chunks.py`：解析流式分片时，取第一个带字符串 `model` 的分片的值填 `model`，第一个带字符串 `id` 的分片的值填 `response_id`。
- 评测接模型不改模型层：沿用 v2 的 `AgentRuntime.client_factory` 注入点（`scripts/agent_smoke.py` 约 722 行的做法）。M67 的 `ModelRouter` 与 `ModelProfileService` 经 `client_factory_provider` 现读这个工厂，旧会话、总控、Worker、能力探查与档案探查的请求都走它（§4.5.4 第 5 步）。

#### 4.2.4 `maa_api/agent/trace/otlp.py`（新建）

```python
OTLP_EXTRA = "otlp"
EXPORT_TIMEOUT_SECONDS = 10.0
MAX_EXPORT_PENDING = 10_000

class OtlpUnavailable(RuntimeError): ...
def parse_otlp_headers(raw: str) -> dict[str, str]: ...
    # 按逗号拆项、每项按第一个 "=" 拆键值、去空白、值做 urllib.parse.unquote；空串返回 {}；缺 "=" 的项抛 ValueError
def otlp_trace_id(trace_id: str) -> int: ...    # uuid.UUID(trace_id).int
def otlp_span_name(record: SpanRecord) -> str: ...   # 10 §5.2 的导出名
def backend_attributes(record: SpanRecord) -> dict[str, AttributeValue]: ...
    # gen_ai.provider.name、langfuse.observation.type、openinference.span.kind、session.id、langfuse.trace.name（10 §5.2）
class OtlpExporter:
    def __init__(self, endpoint: str, headers: Mapping[str, str], *, service_version: str,
                 timeout_seconds: float = EXPORT_TIMEOUT_SECONDS) -> None: ...
        # 在这里 import opentelemetry；ImportError 转成 OtlpUnavailable
    def export(self, records: Sequence[SpanRecord]) -> bool: ...   # 同步；由 SpanRecorder 用 asyncio.to_thread 调
    def shutdown(self) -> None: ...
def build_exporter(settings: Settings, *, service_version: str) -> OtlpExporter | None: ...
    # endpoint 为空返回 None；OtlpUnavailable 记一条警告后返回 None
```

`export` 把每条 `SpanRecord` 构造成 `opentelemetry.sdk.trace.ReadableSpan`：`context=SpanContext(trace_id=otlp_trace_id(...), span_id=int(span_id, 16), is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED))`，`parent` 同理（无上级为 None），`attributes` 为库里属性加 `backend_attributes`，`kind` 按 10 §5.2，`status` 为 `Status(StatusCode.ERROR, error.type)` 或 `Status(StatusCode.UNSET)`，`start_time`、`end_time` 为纳秒，`resource=Resource.create({"service.name": "maa-api", "service.version": service_version})`，`instrumentation_scope=InstrumentationScope("maa_api.agent.trace")`；交给 `OTLPSpanExporter(endpoint=..., headers=..., timeout=...)`，返回值是否为 `SpanExportResult.SUCCESS`。

#### 4.2.5 仓储、保留、装配

- `maa_api/db/repositories/trace.py`（新建）：`TRACE_PURGE_BATCH_SIZE = 5000`；`TraceSpanRepository(BaseRepository)` 的 `add_many(rows: Sequence[TraceSpan]) -> None`（`add_all` + `flush`，不提交）、`list_by_trace(trace_id: str, *, limit: int) -> list[TraceSpan]`（按 `started_at, id` 升序）、`purge_before(cutoff: datetime) -> int`（删一批 `started_at < cutoff` 的行并提交，子查询 `ORDER BY id LIMIT 5000`，照 `LogRepository._delete_old_batch`）。
- `maa_api/services/retention_service.py`：`run_retention` 的结果字典加 `"trace_spans_deleted": 0`，在 M67 加的第 6 步（成本账本，`cost_ledger_deleted`）之后加第 7 步：按 `trace.retention_days` 算截止时间，循环 `purge_before` 直到返回值小于批量，批与批之间 `await asyncio.sleep(BATCH_PAUSE_SECONDS)`，最后 `_incremental_vacuum(db)`；docstring 补「M68 起多一个 `trace_spans_deleted`」。读天数的函数 `_trace_retention_days()` 照 `_copilot_retention_days()` 写，非法时退回 14。
- `maa_api/main.py`：按 02 §5 第 5 步，在 `cost_ledger` 之后 `span_recorder = SpanRecorder(db_session.session_factory, exporter=build_exporter(get_settings(), service_version=<服务版本>))`、`await span_recorder.start()`、`app.state.span_recorder = span_recorder`。服务版本取 `get_system_status` 用的同一个来源（`maa_api/agent/tools/status.py` 的 `_service_version()` 所读的值）。两个收尾段都在 `await stop_logging(...)` 之前 `await span_recorder.close()`（启动失败段里先判断它已创建），并把 `"span_recorder"` 加进收尾时删除的 `app.state` 属性清单。

#### 4.2.6 评测模块 `maa_api/agent/evals/`（新建，`__init__.py` 重新导出）

`scenario.py`：

```python
SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
Suite = Literal["regression", "capability"]
EntryKind = Literal["legacy_session", "orchestrator", "ticket"]

class ScenarioError(ValueError): ...   # 消息里带文件路径
class Entry(BaseModel): kind: EntryKind; worker: str | None = None; title: str | None = None; brief: str | None = None; scope: str | None = None
class AnswerSpec(BaseModel): answer: list[str]           # 1–6 项
class InterruptSpec(BaseModel): on: Literal["tool_start", "tool_result"]; tool: str; delay_seconds: float = 0.5   # 0–60
class HumanPolicy(BaseModel):
    confirmations: Literal["approve", "reject", "ignore"] = "reject"
    questions: Literal["ignore", "skip"] | AnswerSpec = "ignore"
    interrupt: InterruptSpec | None = None
class Turn(BaseModel): user: str; human: HumanPolicy = HumanPolicy()   # user 1–32000 字
class HttpFixture(BaseModel): match: str; status: int = 200; json_body: Any = Field(default=None, alias="json"); body_file: str | None = None
class SeedTask(BaseModel): type_name: str; status: str = "completed"; params: dict[str, Any] = {}; error_code: str | None = None; error_message: str | None = None
class SeedLog(BaseModel): task: int = 0; level: Literal["debug", "info", "warning", "error", "critical"] = "info"; content: str
class SeedPipeline(BaseModel): title: str | None = None; status: Literal["completed", "failed", "cancelled"]; minutes_ago: int = 10; error_code: str | None = None; error_message: str | None = None; tasks: list[SeedTask]; logs: list[SeedLog] = []
class WorldSeed(BaseModel): pipelines: list[SeedPipeline] = []
class World(BaseModel): core_script: Literal["success", "failure", "stuck", "disconnect"] = "success"; device_connected: bool = True; screen: Literal["home", "desktop", "stage", "battle", "settlement"] = "home"; http: list[HttpFixture] = []; seed: WorldSeed = WorldSeed()
class Limits(BaseModel): timeout_seconds: float = 60; max_model_calls: int = 30   # 1–900、1–500
class ToolStep(BaseModel): tool: str; args: Any = None; status: Literal["ok", "error"] | None = None; error_code: str | None = None; next: bool = False
class ToolSequenceSpec(BaseModel): grader: Literal["tool_sequence"]; match: Literal["subsequence", "exact"] = "subsequence"; steps: list[ToolStep] = []; forbid: list[str] = []; max_calls: dict[str, int] = {}; min_calls: dict[str, int] = {}
class OutcomeStateSpec(BaseModel): grader: Literal["outcome_state"]; runs: Any = None; pipelines: Any = None; confirmations: Any = None; questions: Any = None; messages: Any = None; model_calls: Any = None; requests: Any = None; spans: Any = None
class TextContainsSpec(BaseModel): grader: Literal["text_contains"]; target: Literal["final", "all"] = "final"; all_of: list[str] = []; any_of: list[str] = []; none_of: list[str] = []; ignore_case: bool = True
class LlmJudgeSpec(BaseModel): grader: Literal["llm_judge"]; rubric: str; pass_score: float = 0.7
GraderSpec = Annotated[ToolSequenceSpec | OutcomeStateSpec | TextContainsSpec | LlmJudgeSpec, Field(discriminator="grader")]
class Scenario(BaseModel):
    id: str; title: str; suite: Suite; agent: str; source: str | None = None; tags: list[str] = []
    entry: Entry; settings: dict[str, Any] = {}; world: World = World(); turns: list[Turn] = []
    expect: list[GraderSpec]; cassette: str | None = None; limits: Limits = Limits()
    def cassette_name(self) -> str: ...   # cassette 或 f"{id}.jsonl"

def load_scenario(path: Path) -> Scenario: ...
def load_scenarios(directory: Path, *, suites: Collection[Suite] | None = None, ids: Collection[str] = (),
                   agent: str | None = None, tags: Collection[str] = ()) -> list[Scenario]: ...
```

全部模型 `extra="forbid"`。校验：`id` 匹配正则且等于文件名主干；`settings` 的键都在 `maa_api.settings.SETTING_KEYS` 里；`suite=regression` 时不能有 `llm_judge`；`entry.kind` 为 `legacy_session`、`orchestrator` 时 `turns` 至少 1 条，为 `ticket` 时四个字段必填；`HttpFixture` 的 `json` 与 `body_file` 恰好一个；同目录 `id` 重复报错。YAML 用 `ruamel.yaml.YAML(typ="safe", pure=True)` 读（仓库已有依赖，不加 PyYAML）；结果按 `id` 排序。

`cassette.py`：

```python
CASSETTE_FORMAT = 1
Channel = Literal["agent", "aux", "probe", "judge"]
PLACEHOLDER_RE = re.compile(r"\{\{result:([^:}]+):([^}]+)\}\}")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DEFAULT_AUX_TEXT = "评测会话"
VISION_PROBE_REPLY = "blue"   # 与 runtime._VISION_PROBE_MARKER 一致

class CassetteError(RuntimeError):
    code: Literal["missing", "format", "mismatch", "exhausted", "unused", "placeholder", "limit"]
@dataclass class CassetteHeader: scenario: str; source: Literal["handwritten", "recorded"]; recorded_at: str; model: str; agent: str; spec_version: str | None = None; notes: str = ""; format: int = CASSETTE_FORMAT
@dataclass class RequestFingerprint: model: str; roles: list[str]; tool_call_ids: list[str]; tools_sha: str; system_sha: str
@dataclass class CassetteCall: channel: Channel; request: RequestFingerprint | None = None; reply: dict[str, Any] | None = None; chunks: list[dict[str, Any]] | None = None; error: dict[str, Any] | None = None
@dataclass class CapturedRequest: channel: Channel; fingerprint: RequestFingerprint; reasoning_messages: int; chars: int; tool_count: int

class Cassette:
    header: CassetteHeader; calls: list[CassetteCall]
    @classmethod
    def load(cls, path: Path) -> Cassette: ...
    @classmethod
    def new(cls, header: CassetteHeader) -> Cassette: ...
    def save(self, path: Path) -> None: ...
    def pop(self, channel: Channel) -> CassetteCall | None: ...
    def remaining(self, channel: Channel) -> int: ...
    def append(self, call: CassetteCall) -> None: ...

def classify_request(body: Mapping[str, Any]) -> Channel: ...        # probe / aux / agent，规则见 §4.5.3
def fingerprint_request(body: Mapping[str, Any]) -> RequestFingerprint: ...
def summarize_request(body: Mapping[str, Any], channel: Channel) -> CapturedRequest: ...
def expand_reply(reply: Mapping[str, Any], *, model: str, seq: int) -> list[dict[str, Any]]: ...
def render_placeholders(reply: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]: ...
def templatize_chunks(chunks: list[dict[str, Any]], messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]: ...
def sse_body(chunks: Sequence[Mapping[str, Any]]) -> bytes: ...
def parse_sse_body(body: bytes) -> list[dict[str, Any]]: ...

class CassetteTransport(httpx.AsyncBaseTransport):     # replay
    def __init__(self, cassette: Cassette, *, max_calls: int, strict: bool = False, channel_override: Channel | None = None) -> None: ...
    requests: list[CapturedRequest]; warnings: list[str]; agent_calls: int
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response: ...
    def check_unused(self) -> None: ...                 # agent 通道有剩余时抛 CassetteError("unused")
class RecordingTransport(httpx.AsyncBaseTransport):    # record：转发给 inner（默认 httpx.AsyncHTTPTransport()），写进 cassette
    def __init__(self, cassette: Cassette, *, max_calls: int, inner: httpx.AsyncBaseTransport | None = None, channel_override: Channel | None = None) -> None: ...
class CountingTransport(httpx.AsyncBaseTransport):     # live：转发、计数、记请求摘要
    def __init__(self, *, max_calls: int, inner: httpx.AsyncBaseTransport | None = None, channel_override: Channel | None = None) -> None: ...
```

`graders.py`：

```python
GradeStatus = Literal["passed", "failed", "skipped", "error"]
OPERATORS = frozenset({"absent", "not", "re", "contains", "gte", "lte", "gt", "lt", "in", "len", "first", "last", "any", "all"})
@dataclass class GradeResult: grader: str; status: GradeStatus; score: float | None = None; details: list[str] = field(default_factory=list)
@dataclass class ObservedToolCall: id: str; name: str; arguments: dict[str, Any]; status: Literal["ok", "error"]; error_code: str | None; result: Any
@dataclass class CaseObservation:
    turns: list[str]; tool_calls: list[ObservedToolCall]; final_text: str; all_text: str
    messages: list[dict[str, Any]]; runs: list[dict[str, Any]]; pipelines: list[dict[str, Any]]
    confirmations: list[dict[str, Any]]; questions: list[dict[str, Any]]; spans: list[dict[str, Any]]
    requests: list[CapturedRequest]; events: list[tuple[str, dict[str, Any]]]; model_calls: int
@dataclass class JudgeContext: client_factory: Callable[[], Any] | None; model: str | None
JUDGE_SYSTEM_PROMPT: str   # 见 §4.5.6
def match_value(expected: Any, actual: Any, *, path: str = "$") -> list[str]: ...   # 返回不匹配的描述，空列表表示匹配
def outcome_view(obs: CaseObservation) -> dict[str, Any]: ...
def grade_tool_sequence(spec: ToolSequenceSpec, obs: CaseObservation) -> GradeResult: ...
def grade_outcome_state(spec: OutcomeStateSpec, obs: CaseObservation) -> GradeResult: ...
def grade_text_contains(spec: TextContainsSpec, obs: CaseObservation) -> GradeResult: ...
async def grade_llm_judge(spec: LlmJudgeSpec, obs: CaseObservation, judge: JudgeContext) -> GradeResult: ...
async def grade_case(scenario: Scenario, obs: CaseObservation, judge: JudgeContext) -> list[GradeResult]: ...
def case_status(results: Sequence[GradeResult]) -> Literal["passed", "failed", "error", "skipped"]: ...
```

`runner.py`：

```python
EvalMode = Literal["replay", "record", "live"]
RESULT_FORMAT = 1
INDEX_FILE_NAME = "index.jsonl"
@dataclass(frozen=True) class LlmEndpoint: base_url: str; api_key: str; model: str
REPLAY_ENDPOINT = LlmEndpoint("https://llm.invalid/v1", "eval-replay", "eval-model")
@dataclass(frozen=True) class EvalApp: app: Any; client: Any; portal: Any; token: str; workdir: Path
class EvalEnvironment(Protocol):
    def open(self, scenario: Scenario, *, llm: LlmEndpoint, workdir: Path,
             transport: httpx.AsyncBaseTransport) -> AbstractContextManager[EvalApp]: ...
@dataclass(frozen=True) class EvalVariant: name: str = "target"; setup: Callable[[EvalApp], Awaitable[None]] | None = None
@dataclass class EvalOptions:
    mode: EvalMode; cassette_dir: Path; out_dir: Path; real_llm: LlmEndpoint | None = None
    judge_model: str | None = None; repeat: int = 1; strict: bool = False; keep_temp: bool = False
    variants: Sequence[EvalVariant] = (EvalVariant(),)
@dataclass class CaseResult:
    scenario: str; suite: Suite; agent: str; attempt: int; variant: str
    status: Literal["passed", "failed", "error", "skipped"]; graders: list[GradeResult]
    duration_ms: int; model_calls: int; usage: dict[str, Any]; run_ids: list[str]
    warnings: list[str]; error: str | None; detail: dict[str, Any]   # detail 只写进 cases/ 文件
@dataclass class SuiteResult:
    id: str; suite: str; mode: EvalMode; started_at: str; finished_at: str; git: dict[str, Any] | None
    model: dict[str, Any]; repeat: int; filters: dict[str, Any]; target: dict[str, Any] | None
    baseline: dict[str, Any] | None; cases: list[CaseResult]
    def summary(self, variant: str = "target") -> dict[str, Any]: ...
    def to_json(self) -> dict[str, Any]: ...
class CaseRecorder: events: list[tuple[str, dict[str, Any]]]; session_ids: list[str]
DriveFn = Callable[[EvalApp, Scenario, CaseRecorder], Awaitable[None]]
async def drive_legacy_session(eval_app: EvalApp, scenario: Scenario, recorder: CaseRecorder) -> None: ...
DRIVERS: dict[str, DriveFn] = {"legacy_session": drive_legacy_session}
async def collect_observation(eval_app: EvalApp, recorder: CaseRecorder, requests: Sequence[CapturedRequest]) -> CaseObservation: ...
class EvalRunner:
    def __init__(self, environment: EvalEnvironment, options: EvalOptions) -> None: ...
    def run(self, scenarios: Sequence[Scenario], *, suite_label: str, filters: Mapping[str, Any]) -> SuiteResult: ...
    def run_case(self, scenario: Scenario, *, attempt: int, variant: EvalVariant) -> CaseResult: ...
def write_result(result: SuiteResult, out_dir: Path) -> Path: ...    # 返回 result.json 路径
def git_info(repo_root: Path) -> dict[str, Any] | None: ...          # {"commit": 短哈希, "dirty": bool}；没有 git 返回 None
```

#### 4.2.7 假世界（测试目录，不进运行镜像）

`tests/fakes/fake_core.py`（新建，四个类照 `scripts/agent_smoke.py` 的同名私有类写，不改 agent_smoke）：

```python
SCREEN_LABELS = {"home": "ARKNIGHTS HOME", "desktop": "ANDROID DESKTOP", "stage": "STAGE START PAGE",
                 "battle": "IN BATTLE", "settlement": "SETTLEMENT"}
def render_screen(screen: str) -> Image.Image: ...    # 1280×720 RGB，每种画面一个底色，左上角用 ImageDraw 默认字体写标签
class FakeCoreSupervisor: ...                         # 同 _SmokeCoreSupervisor
class FakeCoreClient:
    def __init__(self, _supervisor: Any, *, script: str = "success", **_kwargs: Any) -> None: ...
        # FakeAsst(script=script, delay_scale=0)；append_task 返回值 <= 0 时
        # raise AppError(ErrorCode.CORE_COMMAND_FAILED, "AsstAppendTask 返回 0：参数不被内核接受")
class FakeDeviceManager:
    def __init__(self, _settings: Any, _client: Any, *, broadcast: Any, core_id: str,
                 connected: bool = True, screen: str = "home") -> None: ...
    async def screenshot(self, backend: str = "adb") -> ScreenshotResult: ...   # ScreenshotResult(render_screen(screen), "adb")
    def snapshot(self) -> dict[str, Any]: ...       # 键同 DeviceManager.snapshot()，state 为 connected 或 disconnected
class FakePipelineRunner(PipelineRunner): ...       # 同 _SmokePipelineRunner
def fake_core_factories(*, script: str, device_connected: bool, screen: str) -> dict[str, Any]: ...
    # 返回 core_supervisor_factory、core_client_factory、device_manager_factory、pipeline_runner_factory 四项（用 functools.partial 带参数）
```

`tests/evals/environment.py`（新建；`tests/evals/__init__.py` 空文件）：`FakeWorldEnvironment(*, repo_root: Path, fixtures_dir: Path)` 实现 `EvalEnvironment.open`，步骤见 §4.5.4。

#### 4.2.8 `scripts/agent_eval.py`（新建）

`build_parser() -> argparse.ArgumentParser`、`main(argv: list[str] | None = None) -> int`。脚本开头把仓库根插进 `sys.path` 再导入 `tests.*`。参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--suite {regression,capability,all}` | `regression` | |
| `--mode {replay,record,live}` | `replay` | |
| `--scenario ID` | | 可重复；只跑这些 |
| `--agent NAME` | | 只跑 `agent` 等于它的场景 |
| `--tag TAG` | | 可重复 |
| `--repeat N` | 1 | 每个场景跑几次 |
| `--strict` | 关 | 录制指纹不一致算出错 |
| `--min-pass-rate X` | regression 1.0，capability 不设 | 低于它退出码 1 |
| `--out DIR` | `resource/evals` | |
| `--scenarios-dir DIR`、`--cassettes-dir DIR`、`--fixtures-dir DIR` | `tests/evals/scenarios`、`tests/evals/cassettes`、`tests/evals/fixtures` | |
| `--base-url URL`、`--model NAME` | 环境变量 `MAA_EVAL_BASE_URL`、`MAA_EVAL_MODEL` | record、live 用；key 只从环境变量 `MAA_EVAL_API_KEY` 读 |
| `--judge-model NAME` | 同 `--model` | |
| `--keep-temp` | 关 | 保留每例的临时目录 |
| `--list` | | 只列出匹配的场景后退出 |

输出：每例一行 `通过 legacy-daily-submit#1  812ms  模型调用 3`，不通过的行写 `失败`、`出错` 或 `跳过`，并在下一行缩进写第一条判分细节；然后一行 `regression replay：8/8 通过（100.0%），结果 resource/evals/<id>/result.json`；末行 `EVAL OK` 或 `EVAL FAIL`。退出码按 10 §6.8；record、live 缺 key 或缺 base url、model 时打印原因并返回 3。

### 4.3 接口

| 方法与路径 | 函数名 | 响应 | 错误 |
|---|---|---|---|
| GET `/api/agent/runs/{run_id}/spans` | `list_run_spans` | 200 `RunSpansResponse` | 404 `RUN_NOT_FOUND` |

写在 `maa_api/api/routers/runs.py`，tag `agent`，`dependencies=[Depends(require_auth)]`，`responses=error_responses("RUN_NOT_FOUND", "UNAUTHORIZED")`，`summary="读取运行所在的整条追踪"`。响应模型写在同一文件：

```python
MAX_TRACE_SPANS = 5000

class SpanOut(BaseModel):
    span_id: str
    parent_span_id: str | None
    run_id: str | None
    name: str
    kind: str
    status: str
    attributes: dict[str, Any]
    started_at: str           # ISO 8601 带 Z
    ended_at: str | None
    duration_ms: int | None

class RunSpansResponse(BaseModel):
    trace_id: str
    items: list[SpanOut]
    truncated: bool
```

处理：`SELECT root_run_id FROM agent_run WHERE id = :id`，没有行抛 `RUN_NOT_FOUND`；`list_by_trace(root_run_id, limit=MAX_TRACE_SPANS + 1)`，多出的一条丢掉并置 `truncated=true`。追踪关闭或已清理时返回空 `items`。示例：

```json
{"trace_id": "5b0c…", "truncated": false, "items": [
  {"span_id": "9f1c2a7e3b4d5e6f", "parent_span_id": null, "run_id": "5b0c…", "name": "agent.run", "kind": "other", "status": "ok",
   "attributes": {"gen_ai.operation.name": "invoke_agent", "maa.agent.name": "legacy_chat", "maa.run.finish_reason": "stop"},
   "started_at": "2026-11-02T08:15:30.120Z", "ended_at": "2026-11-02T08:15:34.900Z", "duration_ms": 4780}]}
```

### 4.4 配置项

`maa_api/settings.py` 加 `TraceSettings`（`model_config = ConfigDict(extra="ignore")`；`enabled: bool = True`；`retention_days: int = Field(default=14, ge=1, le=90)`；`otlp_endpoint: str = ""`；`otlp_headers: str = ""`），`Settings` 加 `trace: TraceSettings = Field(default_factory=TraceSettings)`。`SETTING_KEYS` 加四项（`"trace.enabled": ("trace", "enabled")` 等），`CONFIG_ONLY_SETTING_KEYS` 加 `trace.otlp_endpoint`、`trace.otlp_headers`。

`maa_api/services/settings_schema.py` 的 `_FIELDS`：

```python
"trace.enabled": dict(label="记录运行追踪", group="Agent", type="boolean", hot_action="none"),
"trace.retention_days": dict(label="运行追踪保留天数", group="Agent", type="integer", minimum=1, maximum=90, hot_action="none"),
```

`config.template.yaml` 加：

```yaml
# 运行追踪：每次代理调用的步骤、耗时与用量
trace:
  enabled: true
  # 超过这么多天的追踪记录由每天 04:00 的清理任务删除
  retention_days: 14
  # 导出到 Langfuse 或 Phoenix：填完整的 traces 地址（以 /v1/traces 结尾），留空不导出；需要安装 otlp 依赖
  otlp_endpoint:
  # 导出请求头，写成 k=v,k=v；值里的逗号写成 %2C
  otlp_headers:
```

测试：`tests/test_settings.py` 的 `ENV_OVERRIDES` 整表加 `MAA_TRACE_ENABLED`、`MAA_TRACE_RETENTION_DAYS`、`MAA_TRACE_OTLP_ENDPOINT`、`MAA_TRACE_OTLP_HEADERS` 四项；`tests/test_vapid_settings.py` 的 config-only 集合加两项。

### 4.5 行为与算法

#### 4.5.1 SpanRecorder 的写入

```
record(r): 追踪关闭 → 丢弃；pending 已满 → dropped += 1；否则 pending.append(r)；攒满 batch_size 时置唤醒事件
_flush_loop: 循环 { 等唤醒事件或 flush_interval 超时；取出最多 batch_size 条；
                    run_with_busy_retry 写入（TraceSpanRepository.add_many + commit）；
                    成功 → written += n，exporter 存在时整批放进导出队列（满了丢最旧的，export_dropped += 丢的条数）；
                    失败 → write_errors += 1，这批的尝试次数 +1，未满 MAX_WRITE_ATTEMPTS 放回队首，满了丢弃计入 dropped；
                    日志警告一分钟最多一条 }
_export_loop: 有导出队列时循环 { 取最多 batch_size 条；ok = await asyncio.to_thread(exporter.export, batch)；
                    失败的批次放回一次，第二次失败丢弃并 export_dropped += n }
```

边界：`trace_id` 不是 36 位 UUID 字符串 → `start_span` 抛 `ValueError`（编程错误，测试覆盖）；`end()` 调两次 → 第二次无效；`close()` 之后 `record` → 丢弃并计数；写入时 `run_id` 指向的运行已被删除导致外键失败 → 按写入失败处理。

#### 4.5.2 回放的一次调用

```
handle_async_request(req):
    body = json.loads(req.content)；channel = channel_override 或 classify_request(body)
    requests.append(summarize_request(body, channel))
    probe: 有录制的 probe 就用，否则：工具里有 capability_probe → 回一个调用 capability_probe 的 tool call；否则回 VISION_PROBE_REPLY
    aux:   有录制的 aux 就用，否则回 DEFAULT_AUX_TEXT
    agent/judge:
        agent_calls += 1（只算 agent）；超过 max_calls → 返回 400 {"error": {"message": "eval max_model_calls reached", "code": "eval_limit"}}，记 CassetteError("limit")
        call = cassette.pop(channel)；None → CassetteError("exhausted")，返回 400
        请求最后一条是 tool 消息：它们的 tool_call_id 集合 ≠ 上一条同通道应答的 tool call id 集合 → CassetteError("mismatch")
        call.request 存在且 tools_sha/system_sha 不同 → warnings 追加一条（strict 时 CassetteError("mismatch")）
        call.error → 按 status、headers、body 返回
        chunks = call.chunks 或 expand_reply(render_placeholders(call.reply, body["messages"]), model=header.model, seq)
        返回 200，content=sse_body(chunks)，content-type text/event-stream
```

传输层记下的第一个 `CassetteError` 由 runner 在本例结束时读取，本例记 `error`，错误文字用它的 `code` 与消息。

#### 4.5.3 请求分类与展开

- `classify_request`：`tools` 里有名为 `capability_probe` 的函数 → `probe`；`tools` 为空且最后一条消息的 content 是含 `image_url` 片段的列表 → `probe`；`tools` 为空且第一条消息不是 `system` → `aux`；其余 → `agent`。
- `expand_reply`：分片形状照 `tests/fakes/fake_llm.py`；`reasoning` 放进 `delta.reasoning_content`，`text` 放进 `delta.content`，`tool_calls` 按顺序给 `index`、`id`、`type="function"`、`function.name`、`function.arguments`（`json.dumps(..., ensure_ascii=False)`）；末尾分片带 `finish_reason`（缺省时有工具调用为 `tool_calls`，否则 `stop`）与 `usage`：`prompt_tokens`、`completion_tokens`、`total_tokens`、`prompt_tokens_details.cached_tokens`、`prompt_cache_hit_tokens`（后两个都写缓存命中数，兼容 OpenAI 与 DeepSeek 的写法）。每个分片带 `id="chatcmpl-eval-<seq>"`、`object="chat.completion.chunk"`、`model`。
- 占位符：路径按 `.` 分段，纯数字段当列表下标；在请求消息里找 `tool_call_id` 等于占位符 id 的 tool 消息，解析其 JSON（有 `result` 键取 `result`）后取值；找不到 → `CassetteError("placeholder")`。
- `templatize_chunks`（录制用）：先把分片里的 tool call 参数拼回完整 JSON，字符串值匹配 `UUID_RE` 且在之前某条 tool 消息结果里出现，就换成指向它的占位符，再把参数写回最后一个参数分片、清空其余参数片段。

#### 4.5.4 一个用例怎么跑

`FakeWorldEnvironment.open(scenario, llm, workdir, transport)`：

1. `workdir` 下建 `resource/`、`app-root/core`、`config.yaml`。配置写 `app.access_token=eval-token`、`app.maa_core_path`、`llm.base_url/api_key/model`（取 `llm`）、默认设置（`agent.auto_diagnose_failures=false`、`agent.question_timeout_seconds=5`、`agent.confirmation_timeout_seconds=5`），再叠加 `scenario.settings`（点分键展开成嵌套）。
2. 照 agent_smoke 的隔离步骤：换 `db_session` 的 `DB_PATH`、`ASYNC_URL`、`SYNC_URL`、`engine`、`session_factory`；清掉 `ENV_OVERRIDES` 里的环境变量；`settings_module.DEFAULT_CONFIG_PATH`、`set_settings(load_settings(config))`；`main_module.REPO_ROOT = app-root`。
3. `maa_api.services.external.http._DEFAULT = ExternalClient(transport=httpx.MockTransport(handler))`；`handler` 按 `world.http` 依次找 `match` 是 `str(request.url)` 子串的第一项，返回 `status` 与 `json` 或 `fixtures_dir/body_file` 的内容；都不匹配时抛 `httpx.ConnectError("评测环境不访问外部网络")`。
4. `app = main_module.create_app()`，把 `fake_core_factories(...)` 的四项设到 `app.state`；`client = TestClient(app, raise_server_exceptions=False)`，`client.__enter__()` 跑 lifespan。
5. 接模型：`http = httpx.AsyncClient(transport=transport, timeout=120)`，`eval_client = OpenAICompatibleClient(base_url=llm.base_url, api_key=llm.api_key, http_client=http)`，`app.state.agent_runtime.client_factory = lambda _settings: eval_client`。收尾时 `client.portal.call(http.aclose)`。
6. `world.seed` 不为空时 `client.portal.call(seed_world, app, scenario.world.seed)`：直接写 `Pipeline`（`source="manual"`、`priority=0`、`task_count`、`created_at=now-minutes_ago`、`started_at`、`finished_at`）、`Task`（`order_index`、`type_name`、`task_name=type_name`、`params`、`raw_params=params`、状态与错误）、`LogEntry`（`source="maa_task"`，`task` 是任务下标）。
7. `yield EvalApp(app, client, client.portal, "eval-token", workdir)`；`finally` 里关 `http`、`client.__exit__`、恢复第 2、3 步换掉的全部全局对象、`reset_default_external_client()`、`FakeAsst.reset_class_state()`、`engine.sync_engine.dispose()`。

`EvalRunner.run_case(scenario, attempt, variant)`：

1. 驱动不在 `DRIVERS` 里 → `skipped`（「这个入口还没有实现」）。replay 时读 `cassette_dir/scenario.cassette_name()`，文件不存在 → `error`。
2. 按模式建传输层（replay `CassetteTransport`，record `RecordingTransport` 加新的 `Cassette`，live `CountingTransport`），`max_calls=scenario.limits.max_model_calls`。
3. `with environment.open(..., transport=传输层) as eval_app`：`variant.setup` 存在时 `portal.call(variant.setup, eval_app)`；`portal.call(_run_with_timeout, driver, eval_app, scenario, recorder)`，超时则对 `recorder.session_ids` 逐个 `interrupt_turn`，本例记 `error`（「超过 N 秒没有结束」）；然后 `portal.call(collect_observation, ...)`（先 `await app.state.span_recorder.flush()`），再 `portal.call(grade_case, ...)`。
4. 环境关闭后：replay 时调 `check_unused()`；传输层记过 `CassetteError` 时本例为 `error`。record 模式本例通过时 `cassette.save(cassette_dir/...)`，不通过时存到结果目录 `<id>.failed.jsonl`。
5. `usage` 从临时库 `agent_run` 汇总（三种 token、`cost_micros`、`currency`），`run_ids` 是全部运行 id。

`drive_legacy_session`：用 `AgentSessionRepository(db).create(AgentSession(title=f"评测 {scenario.id}", model=<llm.model>, base_url=<llm.base_url>))` 建会话并提交，记进 `recorder.session_ids`；每个 turn：`acquire_session` → `ensure_configured(request, sid)` → 逐块读 `stream_message(sid, turn.user, request)` 并解析 SSE（`event:`、`data:` 两行一块，忽略 `:` 开头的注释）→ `finally: release_session(sid)`。`request` 是只带 `app` 属性的替身 `_AppRequest(app)`。事件处理：

- `confirmation_required`：`approve` / `reject` 调 `confirmation_service.resolve(id, approved=..., resolved_by="eval", reason="评测脚本的裁决")`；`ignore` 不动。
- `question_required`：`skip` 调 `question_service.skip(id, answered_by="eval")`；`answer` 调 `answer(id, selected=..., answered_by="eval")`；`ignore` 不动。
- `interrupt` 命中（事件名与 `data["name"]` 都对上，只触发一次）：`asyncio.create_task` 一个先睡 `delay_seconds` 再 `interrupt_turn(sid)` 的任务，不在读流的协程里等它。

`collect_observation` 用 `sqlalchemy.text` 按表名、列名查临时库（不依赖 ORM 类名）：会话的消息（按 `session_id, seq`）、`agent_run`（按 `started_at`）、`pipeline` 与 `task`、`confirmation`、`agent_question`、`trace_span`。工具调用由助手消息的 `tool_calls` 与同 `tool_call_id` 的 tool 消息配对：tool 消息 JSON 顶层有 `error` 对象 → `status=error`、`error_code=error.code`；否则 `ok`，`result` 取 `result` 键或整个对象。`final_text` 是最后一条用户消息之后最后一条有正文的助手消息，`all_text` 是全部助手正文用换行拼接。

#### 4.5.5 判分

- `match_value` 按 10 §6.7 的规则（含 `not` 运算符与缺键规则）；描述不匹配时带路径，例如 `$.tasks[0].name：期望 StartUp，实际 Fight`。
- `outcome_view(obs)` 产出：`runs: {count, last, all}`（每项 `agent_name, kind, status, finish_reason, steps`）、`pipelines: {count, last}`（`last` 带 `status, title, error_code, tasks[{type_name, status, params, raw_params, error_code}]`）、`confirmations: {count, statuses}`、`questions: {count, statuses}`、`messages: {orphan_tool_calls, last_assistant: {finish_reason, content}}`、`model_calls`、`requests: {max_reasoning_messages, max_chars}`（只看 `agent` 通道）、`spans: {count, names: {名字: 条数}}`。`grade_outcome_state` 对规格里每个非空字段做 `match_value(规格字段, 视图字段)`。
- `orphan_tool_calls`：全部助手消息 `tool_calls[].id` 与全部 tool 消息 `tool_call_id` 的差集大小（与 `scripts/agent_history_audit.py` 的判定一致）。
- `grade_tool_sequence`：`exact` 要求调用数等于步数且逐一匹配；`subsequence` 从左到右贪心匹配，`next: true` 的步必须是上一个匹配步的下一个调用；每步匹配 `tool`、`args`（`match_value`）、`status`、`error_code`；`forbid` 里的工具出现即失败；`max_calls`、`min_calls` 按工具计数。细节写第一个对不上的步与实际调用序列。
- `grade_text_contains`：`all_of` 全部出现、`any_of` 至少一个、`none_of` 都不出现；`ignore_case` 时两边都 `casefold()`。

#### 4.5.6 llm_judge

`JUDGE_SYSTEM_PROMPT`：「你是评测员。按评分标准判断这次代理运行是否达标。只输出一个 JSON 对象：{"score": 0 到 1 的数, "passed": true 或 false, "reasons": [最多 5 条简短理由]}。」用户消息依次是：评分标准、用户每轮说的话、工具调用列表（名字、参数截到 300 字、结果状态与错误码）、最终回复。评审调用走一个新的 `OpenAICompatibleClient`，`http_client=httpx.AsyncClient(transport=评审传输层)`：replay 是同一个录制文件上 `channel_override="judge"` 的 `CassetteTransport`，录制文件里没有 judge 调用时直接返回 `skipped`（「回放没有录制评审应答」）；record 是 `RecordingTransport(channel_override="judge")`；live 是 `CountingTransport`。输出用 pydantic 模型校验，失败重试一次（把校验错误附在下一条用户消息里），仍失败为 `error`；`score >= pass_score` 且 `passed` 为真时 `passed`。

#### 4.5.7 结果文件

`write_result`：`out_dir/<id>/result.json`（`SuiteResult.to_json()`，字段按 10 §6.8）；每例 `out_dir/<id>/cases/<场景>-<attempt>.json`（变体不是 `target` 时文件名再加 `-<变体>`），内容是 `CaseResult` 加 `detail`（消息、工具调用、SSE 事件、span、请求摘要、警告）；`out_dir/index.jsonl` 追加一行 `{"id", "path"（相对 out_dir）, "suite", "mode", "started_at", "finished_at", "summary", "variants", "target", "baseline"}`。`<id>` 为 `UTC 时间 %Y%m%d-%H%M%S` 加 `-<suite>-<mode>-` 加 `secrets.token_hex(2)`。`summary(variant)` 只统计该变体的用例；`variants` 是每个变体名到它的 `summary`。

### 4.6 前端

路由（`web/src/routes/router.tsx`，挂在外壳下，`page()` 包裹，懒加载）：`more/runs` → `features/runs/RunsPage`，`more/runs/:runId` → `features/runs/RunDetailPage`，`more/costs` → `features/costs/CostsPage`。

导航（`web/src/components/layout/nav.ts`）：在 `secondaryNav` 的「Agent 审计」之前加两项 `{ to: '/more/runs', label: '运行追踪', icon: Activity, description: '每次代理调用的步骤、耗时与用量' }`、`{ to: '/more/costs', label: '成本', icon: Coins, description: '按天、代理、模型统计模型费用' }`；分组字段由 M72 统一加（M72 spec 的 `secondaryNav` 写着给这两项补 `group: 'agent'`）。

Query key（`web/src/api/keys.ts` 的 `keys.agent` 下；形状与 M72 spec §4.6.2 一致，M72 复用不重复定义）：

```ts
specs: {
  all: () => [...root, 'agent', 'specs'] as const,
  detail: (name: string) => [...root, 'agent', 'specs', 'detail', name] as const,
},
runs: {
  all: () => [...root, 'agent', 'runs'] as const,
  list: (filters?: Readonly<Record<string, unknown>>) => [...root, 'agent', 'runs', 'list', filters ?? {}] as const,
  detail: (runId: string) => [...root, 'agent', 'runs', 'detail', runId] as const,
  spans: (runId: string) => [...root, 'agent', 'runs', 'detail', runId, 'spans'] as const,
},
costs: (filters?: Readonly<Record<string, unknown>>) => [...root, 'agent', 'costs', filters ?? {}] as const,
```

| 文件 | 内容 |
|---|---|
| `features/runs/RunsPage.tsx` | `Page title="运行追踪" back="/more"`；读 `?ticket_id=`、`?page=`；`GET /api/agent/runs`（`root_only=true`、`size=20`）；行：代理（`GET /api/agent/specs`，key `keys.agent.specs.all()`，取 `title`，查不到写「未知代理」）、种类、状态徽标、开始时间、时长、步数、token、成本；整行链到详情；右上角刷新按钮；空状态标题「还没有运行记录」、说明「去 Agent 页发一条消息」、按钮「去 Agent 页」链到 `/agent`（M72 改成指挥台）；出错用 `ErrorState` |
| `features/runs/RunDetailPage.tsx` | 概要卡（状态、终止原因、模型、版本前 8 位、开始与结束、步数、输入与缓存命中与输出 token、成本）、`RunTree`、`SpanWaterfall`；运行状态为 `running` 或 `waiting_human` 时 `GET /api/agent/runs/{run_id}` 与 spans 每 3 秒轮询，结束后停 |
| `features/runs/RunTree.tsx` | 递归画 `children`，当前运行高亮，节点链到各自详情 |
| `features/runs/SpanWaterfall.tsx` | props `{spans, highlightRunId, onSelect}`；每行左边是标签（`agent.run` 写「运行 · 代理名」，`llm.chat` 写「模型 · 模型名」，`tool.execute` 写「工具 · 工具名」，`delegate.subagent` 写「委派 · 子代理名」，`confirmation.wait` 写「等待确认」，其余写种类名），按深度缩进 12px（最多 6 级），右边是相对整条 trace 的横条与时长；`truncated` 时顶部提示「只显示前 5000 条」；没有 span 时写「这次运行没有追踪记录」，`InfoHint` 说明「运行追踪关闭，或已超过保留天数」 |
| `features/runs/SpanDetailSheet.tsx` | `ResponsiveSheet`：名字、种类、状态、开始、时长、属性表；`run_id` 不是当前运行时给按钮「打开这次运行」 |
| `features/runs/timeline.ts` | `buildSpanRows(spans): SpanRow[]`（按开始时间深度优先排列，上级缺失的行 `orphan: true` 当顶层）、`spanBar(row, start, end): {left: number; width: number}`（百分比，最小宽度 0.5） |
| `features/runs/format.ts` | `RUN_KIND_LABELS`、`RUN_STATUS_LABELS`（11 §11 的写法）、`FINISH_REASON_LABELS`、`SPAN_KIND_LABELS`、`formatMicros(micros, currency)`（CNY 写 `¥`、USD 写 `$`，其他写币种代码，最多 4 位小数）、`formatDuration(ms)` |
| `features/costs/CostsPage.tsx` | `Page title="成本" back="/more"`；`SegmentedControl` 选范围（今天、近 7 天、近 30 天）与分组（按天、按代理、按模型、按角色）；汇总：总费用、调用次数、缓存命中率；「今日」卡片：今日费用（单独一次 `summarize_costs`，`since` 为本地零点、`group_by=day`、`currency=CNY`，M67 的预算只按人民币统计）与 `budget.daily_cost_limit`（从 `settingsQueryOptions` 的 `values` 读，0 写「不限」，达到 `budget.warn_ratio` 用警告色）；按天时用 `EChartSurface` 画柱状图；分组表：名称、调用、输入 token、缓存命中、输出 token、费用；按代理时名称用定义的 `title`，按角色时用角色中文名 |
| `features/costs/format.ts` | `ROLE_LABELS`（orchestrator 总控、worker Worker、subagent 子代理、summarizer 摘要、title 标题、trigger_eval 事件研判、memory 记忆、vision 视觉、embedding 向量）、范围到 `since`、`until` 的换算（本地零点转 ISO 带 Z） |

文案照 11 §11：界面不出现代码名，代理一律显示 `title`；状态：running 运行中、waiting_human 等你处理、succeeded 已完成、failed 失败、interrupted 已停止、cancelled 已取消。种类：orchestrator_turn 总控一轮、worker_turn Worker 一轮、subagent 子代理、trigger_eval 事件研判、compaction 摘要压缩、memory_extract 记忆整理、title 标题生成、watchdog_check Watchdog 巡检、eval_case 评测用例、legacy_turn 旧会话一轮。

### 4.7 提示词与代理定义

无。

## 5. 实施任务

### T1 表、枚举、迁移与仓储
- 写范围：`maa_api/domain/enums.py`、`maa_api/db/models.py`、`maa_api/db/migrations/versions/0025_trace_spans.py`、`maa_api/db/repositories/trace.py`、`tests/db/test_models.py`、`tests/domain/test_enums.py`、`tests/db/test_trace_spans_migration.py`、`tests/db/test_repositories_trace.py`
- 步骤：按 §4.1、§4.2.5 写；迁移测试照 `tests/db/test_agent_question_migration.py`。
- 验证：`.venv/bin/python -m pytest -q tests/db tests/domain/test_enums.py > /tmp/m68-t1.log 2>&1; echo $?; tail -n 5 /tmp/m68-t1.log` → 0
- 完成判据：§6 中 T1 的用例全部通过。

### T2 设置项
- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`tests/test_vapid_settings.py`
- 步骤：按 §4.4。
- 验证：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py tests/api > /tmp/m68-t2.log 2>&1; echo $?` → 0

### T3 SpanRecorder 与装配
- 写范围：`maa_api/agent/trace/__init__.py`、`maa_api/agent/trace/spans.py`、`maa_api/main.py`、`tests/agent/trace/test_trace_spans.py`
- 步骤：按 §4.2.1、§4.2.5 的装配、§4.5.1；这一步 `exporter` 先传 `None`，T7 再接。
- 验证：`.venv/bin/python -m pytest -q tests/agent/trace tests/api/test_agent_assembly_wiring.py > /tmp/m68-t3.log 2>&1; echo $?` → 0

### T4 TraceMiddleware 与模型响应字段
- 写范围：`maa_api/agent/harness/builtin_middleware.py`、`maa_api/agent/harness/context.py`、`maa_api/agent/harness/chunks.py`、`tests/agent/trace/test_trace_middleware.py`
- 步骤：按 §4.2.2、§4.2.3 前两条。钩子测试不起 lifespan：构造 `SpanRecorder`（临时库）与最小的 `RunContext` 直接调；另用 `FakeLLM` 驱动一次 `legacy_chat` 会话，断言写出的 span。
- 验证：`.venv/bin/python -m pytest -q tests/agent > /tmp/m68-t4.log 2>&1; echo $?; tail -n 5 /tmp/m68-t4.log` → 与基线相比不新增失败

### T5 追踪端点
- 写范围：`maa_api/api/routers/runs.py`、`tests/api/test_run_spans_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`
- 步骤：按 §4.3；`.venv/bin/python scripts/dump_openapi.py`，`cd web && pnpm gen:api`；清单的 agent 小节追加一行 `| GET | \`/api/agent/runs/{run_id}/spans\` | 运行所在的整条追踪（M68） | | 200 | \`RUN_NOT_FOUND\` |`，该小节「（N 条）」与 §6 开头的总数各 +1。
- 验证：`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0；`.venv/bin/python -m pytest -q tests/api/test_run_spans_router.py tests/domain/test_route_inventory.py > /tmp/m68-t5.log 2>&1; echo $?` → 0

### T6 保留策略
- 写范围：`maa_api/services/retention_service.py`、`tests/services/test_retention_service.py`
- 验证：`.venv/bin/python -m pytest -q tests/services/test_retention_service.py tests/services/test_retention_copilots.py > /tmp/m68-t6.log 2>&1; echo $?` → 0

### T7 OTLP 导出与 extra
- 写范围：`maa_api/agent/trace/otlp.py`、`maa_api/agent/trace/spans.py`（导出队列）、`maa_api/main.py`（`build_exporter`）、`pyproject.toml`、锁文件、`Dockerfile`、`docs/v3/16-部署指南.md` §12、`tests/agent/trace/test_trace_otlp.py`
- 步骤：
  1. 依赖按 16 §12 的 extra 约定：`opentelemetry-sdk>=1.30,<2`、`opentelemetry-exporter-otlp-proto-http>=1.30,<2`，理由按 10 §5.1。前置检查第 17 条是 `[tool.poetry]` 时在 `[tool.poetry.dependencies]` 写成 `optional = true` 并加 `[tool.poetry.extras] otlp = [...]`；是 `[project]` 时写进 `[project.optional-dependencies]` 的 `otlp`。按 16 §12 重建锁文件，再用 16 §12 的安装命令带上 `otlp` 装进 `.venv`。
  2. M68 是第一个引入 extra 的里程碑，按 16 §12 给 `Dockerfile` 加构建参数：运行阶段加 `ARG MAA_EXTRAS=""`，安装行在它非空时带上这些 extra（Poetry：`--extras "$MAA_EXTRAS"`；uv：每个名字一个 `--extra`）；16 §12 补用法 `docker build --build-arg MAA_EXTRAS="otlp" .`。
  3. 按 §4.2.4 写 `otlp.py`，在 SpanRecorder 里接导出队列。
- 验证：`.venv/bin/python -c "import opentelemetry.exporter.otlp.proto.http.trace_exporter"; echo $?` → 0；`.venv/bin/python -m pytest -q tests/agent/trace > /tmp/m68-t7.log 2>&1; echo $?` → 0；不装 extra 的环境里全量测试仍要通过（用到 SDK 的用例见 §6，用 `pytest.importorskip`）

### T8 录制文件与传输层
- 写范围：`maa_api/agent/evals/__init__.py`、`maa_api/agent/evals/cassette.py`、`tests/evals/__init__.py`、`tests/evals/test_eval_cassette.py`
- 步骤：按 §4.2.6 的 `cassette.py`、§4.5.2、§4.5.3。
- 验证：`.venv/bin/python -m pytest -q tests/evals/test_eval_cassette.py > /tmp/m68-t8.log 2>&1; echo $?` → 0

### T9 场景 schema 与判分器
- 写范围：`maa_api/agent/evals/scenario.py`、`maa_api/agent/evals/graders.py`、`tests/evals/test_eval_scenario.py`、`tests/evals/test_eval_graders.py`
- 验证：`.venv/bin/python -m pytest -q tests/evals/test_eval_scenario.py tests/evals/test_eval_graders.py > /tmp/m68-t9.log 2>&1; echo $?` → 0

### T10 假世界、runner 与脚本
- 写范围：`tests/fakes/fake_core.py`、`tests/evals/environment.py`、`maa_api/agent/evals/runner.py`、`scripts/agent_eval.py`、`tests/evals/test_eval_runner.py`
- 步骤：按 §4.2.7、§4.2.8、§4.5.4、§4.5.7。`test_eval_runner.py` 用一个手写的小场景与录制文件（放在 `tmp_path`）跑通整条链。
- 验证：`.venv/bin/python -m pytest -q tests/evals/test_eval_runner.py > /tmp/m68-t10.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/agent_eval.py --list` 退出码 0

### T11 初始场景集与基线
- 写范围：`tests/evals/scenarios/*.yaml`（12 个）、`tests/evals/cassettes/*.jsonl`（12 个）、`tests/evals/fixtures/`、`tests/evals/test_regression_replay.py`
- 步骤：按附录 A 写场景与手写录制文件；`test_regression_replay.py` 对每个 regression 场景参数化跑 replay，并断言 `load_scenarios` 能读全部场景且每个都有录制文件。
- 验证：`.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m68-eval-reg.log 2>&1; echo $?; tail -n 3 /tmp/m68-eval-reg.log` → 0，末行 `EVAL OK`；`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m68-eval-cap.log 2>&1; echo $?; tail -n 3 /tmp/m68-eval-cap.log` → 0
- 完成判据：两个通过率与结果 id 写进台账「基线」一节。

### T12 前端：运行追踪
- 写范围：`web/src/api/keys.ts`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/features/runs/*`
- 步骤：按 §4.6；测试照 `features/audit/audit.test.tsx` 用 `vi.stubGlobal('fetch')`。
- 验证：`cd web && pnpm typecheck > /tmp/m68-tc.log 2>&1 && pnpm test --run > /tmp/m68-vt.log 2>&1; echo $?` → 0

### T13 前端：成本
- 写范围：`web/src/features/costs/*`、`web/src/routes/router.tsx`
- 验证：同 T12

### T14 文档与全量验证
- 写范围：§9 列出的文档
- 验证：§7 全部命令

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_trace_spans_migration.py` | `test_upgrade_creates_trace_span_with_indexes`、`test_downgrade_drops_trace_span` | 列类型与可空、三个索引名、外键 CASCADE、autoincrement；降级后表不存在 |
| `tests/db/test_repositories_trace.py` | `test_list_by_trace_orders_by_start_and_respects_limit`、`test_purge_before_deletes_in_batches_and_commits` | 排序与条数；只删截止时间之前的 |
| `tests/agent/trace/test_trace_spans.py` | `test_clean_attributes_truncates_and_caps`、`test_disabled_recorder_returns_noop_handle`、`test_spans_are_written_in_batches_without_awaiting`、`test_full_queue_drops_and_counts`、`test_write_failure_is_retried_then_dropped`、`test_end_is_idempotent`、`test_close_flushes_pending_spans`、`test_non_uuid_trace_id_is_rejected` | `record` 是同步调用；写库失败不抛给调用方；`stats()` 计数 |
| `tests/agent/trace/test_trace_middleware.py` | `test_run_llm_and_tool_spans_form_one_tree`、`test_llm_span_uses_upstream_latency_and_usage`、`test_llm_span_costs_add_up_to_the_run_total`、`test_delegate_tool_gets_delegate_span`、`test_child_run_parent_is_the_tool_span`、`test_foreign_trace_parent_is_ignored`、`test_confirmation_wait_span_is_derived_from_audit`、`test_model_error_still_records_llm_span`、`test_no_content_attributes_are_recorded`、`test_legacy_chat_turn_writes_spans` | 树形；`llm.chat` 成本之和等于 `agent_run.cost_micros` 与 `cost_ledger` 之和；属性里没有消息正文与工具参数 |
| `tests/agent/trace/test_trace_otlp.py` | `test_parse_headers_splits_on_first_equal_and_unquotes`、`test_parse_headers_rejects_items_without_equal`、`test_export_names_follow_genai_conventions`、`test_backend_attributes_for_langfuse_and_phoenix`、`test_missing_extra_disables_export_with_warning`（monkeypatch 让导入失败）、`test_exporter_sends_readable_spans_with_ids`（开头 `pytest.importorskip("opentelemetry.sdk")`，用 `InMemorySpanExporter` 替换 `OTLPSpanExporter`；16 §12 要求列出的 importorskip 用例只有这一条） | 导出名、id 换算、session.id、缺依赖只警告 |
| `tests/api/test_run_spans_router.py` | `test_returns_whole_trace_for_child_run`、`test_unknown_run_is_404`、`test_truncates_after_limit`、`test_requires_auth` | 子运行 id 也返回整条 trace；`truncated` |
| `tests/services/test_retention_service.py` | `test_run_retention_purges_trace_spans_older_than_retention_days` | 返回字典有 `trace_spans_deleted`；新 span 保留 |
| `tests/evals/test_eval_cassette.py` | `test_expand_reply_matches_fake_llm_chunks`、`test_placeholder_reads_previous_tool_result`、`test_unresolved_placeholder_errors`、`test_tool_call_id_mismatch_errors`、`test_exhausted_and_unused_are_errors`、`test_fingerprint_drift_warns_unless_strict`、`test_probe_and_aux_get_default_replies`、`test_recording_templatizes_uuid_arguments`、`test_openai_client_streams_through_cassette_transport`、`test_max_calls_returns_limit_error` | 回放检查逐条覆盖；真实的 `OpenAICompatibleClient` 经传输层读出与 `expand_reply` 一致的分片 |
| `tests/evals/test_eval_scenario.py` | `test_all_fields_round_trip`、`test_id_must_equal_file_stem`、`test_unknown_setting_key_is_rejected`、`test_regression_cannot_use_llm_judge`、`test_ticket_entry_requires_brief_and_scope`、`test_http_fixture_needs_exactly_one_body` | 错误消息带文件路径 |
| `tests/evals/test_eval_graders.py` | `test_match_value_operators`（每个运算符一条参数）、`test_absent_and_not_on_missing_keys`、`test_tool_sequence_subsequence_next_and_exact`、`test_forbid_and_call_limits`、`test_outcome_view_counts_orphans`、`test_text_contains_rules`、`test_llm_judge_skips_without_recorded_reply`、`test_llm_judge_retries_once_on_invalid_json`、`test_case_status_rules` | 判分细节指出第一处不匹配 |
| `tests/evals/test_eval_runner.py` | `test_replay_case_passes_end_to_end`、`test_unsupported_entry_is_skipped`、`test_timeout_interrupts_and_errors`、`test_write_result_appends_index`、`test_record_saves_only_passing_cassettes`、`test_variants_are_summarized_separately` | 用 `FakeWorldEnvironment` 与临时目录 |
| `tests/evals/test_regression_replay.py` | `test_every_scenario_loads_and_has_a_cassette`、`test_regression_scenario_passes`（参数化） | 每个 regression 场景 `passed` |
| `web/src/features/runs/runs.test.tsx` | `lists root runs with agent titles`、`passes ticket_id filter`、`shows empty state`、`detail renders tree and waterfall`、`polls while running and stops after finish`、`opens span sheet` | 界面不出现代码名 |
| `web/src/features/runs/timeline.test.ts` | `orders rows depth first`、`marks orphans as top level`、`bars stay within range` | |
| `web/src/features/costs/costs.test.tsx` | `switches range and group`、`shows cache hit ratio`、`warns at warn ratio`、`shows unlimited when limit is zero` | |

## 7. 验收

按 12 §4：

```bash
.venv/bin/python -m pytest -q > /tmp/m68-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m68-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m68-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck && pnpm test --run > /tmp/m68-vitest.log 2>&1 && pnpm build > /tmp/m68-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m68-fsmoke.log 2>&1; tail -n 1 /tmp/m68-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m68-asmoke.log 2>&1; tail -n 1 /tmp/m68-asmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m68-eval.log 2>&1; echo $?; tail -n 1 /tmp/m68-eval.log
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 与基线相比不新增失败；两个冒烟末行 `SMOKE OK`；评测末行 `EVAL OK`；其余退出码 0；断链 `BROKEN 0`。

本里程碑特有：

- 起服务，在 v2 Agent 页发一条消息，打开「更多 › 运行追踪」，进最新一次运行：瀑布图里有「运行」「模型」「工具」三类行，点开一个工具行能看到 `maa.audit.id`。
- 「更多 › 成本」切换四种分组都有数据或空状态，「今日」卡片在设置了每日上限时显示用量。
- 有 Phoenix 时补充检查：按 10 §5.4 配置后重启，在 Phoenix 里看到同一条 trace；结果记台账，不作硬性验证。
- 有真实 key 时补充检查：`MAA_EVAL_API_KEY=… .venv/bin/python scripts/agent_eval.py --suite capability --mode live --base-url … --model …`，通过率记进台账。

## 8. 风险与回滚

- 追踪写库与业务写库争 SQLite 写锁：批量写、单批 200 条、`run_with_busy_retry`；若全量测试或冒烟出现 busy 超时，先把 `FLUSH_BATCH_SIZE` 降到 50 再观察，记台账。
- M65 的 v2 门面若没有让 legacy 运行经过中间件链，`legacy_chat` 不会产生 span：前置检查第 6 条会发现，停下来问。
- `legacy_session` 入口依赖 `AgentRuntime.stream_message`，M72 删除它；M72 或 M73 的 spec 要把附录 A 的场景迁到 `ticket` 入口，否则 regression 闸门在 M72 失效。
- OTLP 依赖装不上（镜像不可达）：T7 停下来问，其余任务照做，导出代码与测试在依赖可装后补齐。
- 回滚：`git revert -m 1 <merge>`，先 `alembic downgrade 0024_model_profiles`；`resource/evals/` 可直接删除。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md` §6 agent 小节 +1 行（T5）；`docs/v3/04-接口与事件协议.md` §2.1 M68 那一行的响应写成 `{trace_id, items: [Span], truncated}`；`config.template.yaml` 加 §4.4 的片段。
- `docs/v3/16-部署指南.md` 新增「运行追踪导出」一节：extra `otlp` 的安装命令、Langfuse 与 Phoenix 的配置（引用 10 §5.3、§5.4）、镜像用 `--build-arg MAA_EXTRAS="otlp"` 带上依赖；§12 补 `MAA_EXTRAS` 的用法。
- 计数：业务表 +1（`trace_span`）；HTTP 端点 +1；配置键 +4（其中 config-only +2）；错误码、工具、WebSocket 频道不变。实现与本文不一致时，同步改 10 并在 13 §8 补一条。

## 10. 停下来问

- 前置检查任何一条不符合预期；需要新增错误码、端点、表或本文没写的依赖。
- 某个初始场景跑不过，原因是 M65–M67 改变了 v2 行为（终止原因、提示文案、事件名）：先停下来，确认是改场景还是修代码。
- M67 的模型调用不经过 `OpenAICompatibleClient`，传输层覆盖点接不上；OTLP 依赖或锁文件重建失败。
- 11 的导航分组与本文 §4.6 冲突。

## 附录 A：初始场景集

12 个场景：6 个来自 v2 实机复盘（`source` 写对应小节），6 个覆盖 `legacy_chat` 的基本路径。全部 `agent: legacy_chat`、`entry.kind: legacy_session`，录制文件都手写（头里 `source: handwritten`）。下面「录制」一栏按顺序列出 `agent` 通道的每条应答，`cN` 是 tool call id，`{{result:c1:pipeline_id}}` 是占位符。

完整示例（A1）：

```yaml
id: legacy-daily-submit
title: 提交日常并等它跑完
suite: regression
agent: legacy_chat
tags: [legacy, pipeline]
entry: {kind: legacy_session}
world: {core_script: success}
turns:
  - user: 把日常做了：理智清在 1-7，再把奖励领了
expect:
  - grader: tool_sequence
    match: exact
    steps:
      - {tool: submit_pipeline, status: ok, args: {tasks: [{name: StartUp}, {name: Fight, stage: "1-7"}, {name: Award}]}}
      - {tool: wait_pipeline, status: ok}
  - grader: outcome_state
    pipelines: {count: 1, last: {status: completed, tasks: [{type_name: StartUp}, {type_name: Fight}, {type_name: Award}]}}
    confirmations: {count: 0}
    messages: {orphan_tool_calls: 0}
    model_calls: 3
    spans: {names: {agent.run: {gte: 1}, llm.chat: {gte: 3}, tool.execute: {gte: 2}}}
  - grader: text_contains
    any_of: [做完, 完成]
```

对应录制文件 `tests/evals/cassettes/legacy-daily-submit.jsonl` 就是 10 §6.5 的示例，tasks 改成 StartUp、Fight（1-7）、Award（`award: true`）三条。

| # | id（suite） | 来源 | 世界与设置 | 用户与模拟的人 | 录制 | 判分 |
|---|---|---|---|---|---|---|
| A1 | `legacy-daily-submit`（regression） | 基本路径：提交日常 | `success` | 见上 | c1 `submit_pipeline`；c2 `wait_pipeline{pipeline_id: {{result:c1:pipeline_id}}, timeout_seconds: 60}`；文字「日常做完了：理智清在 1-7，奖励也领了。」 | 见上 |
| A2 | `legacy-consume-rejected`（regression） | 基本路径：消耗确认被拒 | `success` | 「刷 1-7，可以吃 2 瓶理智药」；`confirmations: reject` | c1 `submit_pipeline{tasks: [{name: StartUp, start_game_enabled: true}, {name: Fight, stage: "1-7", medicine: 2}]}` | tool_sequence exact `[{tool: submit_pipeline, status: error, error_code: CONFIRMATION_REJECTED}]`；outcome_state `confirmations: {count: 1, statuses: [rejected]}`、`pipelines: {count: 0}`、`messages: {last_assistant: {finish_reason: confirmation_resolved}}`、`model_calls: 1`、`spans: {names: {confirmation.wait: 1}}`；text_contains `all_of: [不会重试]` |
| A3 | `legacy-consume-expired`（regression） | 基本路径：确认过期 | `success`；`agent.confirmation_timeout_seconds: 1`；`limits.timeout_seconds: 40` | 同 A2；`confirmations: ignore` | 同 A2 | 同 A2，错误码换 `CONFIRMATION_EXPIRED`，状态换 `expired`，去掉 span 断言 |
| A4 | `legacy-question-timeout`（regression） | 基本路径：提问超时 | `agent.question_timeout_seconds: 1` | 「帮我刷点东西」；`questions: ignore` | c1 `ask_user{question: 刷哪一关？, options: [{label: 1-7}, {label: CE-6}]}` | tool_sequence exact `[ask_user]`；outcome_state `questions: {count: 1, statuses: [expired]}`、`messages: {last_assistant: {finish_reason: question_unanswered}}`、`model_calls: 1`；text_contains `all_of: [提问超时了]` |
| A5 | `legacy-stuck-loop`（regression） | 基本路径：卡死检测 | `stuck` | 「刷一把 1-7，再看看跑到哪了」 | c1 `submit_pipeline{tasks: [{name: Fight, stage: "1-7"}]}`；c2、c3、c4 都是 `get_pipeline{pipeline_id: {{result:c1:pipeline_id}}}` | tool_sequence exact `[submit_pipeline, get_pipeline, get_pipeline, get_pipeline]`；outcome_state `messages: {last_assistant: {finish_reason: stuck_loop}}`、`model_calls: 4`、`pipelines: {last: {status: {in: [pending, running]}}}`；text_contains `all_of: [一样的工具调用]` |
| A6 | `legacy-interrupt-wait`（regression） | 基本路径：中断 | `stuck` | 「刷 1-7，打完告诉我」；`interrupt: {on: tool_start, tool: wait_pipeline, delay_seconds: 0.5}` | c1 `submit_pipeline{tasks: [{name: StartUp, start_game_enabled: true}, {name: Fight, stage: "1-7"}]}`；c2 `wait_pipeline{pipeline_id: …, timeout_seconds: 30}` | tool_sequence exact `[submit_pipeline, {tool: wait_pipeline, status: error, error_code: AGENT_TURN_INTERRUPTED}]`；outcome_state `messages: {orphan_tool_calls: 0, last_assistant: {finish_reason: interrupted}}`、`pipelines: {count: 1, last: {status: {in: [pending, running]}}}`（停止本轮不停流水线）、`model_calls: 2` |
| B1 | `v2-875-reasoning-last-only`（regression） | §8.75 思考内容只回灌最后一条 | `success` | 两轮：「看看现在系统状态」「再看一次」 | 第一轮 c1 `{reasoning: 约 200 字, tool_calls: [get_system_status]}`、`{reasoning, text: 内核在线，设备已连接。}`；第二轮 c3 同 c1、`{reasoning, text: 还是一样。}` | outcome_state `requests: {max_reasoning_messages: {lte: 1}}`、`model_calls: 4`、`messages: {orphan_tool_calls: 0}`；tool_sequence exact `[get_system_status, get_system_status]` |
| B2 | `v2-878-interrupt-mid-batch`（regression） | §8.78 中断调用的协议完整性 | `stuck` | 第一轮「刷 1-7，顺便看下状态」，`interrupt: {on: tool_start, tool: wait_pipeline, delay_seconds: 0.5}`；第二轮「先不用等了，说下现在的情况」 | c1 `submit_pipeline{tasks: [{name: Fight, stage: "1-7"}]}`；c2 同一批 `[get_system_status, wait_pipeline{pipeline_id: …, timeout_seconds: 30}]`（两个都是只读，并发执行）；c3 文字「流水线还在跑，我先不等了。」 | outcome_state `messages: {orphan_tool_calls: 0}`、`model_calls: 3`；tool_sequence `[submit_pipeline, {tool: get_system_status, status: ok}, {tool: wait_pipeline, error_code: AGENT_TURN_INTERRUPTED}]`；text_contains `any_of: [还在跑]` |
| B3 | `v2-876-logs-budget`（capability） | §8.76、§8.77 日志只取够判断的量 | `seed.pipelines`：一条 `failed` 的「刷 1-7」，任务 Fight `skipped`、`CORE_COMMAND_FAILED`，12 行日志（info「开始任务 Fight」，warning「识别失败：没有找到开始行动按钮」10 行，error「TaskChainError」） | 「上一条流水线为什么失败了？」 | c1 `list_pipelines{status: failed, size: 5}`；c2 `get_logs{pipeline_id: {{result:c1:items.0.id}}, level: WARNING}`；c3 文字「失败在 Fight：一直识别不到开始行动按钮，游戏多半没停在关卡页。先把游戏停到 1-7 的开始行动页再重跑。」 | tool_sequence `steps: [{tool: get_logs, args: {detail: {not: full}, content_chars: {not: {gt: 400}}}}]`、`min_calls: {get_logs: 1}`、`max_calls: {get_logs: 3}`；text_contains `any_of: [开始行动, 识别]`；llm_judge `rubric: 回答点出失败的任务与原因并给出下一步；没有整段粘贴日志`（replay 下 skipped） |
| B4 | `v2-877-failure-hint-startup`（capability） | §8.77 第 6 条：照内核提示先补 StartUp | `seed.pipelines`：一条 `failed` 的「刷 1-7」，只有 Fight，`CORE_COMMAND_FAILED`；`screen: desktop` | 「刚才刷 1-7 没成功，帮我重新来一次」 | c1 `list_pipelines{status: failed, size: 5}`；c2 `wait_pipeline{pipeline_id: {{result:c1:items.0.id}}, timeout_seconds: 5}`（终态立即返回带 `FAILURE_HINT`）；c3 `get_screenshot`；c4 `submit_pipeline{tasks: [{name: StartUp, start_game_enabled: true}, {name: Fight, stage: "1-7"}]}`；c5 文字「画面停在模拟器桌面，这次先启动游戏再刷 1-7，已经提交。」 | tool_sequence `steps: [{tool: submit_pipeline, args: {tasks: {first: {name: StartUp}}}}]`、`max_calls: {submit_pipeline: 1}`；outcome_state `pipelines: {last: {tasks: {first: {type_name: StartUp}}}}` |
| B5 | `v2-881-startup-no-channel`（capability） | §8.81 R1、R3：不猜客户端渠道 | `success`；`screen: desktop` | 「游戏好像没开，帮我把游戏启动起来」 | c1 `submit_pipeline{tasks: [{name: StartUp, start_game_enabled: true}]}`；c2 `wait_pipeline{pipeline_id: …, timeout_seconds: 60}`；c3 文字「游戏已经启动。」 | tool_sequence `steps: [{tool: submit_pipeline, args: {tasks: {first: {name: StartUp, client_type: {absent: true}, server: {absent: true}}}}}]`；outcome_state `pipelines: {last: {tasks: {first: {type_name: StartUp, params: {client_type: Bilibili}, raw_params: {client_type: {absent: true}}}}}}`（服务端按设置补渠道） |
| B6 | `v2-881-wait-timeout-screenshot`（capability） | §8.81 R2、W4：等待超时先看画面 | `stuck`；`screen: desktop`；`limits.timeout_seconds: 300` | 「帮我启动游戏，好了跟我说一声」 | c1 `submit_pipeline{tasks: [{name: StartUp, start_game_enabled: true}]}`；c2 `wait_pipeline{pipeline_id: …, timeout_seconds: 5}`（超时，带 `WAIT_TIMEOUT_HINT`）；c3 `get_screenshot`；c4 `stop_pipeline`；c5 文字「等了一会儿画面还是模拟器桌面，游戏没有拉起来，我先停了流水线。要换个方式试吗？」 | tool_sequence `steps: [{tool: submit_pipeline}, {tool: wait_pipeline, args: {timeout_seconds: {not: {gt: 120}}}}, {tool: get_screenshot, next: true}]`、`max_calls: {wait_pipeline: 2}`；text_contains `any_of: [桌面, 画面]` |

`usage` 一律写 `{prompt_tokens: 1800, completion_tokens: 60, cached_tokens: 1536}` 一类的整数，回放时成本与命中率的计算也被走到。

## 附录 B：录制 cassette

有 key 时：

1. 准备一个 OpenAI 兼容端点：`export MAA_EVAL_BASE_URL=https://api.deepseek.com MAA_EVAL_MODEL=deepseek-flash MAA_EVAL_API_KEY=<key>`（key 只放在当前 shell，不写进任何文件）。
2. 录一个：`.venv/bin/python scripts/agent_eval.py --mode record --scenario v2-881-startup-no-channel`。通过时覆盖 `tests/evals/cassettes/v2-881-startup-no-channel.jsonl`，头里 `source: recorded`、`model`、`spec_version` 为录制时的值；不通过时在结果目录留下 `<id>.failed.jsonl`，看它判断是场景期望写错还是模型真做错了。
3. 回放确认：`.venv/bin/python scripts/agent_eval.py --mode replay --scenario v2-881-startup-no-channel`。提交前看一遍 diff：录制文件只应有应答分片、请求指纹与占位符，不能出现 key、`Authorization`、真实账号数据或本机路径。
4. regression 场景不用 record 覆盖：它们的录制文件描述确定的模型输出，手写维护。

没有 key 时：

1. 照附录 A 或 10 §6.5 手写：每次模型调用一行 `reply`，工具调用的 id 自己起（`c1`、`c2`…），引用上一步结果里的 id 用占位符。
2. 跑 replay：出现 `mismatch` 说明系统执行的工具调用与录制的不一致，先看判分细节里的实际调用序列；出现 `exhausted` 说明系统比录制多调了一次模型（例如多了一轮结构化输出重试），按实际行为补一行或改场景。
3. 头里写 `source: handwritten` 与 `notes`，说明这是理想轨迹、等有 key 时重录。

## 需要补进契约的名字

| 名字 | 用途 | 建议写进 |
|---|---|---|
| `RunSpansResponse.truncated`、`SpanOut`、`MAX_TRACE_SPANS=5000` | spans 端点超过上限时截断 | 04 §2.1 |
| `ModelResponse.model`、`ModelResponse.response_id` | `gen_ai.response.model`、`gen_ai.response.id` | 05 §2.1 |
| `maa_api/agent/trace/otlp.py` 的 `OtlpExporter`、`build_exporter`；extra `otlp` | 可选导出 | 02 §4.1 |
| `maa_api/db/repositories/trace.py` 的 `TraceSpanRepository` | 追踪的读写与清理 | 02 §4.1 |
| `run_retention` 返回值的 `trace_spans_deleted` | 保留策略 | 03 §14 |
| 评测模块的公开名字：`Scenario`、`load_scenarios`、`Cassette`、`CassetteTransport`、`RecordingTransport`、`CountingTransport`、`GradeResult`、`EvalRunner`、`EvalOptions`、`EvalVariant`、`EvalEnvironment`、`SuiteResult`、`write_result`、`INDEX_FILE_NAME`、`DRIVERS` | M70 加驱动、M98 加变体时引用 | 02 §4.1 |
| `tests/fakes/fake_core.py`、`tests/evals/environment.py` 的 `FakeWorldEnvironment` | 假世界 | 02 §4.3 |
| 环境变量 `MAA_EVAL_BASE_URL`、`MAA_EVAL_MODEL`、`MAA_EVAL_API_KEY` | record、live 模式的模型配置，不是设置项 | 04 §6 的说明段 |
| 前端 `keys.agent.runs.list`、`runs.all`、`runs.spans`、`costs`（`specs`、`runs.detail` 与 M72 spec 同形） | 运行追踪与成本页 | 11 §6 |
| Dockerfile 构建参数 `MAA_EXTRAS` | 镜像按需装 extra（16 §12 要求第一个引入 extra 的里程碑加） | 16 §12 |
