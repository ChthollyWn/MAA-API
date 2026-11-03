# M65 代理定义与通用循环（规格）

> 阶段：P0　依赖：M64　分支：`agentspec-harness`　迁移：无
> 本文是 M65 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

本里程碑把 `maa_api/agent/runtime.py`（约 3000 行）里的「模型 → 工具 → 模型」循环拆进 `maa_api/agent/harness/`，并引入代理定义（AgentSpec）。这是 v3 风险最高的一步，规则只有一条：对外行为一处不改。v2 会话的 SSE 事件名与字段、落库消息、审计、确认、提问、中断、自动诊断与侦察报告，拆分前后逐字一致。新增的对外面只有两个只读端点和一个错误码。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支，基点是 dev | `git status -sb && git merge-base --is-ancestor dev HEAD && echo ok` | `## agentspec-harness`，工作区干净，末行 `ok` |
| 2 | M64 已合并 | `git tag -l 'v3-m64'` | `v3-m64` |
| 3 | runtime 还没拆 | `wc -l < maa_api/agent/runtime.py` | 2900–3100（2026-11-02 为 2986） |
| 4 | v2 系统提示词没变 | `.venv/bin/python -c "import hashlib; from maa_api.agent.runtime import SYSTEM_PROMPT as p; print(hashlib.sha256(p.encode()).hexdigest())"` | `664c320349ef13cf3fdbc1f1d6969b85eebf7c7ffe980cd656c1b03381b72536` |
| 5 | 侦察提示词没变 | `.venv/bin/python -c "import hashlib; from maa_api.agent.recon.runner import SYSTEM_PROMPT as p; print(hashlib.sha256(p.encode()).hexdigest())"` | `c321d4483a9824cdd517d8844a37a558c4d3269a2f51151c45d960be214b9bda` |
| 6 | 要搬的方法都在 | `rg -c -e "async def _run_turn_body\(" -e "async def _execute_tool_batch\(" -e "async def _ask_user\(" -e "async def _finish_interrupted_turn\(" -e "async def _complete\(" -e "async def _persist_message\(" maa_api/agent/runtime.py` | `6` |
| 7 | 要搬的函数都在 | `rg -c -e "^def build_model_context\(" -e "^def merge_tool_call_deltas\(" -e "^def sse_event\(" maa_api/agent/runtime.py` | `3` |
| 8 | 新包还不存在 | `ls -d maa_api/agent/harness maa_api/agent/specs` | 两行 `No such file or directory` |
| 9 | 工具定义还没有扩展字段 | `rg -c -e device -e rest_visible maa_api/agent/registry.py; echo $?` | 只输出 `1`（没有匹配） |
| 10 | 测试已改读 v3 现行清单 | `rg -c "14-现行接口清单" tests/domain/test_errors.py tests/domain/test_route_inventory.py` | 两个文件各 `1` |
| 11 | 错误码计数 | `rg -n -e "^ALL_CODE_COUNT" -e "^HTTP_BOUND_CODE_COUNT" tests/domain/test_errors.py` | `= 124` 与 `= 123` |
| 12 | 路由总数，新码还没加 | `rg -n "^共 [0-9]+ 条 HTTP 端点" docs/v3/14-现行接口清单.md; rg -c AGENT_SPEC_NOT_FOUND maa_api docs/v3/14-现行接口清单.md; echo $?` | 一行 `共 121 条…`，然后只输出 `1` |
| 13 | 新函数名在 agent 组内不重名 | `rg -c -e "def list_specs\b" -e "def get_spec\b" maa_api/api/routers; echo $?` | 只输出 `1` |
| 14 | 测试基线 | `.venv/bin/python -m pytest tests/agent/ > /tmp/m65-pre.log 2>&1; echo $?; tail -n 1 /tmp/m65-pre.log` | `0`；汇总行记进台账（2026-11-02 为 `686 passed, 1 skipped, 2 xfailed`） |
| 15 | 冒烟基线 | `.venv/bin/python scripts/agent_smoke.py > /tmp/m65-pre-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m65-pre-smoke.log` | `0` 与 `SMOKE OK` |
| 16 | pytest 默认参数 | `rg -n "^addopts" pytest.ini` | `addopts = -q`（命令行再加 `-q` 就不打印汇总行，所以本文的 pytest 命令都不带 `-q`） |

第 4、5 条对不上说明有人在 M64 之后改了提示词，本 spec 的哈希与生成脚本都已过时，停下来报告。

## 1. 背景与目标

v2 的循环、提问特判、卡死检测、确认等待、中断收尾、上下文裁剪都写在 `AgentRuntime` 里；v3 的压缩、记忆、技能、租约要按顺序插进去，必须先把循环与特判拆开（V3-D31）。做完后：

1. `maa_api/agent/specs/builtin/` 有三份内置代理定义：`legacy_chat`、`legacy_incident`、`recon`。启动时逐条校验（05 §1.2 的全部规则），任何一条不合法都中止启动；每份定义有 12 位版本哈希。
2. `AgentLoop`（`harness/loop.py`）执行一次运行；v2 的提问、卡死检测、确认、上下文组装分别成为 `ClarificationMiddleware`、`LoopGuardMiddleware`、`PolicyMiddleware`、`ContextLayoutMiddleware`，另有空实现的 `TraceMiddleware`。
3. v2 会话（`stream_message`）用 `legacy_chat`、自动诊断（`run_incident`）用 `legacy_incident`、侦察（`run_recon`）用 `recon`，三者都跑在 `AgentLoop` 上。
4. `runtime.py` 变成门面：会话锁、中断状态、能力探查、标题、失败说明、状态摘要与知识段留下，其余搬走；行数降到 1900 以下。
5. `ToolDefinition` 增加 `device`、`refetchable`、`untrusted_output`、`rest_visible` 四个字段，v2 工具按 05 §6 赋 `device`；REST 工具目录与调用入口按 `rest_visible` 过滤。
6. 新端点 `GET /api/agent/specs`、`GET /api/agent/specs/{name}`（Pydantic 响应模型），新错误码 `AGENT_SPEC_NOT_FOUND`。
7. 不回退可以机械验证：`tests/agent/` 现有用例一个断言不改全部通过；新增的行为对齐测试在搬家前录下 16 个场景的 SSE 帧、模型请求与落库结果，之后每个任务都逐字比对。

## 2. 非目标

- 不建 `agent_run` 表，不持久化运行、会话锁与中断状态（M66）；`_locked_sessions`、`_interrupted` 等仍在进程内存。
- 不做模型档案、角色路由、前缀缓存布局（M67）；`ContextLayoutMiddleware` 原样包装 `build_model_context`，`collect_completion` 仍用 v2 的 `client_factory`。
- `TraceMiddleware` 只放空实现（M68）。
- 不加工单、总控、Worker、`delegate` 工具（M69–M70）；`recon` 仍只由 `delegate_recon` 调用。
- 不改提示词正文、工具描述、工具集合、风险分级、确认与提问口径、SSE 事件。
- 不修 v2 的已知怪行为（§4.5.3），发现新的也不修。
- 没有界面改动；前端只重新生成类型。

## 3. 相关文档

- v3 契约：[02 §2.2、§4.1、§5](../02-系统架构.md)；[04 §1、§2.1、§5、§7](../04-接口与事件协议.md)；[05 §1–§3.1、§5、§6](../05-运行时与代理定义.md)；[12 §4、§6](../12-实施路线与里程碑.md)；[13 V3-D30、V3-D31](../13-决策记录.md)；[接缝速查 §1–§3、§11](./接缝速查.md)。
- v2 行为来源（只读）：[v2/11 §5.3、§5.4、§8.2、§8.3、§8.6](../../v2/11-Agent模块设计.md)，定位 `rg -n "^### (5\.3|5\.4|8\.2|8\.3|8\.6) " docs/v2/11-Agent模块设计.md`；[v2/13 §8.16、§8.71、§8.79、§8.80](../../v2/13-决策记录.md)，定位 `rg -n "^### 8\.(16|71|79|80) " docs/v2/13-决策记录.md`；[M48 侦察规格](../../v2/superpowers/specs/2026-10-14-m48-readonly-recon-subagent.md) §3 的 D4、D5；[M22 中断规格](../../v2/superpowers/specs/2026-09-29-m22-agent-interrupt.md)；[M21 提问规格](../../v2/superpowers/specs/2026-09-29-m21-agent-question-card.md)。
- 既有代码入口（行号为 2026-11-02 的近似值，用符号名定位）：
  - `maa_api/agent/runtime.py`：`AgentRuntime`（约 554）、`_run_turn_body`（约 1182）、`_execute_tool_batch`（约 1611）、`_ask_user`（约 1831）、`_finish_interrupted_turn`（约 1481）、`_complete`（约 1557）、`build_model_context`（约 314）、`SYSTEM_PROMPT`（234–258）。完整清单见 §4.2.2。
  - `maa_api/agent/recon/runner.py`：`SYSTEM_PROMPT`（35–46）、`ReconBudget`（约 62）、`run_recon`（约 176）、`_default_step_runner`（约 278）、`_call_parts`（约 365）、`_report`（约 394）。
  - `maa_api/agent/incidents.py`：`INCIDENT_ALLOWED_TOOLS` 的导入（约 21）、`_create_and_run` 里调 `run_incident`（约 451）。
  - `maa_api/agent/registry.py`：`ToolDefinition`（39–70）、`ToolRegistry.get`（约 95）。
  - `maa_api/agent/knowledge/__init__.py`：`_FRONTMATTER`（约 155）、`_parse_builtin`（约 520）、`load_builtins`（约 556），定义加载照这个写法。
  - `maa_api/main.py`：`confirmation_factory(..., build_registry(), ...)`（约 1146–1172）、`app.state.agent_runtime = agent_runtime`（约 1209）、`include_router`（约 1523–1544）。
  - `maa_api/api/routers/agent.py`：`list_tools`（约 391）、`invoke_tool`（约 442）；`maa_api/api/routers/knowledge.py` 是 Pydantic 响应模型的写法范例。
- 测试里依赖门面内部的地方（拆分时必须继续生效，逐项见 §4.2.3）：`tests/agent/test_runtime.py` 约 135、1751、1876、3166、3659、4008–4019、4076、4118、4464、4559、4623、4638 行；`tests/agent/test_incidents.py` 约 596–613、657 行；`tests/agent/test_tool_catalog.py` 约 98、479、528 行；`tests/agent/test_recon_delegate.py` 约 962 行。

## 4. 设计

### 4.1 数据模型

无。运行不落库（M66 才建 `agent_run`），`spec_version` 只在端点里返回。

### 4.2 模块与文件

#### 4.2.1 目录与依赖方向

```
maa_api/agent/
├── runtime.py                 门面（瘦身后 ≤ 1900 行）
├── harness/
│   ├── __init__.py            只有模块说明
│   ├── context.py             RunContext、ModelRequest、ModelResponse、ToolCall、ToolOutcome、RunOutcome、Usage、TurnDraft、StopRun
│   ├── middleware.py          Middleware、MiddlewareChain
│   ├── builtin_middleware.py  build_model_context 及其辅助；Trace、ContextLayout、LoopGuard、Clarification、Policy 五个中间件
│   ├── events.py              RunEvent、RunEventBus、sse_event
│   ├── chunks.py              流式 chunk 解析、工具调用与参数解析、collect_completion
│   ├── toolexec.py            execute_batch 与三道闸门
│   └── loop.py                AgentLoop、Transcript、MemoryTranscript、select_stop
├── specs/
│   ├── __init__.py            只有模块说明
│   ├── schema.py              AgentSpec 等 Pydantic 模型
│   ├── registry.py            加载、校验、版本哈希、AgentSpecRegistry
│   └── builtin/legacy_chat.md、legacy_incident.md、recon.md
└── recon/runner.py            run_recon 改用 AgentLoop
maa_api/api/routers/agent_specs.py   两个端点
```

依赖只能单向：`specs` 不导入 `harness`；`harness` 只导入 `specs.schema`、`specs.registry` 与 v2 的底层模块，绝不导入 `maa_api.agent.runtime`；`runtime.py` 与 `recon/runner.py` 导入 `harness`。`tests/agent/harness/test_harness_runtime_facade.py` 用文本检查钉住这一条。

#### 4.2.2 搬家清单

「任务」列是搬动发生的任务；「再导出」为是时 `runtime.py` 用显式 `from … import …` 留住这个名字（同一个对象）。行号是 `runtime.py` 里的约数。没列出的名字一律留在 `runtime.py`。

| 原符号（行） | 去向 | 新名字 | 任务 | 再导出 |
|---|---|---|---|---|
| `_TurnDraft`（59） | `harness/context.py` | `TurnDraft` | T5 | 否 |
| `DEFAULT_MAX_CONTEXT_CHARS`（88）、`IMAGE_CONTEXT_CHARS`（91）、`TOOL_RESULT_PRUNE_THRESHOLD/HEAD/TAIL`（94–96）、`HISTORICAL_REASONING_CHARS`（157）、`SCREENSHOT_RESULT_TOOLS`（218）、`AGENT_SCREENSHOT_GRID`（2557） | `builtin_middleware.py` | 不变 | T6 | 是 |
| `QUESTION_EXPIRED_TEXT`、`QUESTION_SKIPPED_TEXT`（101–102）、`QUESTION_UNANSWERED_REASONS`（107） | `builtin_middleware.py` | 不变 | T7 | 前两个是 |
| `QUESTION_UNANSWERED_KEY`（106） | 删除 | 由 `ToolOutcome.stop_reason` 取代 | T7 | 否 |
| `STUCK_REPEAT_LIMIT`（124）、`STUCK_EXEMPT_TOOLS`（127） | `builtin_middleware.py` | 不变 | T7 | 是 |
| `INTERRUPTED_FINISH_REASON`（160）、`INTERRUPTED_NOTICE`（162）、`INTERRUPTED_TOOL_STUB`（约 168） | `loop.py` | 不变 | T8 | 是 |
| `SCREEN_TOOL_NAMES`（198）、`_VISION_DISABLED_MESSAGES`（212）、`_DEFAULT_VISION_DISABLED_MESSAGE`（219） | `toolexec.py` | 不变 | T7 | 第一个是 |
| `SYSTEM_PROMPT` 的正文（234–258） | `specs/builtin/legacy_chat.md`、`legacy_incident.md` | 名字留在 runtime，值改为从定义读 | T3 | 本来就在 |
| `INCIDENT_ALLOWED_TOOLS` 的字面量（135–150） | `specs/builtin/legacy_incident.md` | 名字留在 runtime，值改为 `frozenset(定义.tools)` | T3 | 本来就在 |
| `merge_tool_call_deltas`（286） | `chunks.py` | 不变 | T5 | 是 |
| `build_model_context`（314） | `builtin_middleware.py` | 不变 | T6 | 是 |
| `AgentRuntime._finish_interrupted_turn`（1481） | `loop.py` | `AgentLoop._finish_interrupted` | T8 | 否 |
| `AgentRuntime._persisted_tool_call_ids`（1543） | `runtime.py` 内的 `_LegacyTranscript` | `persisted_tool_call_ids` | T8 | 否 |
| `AgentRuntime._complete`（1557） | `chunks.py` | `collect_completion`（模块函数） | T5 | 否 |
| `AgentRuntime._execute_tool_batch`（1611） | 批调度与闸门进 `toolexec.py`；策略、确认、等待那一段进 `PolicyMiddleware` | `execute_batch`、`PolicyMiddleware.wrap_tool` | T7 | 否 |
| `AgentRuntime._ask_user`（1831） | `builtin_middleware.py` | `ClarificationMiddleware.wrap_tool` | T7 | 否 |
| `AgentRuntime._finish_with_text`（1960） | `loop.py` | `AgentLoop._finish_stop` | T8 | 否 |
| `AgentRuntime._archive_screenshot_result`（2363，方法体不用 `self`） | `builtin_middleware.py` | `archive_screenshot_result`（模块函数） | T7 | 否 |
| `sse_event`（2458） | `events.py` | 不变 | T4 | 是 |
| `_trim_historical_reasoning`（2463）、`_message_dict`（2498）、`_screenshot_payload`（2512）、`_replace_screenshot_payload`（2529）、`_screenshot_data_url`（2545）、`_model_screenshot_data_url`（2560）、`_turn_groups`（2664）、`_has_complete_tool_round`（2673）、`_context_size`（2686）、`_message_effective_chars`（2703）、`_content_effective_chars`（2715）、`_prune_oversized_tool_results`（2744）、`_is_complete_tool_exchange`（2774）、`_drop_oldest_tool_rounds`（2803） | `builtin_middleware.py` | 不变 | T6 | 只有 `_context_size` 是；`_message_dict`、`_screenshot_payload` 由 runtime 导入自用 |
| `_chunk_tool_calls`（2827） | `chunks.py` | `chunk_tool_calls` | T5 | 否（`_probe_capabilities` 改用新名） |
| `_parse_tool_call`（2840） | `chunks.py` | `parse_tool_call`（返回 `ToolCall`） | T7 | 否 |
| `_parse_arguments`（2851） | `chunks.py` | `parse_arguments` | T7 | 否 |
| `_json_text`（2861）、`_app_error_wire`（2898） | `toolexec.py` | `json_text`、`app_error_wire` | T7 | 否 |
| `_vision_disabled_message`（2865） | `toolexec.py` | `vision_disabled_message` | T7 | 否 |
| `_exempt_from_stuck_loop`（2873）、`_calls_signature`（2884） | `builtin_middleware.py` | `exempt_from_stuck_loop`、`calls_signature` | T7 | 否 |
| `_request_id`（2906） | `builtin_middleware.py` | `request_id_of` | T7 | 否 |
| `AgentRuntime._run_turn_body`（1182） | 留在 runtime，T7 改调 `execute_batch`，T8 改成「v2 前置步骤 + `AgentLoop.run`」 | 不变 | T7、T8 | 本来就在 |

留在 `runtime.py` 的（不搬）：`DEFAULT_MAX_ITERATIONS`、`DEFAULT_CONFIRMATION_POLL_SECONDS`、`TITLE_*`、`KNOWLEDGE_UNAVAILABLE`、`RECON_MAX_STEPS_CAP`、`RECON_TIMEOUT_SECONDS`、`SSE_KEEPALIVE_SECONDS`、`INTERRUPT_GRACE_SECONDS`、`CAPABILITY_FAILURE_TTL_SECONDS`、`TURN_FAILED_FINISH_REASON`、`TURN_FAILURE_NOTICE_*`、`TURN_FAILURE_GENERIC_MESSAGE`、`_SSE_END`、`_VISION_PROBE_*`、`call_runtime_factory`、`MODEL_SCHEMA_DROPPED_KEYS`、`_compact_model_schema`、`_local_resource_version`、`_clean_title`、`ERROR_BRIEF_MAX_CHARS`、`_error_brief`、`_capability_status`、`_probe_degraded_reason`，以及 `AgentRuntime` 的其余方法：`__init__`、`acquire_session`、`release_session`、`turn_running`、`interrupt_turn`、`_interrupted_now`、`_clear_interrupt`、`_abort_in_flight_calls`、`ensure_configured`、`stream_message`、`run_incident`、`_save_incident_failure`、`_forget_turn_task`、`_forget_queue_waiter`、`_cached_capabilities`、`_remember_capabilities`、`check_capabilities`、`recon`、`_recon_settings`、`_probe_capabilities`、`_produce_turn`、`_record_turn_failure`、`_run_turn`、`_persist_message`、`_generate_session_title`、`_read_title`、`_effective_settings`、`_make_client`、`_tool_schemas`、`enabled_tool_groups`、`enable_tool_groups`、`session_allowed_tools`、`tool_group_catalog`、`_knowledge_context`、`_status_summary`、`_effective_channel`、`_hydrate_latest_screenshot`。`runtime.py` 新增 `_LegacyTranscript` 类与 `AgentRuntime._legacy_chain()`。`__all__` 保持原来 8 个名字不变。

#### 4.2.3 门面必须保住的名字与钩子

| 谁在用 | 名字或钩子 | 怎么保住 |
|---|---|---|
| 测试 `from maa_api.agent.runtime import …` | `AgentRuntime`、`build_model_context`、`merge_tool_call_deltas`、`DEFAULT_MAX_CONTEXT_CHARS`、`HISTORICAL_REASONING_CHARS`、`_context_size`、`_VISION_PROBE_MARKER`、`_VISION_PROBE_PNG`、`_local_resource_version`、`TITLE_MAX_CHARS`、`_clean_title`、`KNOWLEDGE_UNAVAILABLE`、`SYSTEM_PROMPT`、`QUESTION_EXPIRED_TEXT`、`QUESTION_SKIPPED_TEXT`、`INTERRUPTED_NOTICE`、`RECON_MAX_STEPS_CAP`、`INCIDENT_ALLOWED_TOOLS` | 留在 runtime 或按 §4.2.2 再导出 |
| `main.py`、`routers/agent.py` | `AgentRuntime`、`call_runtime_factory` | 留在 runtime |
| `incidents.py`、`scripts/agent_smoke.py` | `INCIDENT_ALLOWED_TOOLS` | 留在 runtime |
| 测试改模块全局 | `runtime.SSE_KEEPALIVE_SECONDS`、`runtime.CAPABILITY_FAILURE_TTL_SECONDS` | 两个常量留在 runtime，读它们的 `stream_message`、`_remember_capabilities` 也留在 runtime，调用时按模块全局读取 |
| 测试按记录器名抓日志 | 「知识库…降级」、「Agent 轮次失败」、「Agent 能力探查失败」 | `_knowledge_context`、`_produce_turn`、`_probe_capabilities` 留在 runtime |
| 测试在构造后替换实例属性 | `runtime.confirmation_service`、`runtime.client_factory`、`runtime.question_service`、`runtime.settings_provider` | 门面每轮开始时才读这些属性去建链、建客户端（§4.2.11），harness 不在构造期缓存它们 |
| 测试替换 `runtime._persist_message` | 中断补桩用例 | `_LegacyTranscript.append` 每次都现取 `self._runtime._persist_message`，并且把 `session_id` 作为第一个位置参数传，其余全用关键字 |
| 测试替换 `confirmations.invoke` | 诊断白名单用例 | `PolicyMiddleware` 每次调用都现取 `confirmation_service.invoke` |
| 测试的替身确认服务 | `invoke(name, arguments, context, *, mode, on_audit_prepare=None, on_audit_terminal=None, on_audit_committed=None)` | `PolicyMiddleware` 只传 `mode="async"`、`on_audit_prepare`、`on_audit_committed` 三个关键字，与 v2 一致，不加新关键字 |
| 构造参数为 `None` 的门面 | `AgentRuntime(None, None)._tool_schemas(...)`、`object.__new__(AgentRuntime)._tool_schemas(...)`、`AgentRuntime(None, None, ...).recon(...)` | `__init__` 不加任何会读库、读注册表或加载代理定义的逻辑；`_tool_schemas` 不读任何实例属性；recon 路径不碰确认服务与会话库 |
| 测试直接调私有方法 | `_status_summary`、`_effective_settings`、`_record_turn_failure`、`_tool_schemas`、`_cached_capabilities`、`_remember_capabilities`、`_capability_cache`、`_locked_sessions`、`_interrupted` | 全部留在门面，签名不变 |
| `tests/fakes/fake_llm.py` 的 `FakeLLM.turns` | 按「第一条是 system」区分对话请求 | `ContextLayoutMiddleware` 产出的第一条仍是 system |

#### 4.2.4 `harness/context.py`

```python
EmitFn = Callable[[str, Any], Awaitable[None]]
ToolStatus = Literal["ok", "error", "awaiting", "rejected", "expired", "skipped"]

@dataclass(slots=True)
class TurnDraft:
    """当前这次模型调用已经流出、还没落库的内容；中断收尾靠它补历史。"""
    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    def reset(self) -> None: ...                     # 三个列表各自 clear()，不换对象
@dataclass(slots=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    raw_arguments: Any                               # 模型给的原文，通常是 JSON 字符串
    arguments: dict[str, Any] | None = None          # execute_batch 解析后用 dataclasses.replace 填入
@dataclass(slots=True)
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    profile: Any = None                              # M67
    response_format: dict[str, Any] | None = None
    estimated_tokens: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
@dataclass(slots=True)
class ModelResponse:
    text: str
    reasoning: str
    usage: Usage
    upstream_finish_reason: str | None
    latency_ms: int
    raw_tool_calls: list[dict[str, Any]] = field(default_factory=list)   # 合并后的 OpenAI 形态，原样落库
    tool_calls: list[ToolCall] = field(default_factory=list)             # loop 从 raw_tool_calls 解析
@dataclass(slots=True)
class ToolOutcome:
    status: ToolStatus
    result: Any = None
    error: dict[str, Any] | None = None
    audit_id: int | None = None
    message_id: int | None = None                    # loop 落库后回填
    stop_run: bool = False
    stop_reason: str | None = None
    stop_text: str | None = None                     # 结束本轮时写给用户的那句话
    content: str | None = None                       # tool 消息正文；None 时 loop 用 json_text(body())
    def body(self) -> dict[str, Any]:
        return {"error": self.error} if self.error is not None else {"result": self.result}
@dataclass(slots=True)
class RunOutcome:
    status: str                                      # succeeded / failed / interrupted；M66 改用 AgentRunStatus
    finish_reason: str
    final_text: str | None = None
    report_id: int | None = None
    usage: Usage = field(default_factory=Usage)
class StopRun(Exception):
    """before_model / after_model 里抛出，loop 立即结束本次运行，不落当前这次响应。"""
    def __init__(self, reason: str, *, text: str | None = None, status: str = "succeeded") -> None: ...
def _never() -> bool: return False
async def _discard(_name: str, _data: Any) -> None: return None
@dataclass(slots=True)
class RunContext:
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    session_id: str | None
    ticket_id: str | None
    spec: AgentSpec
    caller: CallerType = CallerType.INTERNAL
    app: Any = None
    request: Any = None
    emit: EmitFn = _discard                          # 默认丢弃；v2 会话是 RunEventBus.emit
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    state: dict[str, Any] = field(default_factory=dict)   # 键用中间件 name
    steps: int = 0
    inbox_items: list[Any] = field(default_factory=list)
    interrupted_now: Callable[[], bool] = _never     # v2 会话接 AgentRuntime._interrupted_now(session_id)
    allowed_tools: frozenset[str] | None = None      # 执行期白名单；None 不限（v2 普通会话）
    vision_enabled: bool = True
    parallel_tools: bool = True                      # False 时只读批也串行（recon）
    draft: TurnDraft = field(default_factory=TurnDraft)
    pending_stop: tuple[str, str | None] | None = None
    def stop_after_batch(self, reason: str, text: str | None = None) -> None:
        """当前这批工具执行、落库完之后结束本次运行。"""
        self.pending_stop = (reason, text)
```

`interrupted_now` 必须原样转调 `AgentRuntime._interrupted_now`：它在返回真的同时把会话登记进 `_interrupt_settling`，`interrupt_turn` 据此不再二次 `cancel()`，否则收尾会被打断成半截。v2 的中断靠 `task.cancel()`，M65 里没有任何代码设置 `cancel_event`。

#### 4.2.5 `harness/middleware.py`

```python
ALL_AGENT_KINDS: frozenset[str] = frozenset({"orchestrator", "worker", "subagent", "background"})
CallNext = Callable[[ToolCall], Awaitable[ToolOutcome]]

class Middleware:
    name: ClassVar[str] = "middleware"
    applies_to: ClassVar[frozenset[str]] = ALL_AGENT_KINDS
    async def on_run_start(self, ctx: RunContext) -> None: return None
    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest: return req
    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse: return resp
    async def wrap_tool(self, ctx: RunContext, call: ToolCall, call_next: CallNext) -> ToolOutcome:
        return await call_next(call)
    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None: return None

class MiddlewareChain:
    def __init__(self, middlewares: Sequence[Middleware]) -> None: ...      # 不过滤，保持顺序
    @classmethod
    def for_kind(cls, kind: str, middlewares: Sequence[Middleware]) -> MiddlewareChain: ...  # 只留 applies_to 含 kind 的
    @property
    def names(self) -> tuple[str, ...]: ...
    # on_run_start、before_model（逐个替换 req）正序；after_model、on_run_end 逆序；签名同 Middleware
    async def wrap_tool(self, ctx, call, innermost: CallNext) -> ToolOutcome: ...  # 洋葱：第一个在最外层
```

`toolexec` 调 `wrap_tool` 时传的 `innermost` 直接抛 `RuntimeError("中间件链里没有执行器：<工具名>")`：链里必须有一个不调 `call_next` 的执行器（v2 会话是 `PolicyMiddleware`，recon 是 `ReconDispatchMiddleware`）。

#### 4.2.6 `harness/events.py`

```python
@dataclass(frozen=True, slots=True)
class RunEvent:
    name: str          # v2 的 SSE 事件名，见 §4.3.3
    data: Any          # v2 的 data，原样
    run_id: str | None = None
RunEventSink = Callable[[RunEvent], Awaitable[None]]
class RunEventBus:
    def __init__(self, *, run_id: str | None = None) -> None: ...
    def subscribe(self, sink: RunEventSink) -> None: ...
    async def emit(self, name: str, data: Any) -> None: ...   # 按订阅顺序逐个 await，异常照常上抛
def sse_event(event: str, data: Any) -> str: ...   # v2 原样：先 redact_remote_data，再紧凑 JSON
```

M65 只有一种订阅者：门面把 v2 的 `emit` 包成 sink（§4.2.11）。诊断与侦察的总线没有订阅者，事件直接丢弃，与 v2 的 `ignore_event` 等价。`run_id` 不进 v2 的 SSE 帧。

#### 4.2.7 `harness/chunks.py`

```python
def merge_tool_call_deltas(deltas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]: ...  # v2 原样
def chunk_tool_calls(chunk: Mapping[str, Any]) -> list[dict[str, Any]]: ...                    # v2 _chunk_tool_calls
def parse_tool_call(call: Mapping[str, Any]) -> ToolCall: ...
    # v2 _parse_tool_call 的校验与报错一字不改；返回 ToolCall(id, name, raw_arguments=function.get("arguments", "{}"))
def parse_arguments(value: Any) -> dict[str, Any]: ...                                         # v2 _parse_arguments
async def collect_completion(client: Any, model: str, messages: Sequence[dict[str, Any]],
                             tools: Sequence[dict[str, Any]], *, emit: EmitFn,
                             draft: TurnDraft | None = None) -> ModelResponse: ...
```

`collect_completion` 是 `_complete` 的函数版：逐块 `emit("text_delta", {"text": value})`，文本与思考直接 append 到 `draft.text`、`draft.reasoning`（中断就发生在这个循环里），只收非负的 `prompt_tokens`、`completion_tokens`，最后返回 `ModelResponse(text, reasoning, Usage(...), finish_reason, latency_ms, raw_tool_calls=merge_tool_call_deltas(片段))`。非 Mapping 的块照旧抛 `LLM_REQUEST_FAILED`「LLM 返回了无效的响应块」。

#### 4.2.8 `harness/toolexec.py`

```python
SCREEN_TOOL_NAMES: frozenset[str]                     # v2 原值
HIDDEN_TOOL_MESSAGE = "内置 Agent 不提供该工具，请用 get_screenshot"
TOOL_NOT_ALLOWED_MESSAGE = "此自动诊断会话不允许调用该工具"
SKIPPED_AFTER_REJECTION_MESSAGE = "前一项操作未获确认，本项未执行。"
CONFIRMATION_TERMINAL_CODES = frozenset({"CONFIRMATION_REJECTED", "CONFIRMATION_EXPIRED"})
CONFIRMATION_TERMINAL_TEXT = "确认被拒绝或过期了，我不会重试。要换个方案吗？"

def vision_disabled_message(tool_name: str) -> str: ...
def app_error_wire(error: AppError) -> dict[str, Any]: ...
def json_text(value: Any) -> str: ...
async def execute_batch(ctx: RunContext, calls: Sequence[ToolCall], *,
                        chain: MiddlewareChain, registry: ToolRegistry) -> list[ToolOutcome]: ...
```

`execute_batch` 的算法（与 v2 `_execute_tool_batch` 逐步对应）：

```
parsed = [replace(c, arguments=parse_arguments(c.raw_arguments)) for c in calls]   # 坏 JSON 在派发前整批抛 AppError
parallel = ctx.parallel_tools and parsed 非空 and 每个 registry.get(name).read_only 为真（取不到按 False）
run(call):
    await ctx.emit("tool_start", {"tool_call_id": call.id, "name": call.name, "arguments": call.arguments})
    闸门 1：registry.get(name).model_visible 为假（取不到就跳过此闸） → error TOOL_NOT_FOUND / HIDDEN_TOOL_MESSAGE
    闸门 2：ctx.allowed_tools 不为 None 且 name 不在其中 → error TOOL_NOT_FOUND / TOOL_NOT_ALLOWED_MESSAGE
    闸门 3：not ctx.vision_enabled 且 name in SCREEN_TOOL_NAMES → error LLM_REQUEST_FAILED / vision_disabled_message(name)
    否则 return await chain.wrap_tool(ctx, call, innermost)
parallel 时 asyncio.gather 全部；否则逐个执行，某个结果的 error.code 在 CONFIRMATION_TERMINAL_CODES 里时，
    余下每个调用补 ToolOutcome(status="skipped", error={"code": 同一个码, "message": SKIPPED_AFTER_REJECTION_MESSAGE})，不再执行
最后：error.code 在 CONFIRMATION_TERMINAL_CODES 里且还没 stop_run 的结果，置 stop_run=True、
    stop_reason="confirmation_resolved"、stop_text=CONFIRMATION_TERMINAL_TEXT
```

闸门在中间件链外面、`tool_start` 之后，顺序就是 v2 的顺序：诊断会话里模型调 `ask_user` 时先被白名单拦下，不会建提问。被闸门拦下的调用不进确认服务、不落审计。

#### 4.2.9 `harness/builtin_middleware.py`

上半部分是 §4.2.2 列出的上下文组装代码，原样搬入；`build_model_context` 的 `system_prompt` 默认值改为模块常量 `_DEFAULT_SYSTEM_PROMPT = builtin_spec("legacy_chat").body`（与 `runtime.SYSTEM_PROMPT` 是同一个字符串）。下半部分是五个中间件：

| 中间件 | name | applies_to | 钩子 | 来自 v2 的哪段 | 要点 |
|---|---|---|---|---|---|
| `TraceMiddleware` | `trace` | 全部 | 无（全用基类默认） | 无 | M68 填实现 |
| `ContextLayoutMiddleware` | `context_layout` | 全部 | `before_model` | `_run_turn_body` 里调 `build_model_context` 那几行（约 1302–1309） | `req.messages = build_model_context(req.messages, system_prompt=ctx.spec.body, knowledge_context=…, status_summary=…, max_context_chars=…, envelope_chars=len(json.dumps(req.tools, ensure_ascii=False)), vision_enabled=ctx.vision_enabled)`；三个值取自 `ctx.state["context_layout"]`，由门面每轮写一次；溢出时照旧抛 `LLM_CONTEXT_OVERFLOW` |
| `LoopGuardMiddleware` | `loop_guard` | 全部 | `after_model` | `_run_turn_body` 里的打转检测（约 1438–1455） | 见下 |
| `ClarificationMiddleware` | `clarification` | orchestrator、worker | `wrap_tool` | `_ask_user`（约 1831–1906） | 不是 `ask_user` 就 `call_next`；其余逻辑原样，`self.question_service` 换成构造参数，`emit` 换成 `ctx.emit`，`self._interrupted_now(session_id)` 换成 `ctx.interrupted_now()` |
| `PolicyMiddleware` | `policy` | 全部 | `wrap_tool`（执行器，不调 `call_next`） | `_execute_tool_batch` 内 `run()` 从建 `ToolContext` 到返回结果的那段（约 1683–1803） | 等待循环一字不改；`self.confirmation_poll_seconds` 换成构造参数 |

```python
class LoopGuardMiddleware(Middleware):
    name = "loop_guard"
    async def after_model(self, ctx, resp):
        if not resp.tool_calls:
            return resp
        calls = [{"name": c.name, "arguments": c.raw_arguments} for c in resp.tool_calls]
        if exempt_from_stuck_loop(calls):          # 整批只有 wait_pipeline / wait_update：不计数，也不清零
            return resp
        state = ctx.state.setdefault(self.name, {"last_signature": None, "repeats": 0})
        signature = calls_signature(calls)         # 用原始参数字符串，与 v2 一样
        state["repeats"] = state["repeats"] + 1 if signature == state["last_signature"] else 0
        state["last_signature"] = signature
        if state["repeats"] >= STUCK_REPEAT_LIMIT:
            ctx.stop_after_batch("stuck_loop", STUCK_LOOP_TEXT)
        return resp
STUCK_LOOP_TEXT = "连续几轮发出了一样的工具调用，看来拿不到新信息了。我先停下，告诉我下一步查什么。"

class ClarificationMiddleware(Middleware):
    name = "clarification"
    applies_to = frozenset({"orchestrator", "worker"})
    def __init__(self, *, question_service: Any, registry: ToolRegistry) -> None: ...
    # 服务缺失 → error AGENT_DISABLED「提问服务尚未启动」，audit_id None；参数不合法 → error 加 details 里的 audit_id；
    # answered → ok，result={"status": "answered", "selected": [...], "question_id": …}；skipped/expired → ok，
    # result={"status": 原因, "question_id": …}，stop_run、stop_reason="question_unanswered"、stop_text 为对应的 QUESTION_*_TEXT
class PolicyMiddleware(Middleware):
    name = "policy"
    def __init__(self, *, confirmation_service: Any, session_factory: Any, poll_seconds: float) -> None: ...
    # invoke 抛 AppError → error 加 details 里的 audit_id；等待中被拒或过期 → status rejected / expired；
    # 幂等错误 → error=body["__idempotency_error__"]；成功 → ok，result=body.get("result")；其余错误 status=error
def request_id_of(request: Any) -> str | None: ...                                 # v2 _request_id
async def archive_screenshot_result(result: Mapping[str, Any], db_session: AsyncSession) -> dict[str, Any]: ...
```

LoopGuard 在 `after_model` 只记指纹并预约结束：v2 的第三批相同调用是先执行、落库，再以 `stuck_loop` 收尾的（`test_tool_loop_stops_when_the_model_repeats_the_same_calls` 断言 `invoked == [1, 1, 1]`），在 `after_model` 直接结束会少执行一批。

#### 4.2.10 `harness/loop.py`

```python
INTERRUPTED_FINISH_REASON = "interrupted"
INTERRUPTED_NOTICE = "本轮已被你中断。"
INTERRUPTED_TOOL_STUB = {...}                         # v2 原值
INVALID_TOOL_CALL_TEXT = "LLM 返回了无效的工具调用。"
ModelCall = Callable[[RunContext, ModelRequest], Awaitable[ModelResponse]]
ToolSchemaProvider = Callable[[RunContext], list[dict[str, Any]]]

class Transcript(Protocol):
    async def load(self, ctx: RunContext) -> list[dict[str, Any]]: ...
    async def append(self, ctx: RunContext, *, role: str, **fields: Any) -> Any: ...
        # fields 与 AgentRuntime._persist_message 的关键字参数一一对应：content、reasoning_content、tool_calls、
        # tool_call_id、tool_name、audit_id、prompt_tokens、completion_tokens、finish_reason、latency_ms（缺省都是 None）
    async def persisted_tool_call_ids(self, ctx: RunContext) -> set[str]: ...

class MemoryTranscript:
    """不落库的消息列表（OpenAI 形态），recon 用。"""
    def __init__(self, initial: Sequence[dict[str, Any]] = ()) -> None: ...
    # load 返回 list(self.messages)；append：tool → {"role","tool_call_id","content"}；
    # assistant → {"role","content": content or ""}，有 tool_calls 时再加 "tool_calls"；其余 → {"role","content"}；
    # 返回追加的那个 dict（没有 id）

def select_stop(outcomes: Sequence[ToolOutcome], pending: tuple[str, str | None] | None) -> tuple[str, str | None] | None: ...

class AgentLoop:
    def __init__(self, *, chain: MiddlewareChain, transcript: Transcript, call_model: ModelCall,
                 tool_schemas: ToolSchemaProvider, registry: ToolRegistry) -> None: ...
    async def run(self, ctx: RunContext) -> RunOutcome: ...
```

`select_stop`：先找 `stop_reason == "question_unanswered"` 的结果，有就取这一批里最后一个（v2 逐个覆盖 `unanswered` 变量）；再找第一个 `confirmation_resolved`；再找第一个其他 `stop_run`；都没有就返回 `pending`。`run` 的算法见 §4.5.1。

#### 4.2.11 门面接线（`runtime.py`）

`_run_turn` 与 `_run_turn_body` 增加关键字参数 `spec_name: str = "legacy_chat"`；`run_incident` 调 `_run_turn(..., persist_user=False, allowed_tools=allowed, spec_name="legacy_incident")`，其余不变。T8 之后的 `_run_turn_body`：

```python
async def _run_turn_body(self, session_id, content, request, emit, *, persist_user=True,
                         allowed_tools=None, spec_name="legacy_chat"):
    # 约 1192–1260 行原样保留、顺序不动：ensure_configured → 会话 model/base_url（含诊断会话修正）
    # → 落用户消息或找最后一条用户消息 → 能力探查与 tool_calling 检查 → emit("message_start")
    # → _status_summary → _knowledge_context → _make_client
    client, owned = self._make_client(client_settings)
    try:
        registry = self.confirmation_service.registry
        vision_enabled = bool(capabilities["vision"])
        bus = RunEventBus()
        async def forward(event: RunEvent) -> None:
            await emit(event.name, event.data)
        bus.subscribe(forward)
        run_id = new_uuid()                                   # maa_api.db.models.new_uuid；M65 不落库
        ctx = RunContext(run_id=run_id, root_run_id=run_id, parent_run_id=None, session_id=session_id,
                         ticket_id=None, spec=builtin_spec(spec_name), app=getattr(request, "app", None),
                         request=request, emit=bus.emit,
                         interrupted_now=lambda: self._interrupted_now(session_id),
                         allowed_tools=frozenset(allowed_tools) if allowed_tools is not None else None,
                         vision_enabled=vision_enabled)
        ctx.state[ContextLayoutMiddleware.name] = {"knowledge_context": knowledge_context,
                                                   "status_summary": summary,
                                                   "max_context_chars": self.max_context_chars}
        async def call_model(run: RunContext, req: ModelRequest) -> ModelResponse:
            return await collect_completion(client, model, req.messages, req.tools, emit=run.emit, draft=run.draft)
        def tool_schemas(run: RunContext) -> list[dict[str, Any]]:   # 每步现算：本轮里开启的扩展组下一步生效
            return self._tool_schemas(registry, vision_enabled=run.vision_enabled,
                                      allowed_tools=allowed_tools if allowed_tools is not None
                                      else self.session_allowed_tools(session_id))
        loop = AgentLoop(chain=self._legacy_chain(), transcript=_LegacyTranscript(self, vision_enabled=vision_enabled),
                         call_model=call_model, tool_schemas=tool_schemas, registry=registry)
        await loop.run(ctx)
    finally:
        if owned:
            await client.aclose()

def _legacy_chain(self) -> MiddlewareChain:
    return MiddlewareChain.for_kind("worker", [
        TraceMiddleware(),
        ContextLayoutMiddleware(),
        LoopGuardMiddleware(),
        ClarificationMiddleware(question_service=self.question_service, registry=self.confirmation_service.registry),
        PolicyMiddleware(confirmation_service=self.confirmation_service, session_factory=self.session_factory,
                         poll_seconds=self.confirmation_poll_seconds),
    ])

class _LegacyTranscript:
    """v2 会话的消息存取：读写都经过门面，测试对门面方法的替换照样生效。"""
    def __init__(self, runtime: AgentRuntime, *, vision_enabled: bool) -> None: ...
    async def load(self, ctx) -> list[dict[str, Any]]:
        # list_by_session(ctx.session_id)；视觉可用时 await runtime._hydrate_latest_screenshot(history)，
        # 否则 [_message_dict(m) for m in history]（build_model_context 第一步本来就这么转）
    async def append(self, ctx, **fields) -> AgentMessage:
        return await self._runtime._persist_message(ctx.session_id, **fields)
    async def persisted_tool_call_ids(self, ctx) -> set[str]: ...   # v2 _persisted_tool_call_ids 原样
```

顺序是 05 §5.2 的子集：trace（1）、context_layout（3）、loop_guard（13）、clarification（17）、policy（19）。

#### 4.2.12 `recon/runner.py`

公开名字与签名都不变：`ReconDeps`、`ReconBudget`、`ReconStep`、`budget_from_settings`、`recon_tool_schemas`、`dispatch_tool`、`run_recon(deps, *, question, hints=(), registry=None, step_runner=None)`、`MAX_CALLS_PER_STEP`、`RECON_TOOL_NAMES`、`SUMMARY_MAX_CHARS`、`DETAIL_MAX_CHARS`；`SYSTEM_PROMPT` 改为 `builtin_spec("recon").body`。新增两个只在 recon 用的中间件，`run_recon` 改写成：

```python
async def run_recon(deps, *, question, hints=(), registry=None, step_runner=None):
    explore.resolve_root(deps.settings)                       # 未配置工作区仍在调用模型之前报错
    tools = registry if registry is not None else _recon_registry()
    schemas = recon_tool_schemas(tools)
    budget = ReconBudget(max_steps=deps.max_steps, max_chars=deps.max_chars, output_chars=deps.output_chars)
    if deps.max_steps < 1:                                    # v2 的 for 循环一步都不走
        return _report(question=question, final_text="", budget=budget, stopped="max_steps")
    runner = step_runner or _default_step_runner
    steps: list[ReconStep] = []
    async def call_model(ctx, req):
        step = await runner(deps, req.messages, req.tools)
        steps.append(step)
        parts = [_call_parts(call) for call in step.calls]
        allowed = [p for p in parts if p[0] in RECON_TOOL_NAMES][:MAX_CALLS_PER_STEP]
        return ModelResponse(text=step.text or "", reasoning="", usage=Usage(step.usage.get("prompt_tokens"),
                             step.usage.get("completion_tokens")), upstream_finish_reason=None, latency_ms=0,
                             raw_tool_calls=[{"id": call_id, "type": "function", "function": {"name": name,
                                 "arguments": json.dumps(arguments, ensure_ascii=False)}}
                                 for name, call_id, arguments in allowed])
    spec = builtin_spec("recon").model_copy(update={"max_steps": deps.max_steps})   # 预算仍取 agent.recon_*
    run_id = new_uuid()
    ctx = RunContext(run_id=run_id, root_run_id=run_id, parent_run_id=None, session_id=None, ticket_id=None,
                     spec=spec, parallel_tools=False)
    loop = AgentLoop(chain=MiddlewareChain([TraceMiddleware(), ReconBudgetMiddleware(budget),
                                            ReconDispatchMiddleware(registry=tools, settings=deps.settings)]),
                     transcript=MemoryTranscript([{"role": "system", "content": spec.body},
                                                  {"role": "user", "content": _question_block(question, hints)}]),
                     call_model=call_model, tool_schemas=lambda _ctx: schemas, registry=tools)
    outcome = await loop.run(ctx)
    stopped = {"stop": "answered", "budget": "budget", "max_steps": "max_steps"}[outcome.finish_reason]
    return _report(question=question, final_text=steps[-1].text if steps else "", budget=budget, stopped=stopped)
class ReconBudgetMiddleware(Middleware):
    name = "recon_budget"
    applies_to = frozenset({"subagent"})
    def __init__(self, budget: ReconBudget) -> None: ...
    async def before_model(self, ctx, req): self.budget.steps += 1; return req          # v2 的 budget.steps = index + 1
    async def after_model(self, ctx, resp):
        if not self.budget.spend(resp.text or ""):
            raise StopRun("budget")                                                       # 文本先记账，超了就不派发
        return resp
    async def wrap_tool(self, ctx, call, call_next):
        if self.budget.exhausted:
            return ToolOutcome(status="skipped", content="")                              # 预算用尽后的同批调用不派发
        outcome = await call_next(call)
        outcome.content = (outcome.content or "")[: self.budget.output_chars]
        if not self.budget.spend(outcome.content):
            outcome.stop_run, outcome.stop_reason = True, "budget"
        return outcome
class ReconDispatchMiddleware(Middleware):
    name = "recon_dispatch"
    applies_to = frozenset({"subagent"})
    def __init__(self, *, registry: ToolRegistry, settings: Any) -> None: ...
    async def wrap_tool(self, ctx, call, call_next):
        return ToolOutcome(status="ok", content=await dispatch_tool(self.registry, self.settings, call.name, call.arguments or {}))
```

recon 链不挂 `ContextLayoutMiddleware`、`LoopGuardMiddleware`、`PolicyMiddleware`：v2 侦察不脱敏、不注入状态、不进审计，也没有打转检测（`test_agent_runtime_recon_uses_the_configured_budget_and_clamps_steps` 要求 20 步相同调用照常跑完）。这与 05 §5.2「适用：全部」不一致，按 §4.8 写回 05。`parallel_tools=False` 保证三件套按顺序派发，预算在中途用尽时后面的调用不会被派发（v2 是串行 for 循环）。预算用尽后补的 skipped 结果与最后一条 assistant 只进内存消息列表，报告里看不到。

`AgentRuntime.recon` 不改：仍按 `agent.recon_*` 算预算、用 `RECON_MAX_STEPS_CAP` 截步数、用 `RECON_TIMEOUT_SECONDS` 建客户端。

#### 4.2.13 自动诊断

`incidents.py` 不改。`AgentIncidentService._create_and_run` 调 `runtime.run_incident(session_id, request, INCIDENT_ALLOWED_TOOLS)`，门面以 `spec_name="legacy_incident"` 起一次 `AgentLoop` 运行：工具白名单是 `frozenset(allowed_tools) & INCIDENT_ALLOWED_TOOLS`（后者来自 legacy_incident 定义），同时作为 schema 过滤与执行期闸门；系统提示词是 legacy_incident 的正文（与 legacy_chat 相同）；事件总线没有订阅者；失败与中断时的诊断说明仍由 `run_incident` 写。

#### 4.2.14 `specs/schema.py`（完整代码）

```python
"""代理定义的数据形状：文件头字段，加上加载时补的正文与版本哈希。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

__all__ = [
    "AGENT_KINDS", "DEVICE_LEVELS", "MODEL_ROLES", "RISK_CEILINGS", "SPEC_NAME_PATTERN",
    "AgentKind", "AgentSpec", "AgentSpecBudget", "AgentSpecFrontmatter", "DeviceLevel",
    "ModelRoleName", "RiskCeiling",
]

SPEC_NAME_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"
AgentKind = Literal["orchestrator", "worker", "subagent", "background"]
AGENT_KINDS: tuple[str, ...] = ("orchestrator", "worker", "subagent", "background")
#: 与 ModelRole 枚举同值；枚举建好之前先用字面量。
ModelRoleName = Literal[
    "orchestrator", "worker", "subagent", "summarizer", "title",
    "trigger_eval", "memory", "vision", "embedding",
]
MODEL_ROLES: tuple[str, ...] = (
    "orchestrator", "worker", "subagent", "summarizer", "title",
    "trigger_eval", "memory", "vision", "embedding",
)
#: 由低到高。工具的 device 不能高于代理定义的 device。
DeviceLevel = Literal["none", "read", "exclusive"]
DEVICE_LEVELS: tuple[str, ...] = ("none", "read", "exclusive")
#: 与 RiskLevel 同值。
RiskCeiling = Literal["none", "consume", "destructive"]
RISK_CEILINGS: tuple[str, ...] = ("none", "consume", "destructive")


class AgentSpecBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_cost_per_run: float = Field(default=0, ge=0)


class AgentSpecFrontmatter(BaseModel):
    """内置定义文件的 YAML 头。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=SPEC_NAME_PATTERN)
    title: str = Field(min_length=1, max_length=8)
    kind: AgentKind
    description: str = Field(min_length=1, max_length=120)
    model_role: ModelRoleName
    tools: tuple[str, ...]
    deferred_groups: tuple[str, ...] = ()
    device: DeviceLevel
    risk_ceiling: RiskCeiling
    max_steps: StrictInt = Field(ge=0)
    delegates: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    output_schema: str | None = None
    budget: AgentSpecBudget = Field(default_factory=AgentSpecBudget)
    version: StrictInt = Field(ge=1)


class AgentSpec(AgentSpecFrontmatter):
    """加载完成的代理定义。"""

    body: str = Field(min_length=1)
    spec_version: str = Field(pattern=r"^[0-9a-f]{12}$")
    source_file: str
```

`StrictInt` 是为了拒绝 YAML 里的 `true`（Pydantic 宽松模式会把它当成 1）。

#### 4.2.15 `specs/registry.py`

```python
BUILTIN_PACKAGE = "maa_api.agent.specs"
BUILTIN_DIRNAME = "builtin"
#: 只有这两个代理可以按需开启扩展工具组。
DEFERRED_GROUP_SPECS = frozenset({"legacy_chat", "general"})

class AgentSpecError(RuntimeError):
    """内置代理定义不合法；属于代码错误，启动时直接失败。"""

def parse_spec_file(file_name: str, data: bytes) -> AgentSpec: ...
def load_builtin_specs(root: Traversable | None = None) -> dict[str, AgentSpec]: ...   # 按 name 排序
@lru_cache(maxsize=1)
def builtin_specs() -> Mapping[str, AgentSpec]: ...            # MappingProxyType(load_builtin_specs())
def builtin_spec(name: str) -> AgentSpec: ...                  # 没有就抛 AgentSpecError

class AgentSpecRegistry:
    def __init__(self, tool_registry: ToolRegistry, *, root: Traversable | None = None) -> None: ...
    def load(self) -> None: ...                                # 解析 + 全部规则 + 工具校验；失败抛 AgentSpecError
    def list(self) -> list[AgentSpec]: ...                     # 按 name 排序；未 load 抛 AgentSpecError
    def get(self, name: str) -> AgentSpec: ...                 # 没有就抛 AppError(AGENT_SPEC_NOT_FOUND)
```

文件格式与 `knowledge/__init__.py` 相同：`\A---\n<YAML>\n---\n<正文>`，正则照抄 `_FRONTMATTER`，YAML 用 `ruamel.yaml.YAML(typ="safe")`，读文件用 `importlib.resources`。正文是 `(match.group("body") or "").strip()`，版本哈希是 `hashlib.sha256(文件字节).hexdigest()[:12]`。报错信息形如「代理定义 recon.md 的 max_steps 不合法：subagent 必须大于 0」。规则（`parse_spec_file` 逐份检查前 13 条，`load_builtin_specs` 检查跨定义的 14–15，`AgentSpecRegistry.load` 再检查 16–17）：

| # | 规则 | 来源 |
|---|---|---|
| 1 | 文件是 UTF-8，有 frontmatter，YAML 解析成映射 | 05 §1.1 |
| 2 | 没有未知字段、不缺必填字段（`AgentSpecFrontmatter` 的 `extra="forbid"`）；`body`、`spec_version`、`source_file` 不能写在 frontmatter 里 | 05 §1.2 |
| 3 | `name` 匹配 `^[a-z][a-z0-9_]{1,31}$`，且等于文件名去掉 `.md` | 05 §1.2 |
| 4 | `title` 1–8 字，`description` 1–120 字 | 05 §1.2 |
| 5 | `kind`、`model_role`、`device`、`risk_ceiling` 在取值表里 | 05 §1.2 |
| 6 | `max_steps` 是整数且 ≥ 0；`kind=subagent` 时 > 0 | 05 §1.2 |
| 7 | `version` 是整数且 ≥ 1；`budget.max_cost_per_run` ≥ 0 | 05 §1.2 |
| 8 | `tools`、`delegates`、`deferred_groups`、`skills` 里没有重复 | 本 spec |
| 9 | `deferred_groups` 的每一项都在 `DEFERRED_TOOL_GROUPS` 里；非空时 `name` 必须在 `DEFERRED_GROUP_SPECS` 里 | 05 §1.2 |
| 10 | `delegates` 非空时 `kind` 必须是 `worker`（所以 subagent 不能委派） | 05 §1.2 |
| 11 | `kind` 为 `subagent` 或 `background` 时 `output_schema` 必填（M67 前只校验非空，不校验 schema 是否存在） | 05 §1.2 |
| 12 | `kind=orchestrator` 时 `device=none` | 05 §1.2 |
| 13 | 正文去掉首尾空白后非空 | 05 §1.1 |
| 14 | `kind=orchestrator` 的定义至多一个（M70 加入总控后由 M70 收紧为恰好一个） | 05 §1.2，见 §4.8 |
| 15 | `delegates` 里的名字都是已加载、`kind=subagent` 的定义；名字全局唯一；目录里至少一个 `.md` | 05 §1.2 |
| 16 | `tools` 里每个名字在 `ToolRegistry` 里存在 | 05 §1.2 |
| 17 | 每个工具的 `device` 不高于定义的 `device`（按 `DEVICE_LEVELS` 顺序比较） | 05 §1.2 |

装配（02 §5 第 2 项）：`main.py` 在 `confirmation_factory = getattr(...)` 前加 `tool_registry = build_registry()`，把原来内联的 `build_registry()` 换成 `tool_registry`；在 `app.state.agent_runtime = agent_runtime`（约 1209）之后加：

```python
from maa_api.agent.specs.registry import AgentSpecRegistry

agent_spec_registry = AgentSpecRegistry(tool_registry)
agent_spec_registry.load()   # 内置定义坏了属于代码错误，直接中止启动
app.state.agent_spec_registry = agent_spec_registry
```

门面与 recon 用 `builtin_spec(...)`（只解析、不查工具），所以 `AgentRuntime(None, None)` 这类构造不受影响；导入 `runtime.py` 时会解析一次全部内置定义，文件坏了导入就失败。

#### 4.2.16 `ToolDefinition` 扩展字段

```python
DeviceLevel = Literal["none", "read", "exclusive"]
DEVICE_LEVELS: tuple[str, ...] = ("none", "read", "exclusive")
# ToolDefinition 末尾追加（排在 model_schema 之后，保持既有关键字构造不受影响）：
    device: DeviceLevel = "none"          # exclusive 的工具要独占设备；高于代理定义 device 的工具不能进它的工具集
    refetchable: bool = False             # M74 赋值
    untrusted_output: bool = False        # M80 赋值
    rest_visible: bool = True             # False 时不出现在 REST 目录，REST 调用报 TOOL_NOT_FOUND
# __post_init__ 追加：device 不在 DEVICE_LEVELS 里抛 ValueError("unsupported device level: …")；
# 三个布尔字段不是 bool 抛 TypeError("<字段> must be a bool")
```

`registry.py` 里的 `DEVICE_LEVELS` 与 `specs/schema.py` 同值；`specs/schema.py` 不导入 `registry.py`，两处由 `test_device_levels_are_the_same_in_both_modules` 钉住。

v2 工具的 `device` 赋值（05 §6），其余工具保持默认 `none`：

| 文件 | 工具 | device |
|---|---|---|
| `maa_api/agent/tools/raw.py` `register_tools`（约 205–275，循环注册） | `click`、`swipe`、`long_press`、`input_text`、`key_event`、`back_to_home` | `exclusive`（循环里写 `device="read" if name == "trigger_screencap" else "exclusive"`） |
| 同上 | `trigger_screencap` | `read` |
| `maa_api/agent/tools/copilot_attempt.py`（约 95） | `run_copilot_attempt` | `exclusive` |
| `maa_api/agent/tools/ops.py`（约 147） | `update_game` | `exclusive` |
| `maa_api/agent/tools/status.py`（约 87） | `get_screenshot` | `read` |
| `maa_api/agent/tools/media.py`（约 162、177） | `send_screenshot`、`annotate_screenshot` | `read` |

REST 过滤（`maa_api/api/routers/agent.py`）：`list_tools` 的 `definitions = [d for d in service.registry.list() if d.rest_visible]`；`invoke_tool` 函数体第一行调新函数 `_ensure_rest_visible(service, name)`：`registry.get(name)` 取不到就直接返回（未知工具照旧由 `service.invoke` 报 `TOOL_NOT_FOUND`，幂等键路径不变），取到且 `rest_visible` 为假就抛 `AppError(TOOL_NOT_FOUND, f"未知工具：{name}")`。v2 工具全部 `rest_visible=True`，所以目录与 OpenAPI 都不变。

### 4.3 接口

#### 4.3.1 端点

新文件 `maa_api/api/routers/agent_specs.py`，`router = APIRouter(prefix="/api/agent/specs", tags=["agent"])`，在 `main.py` 的 `app.include_router(knowledge.router)` 之后 include（`from maa_api.api.routers import ... agent_specs ...` 按字母序加进导入列表）。两条路由都挂 `dependencies=[Depends(require_auth)]`；`app.state.agent_spec_registry` 不存在时抛 `AppError(SERVICE_UNAVAILABLE, "代理定义还在加载，请稍后重试")`。

| 方法与路径 | 函数名（operationId） | summary | 响应 | 错误 |
|---|---|---|---|---|
| GET `/api/agent/specs` | `list_specs`（`agent_list_specs`） | 读取代理定义列表 | 200 `SpecList` | `error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED")` |
| GET `/api/agent/specs/{name}` | `get_spec`（`agent_get_spec`） | 读取代理定义详情 | 200 `SpecDetail` | `error_responses("AGENT_SPEC_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED")` |

```python
class SpecSummary(BaseModel):
    name: str
    title: str
    kind: AgentKind
    description: str
    model_role: ModelRoleName
    tools: list[str]
    delegates: list[str]
    version: int            # frontmatter 的人工版本号
    spec_version: str       # 文件内容哈希前 12 位

class SpecList(BaseModel):
    items: list[SpecSummary]        # 按 name 排序

class SpecBudget(BaseModel):
    max_cost_per_run: float

class SpecDetail(SpecSummary):
    deferred_groups: list[str]
    device: DeviceLevel
    risk_ceiling: RiskCeiling
    max_steps: int
    skills: list[str]
    output_schema: str | None
    budget: SpecBudget
    body: str                       # 正文提示词
```

`{name}` 不加格式约束，任何查不到的名字都是 404 `AGENT_SPEC_NOT_FOUND`，message 为 `没有名为 {name} 的代理定义，可用的名字见代理定义列表`。`GET /api/agent/specs` 响应示例（legacy_chat 的 `tools` 有 84 项，这里省掉）：

```json
{"items": [
  {"name": "legacy_chat", "title": "旧版会话", "kind": "worker", "description": "旧版会话的代理：沿用原系统提示词与全部工具，扩展工具组按会话开启。只服务旧版会话，不用于工单。", "model_role": "worker", "tools": ["get_system_status"], "delegates": [], "version": 1, "spec_version": "0123456789ab"},
  {"name": "legacy_incident", "title": "旧版诊断", "kind": "worker", "description": "旧版自动诊断会话的代理：系统提示词同旧版会话，只开放 12 个安全查询与恢复工具。", "model_role": "worker", "tools": ["get_system_status", "get_device_status", "get_logs", "get_screenshot", "get_pipeline", "get_queue", "list_pipelines", "get_drop_stats", "read_knowledge", "stop_pipeline", "reconnect_device", "back_to_home"], "delegates": [], "version": 1, "spec_version": "0123456789ab"},
  {"name": "recon", "title": "侦察", "kind": "subagent", "description": "只读侦察：在工作区里搜代码、读文件、查本地 git，最后回一份带证据的报告。", "model_role": "subagent", "tools": ["list_files", "read_file", "search_code"], "delegates": [], "version": 1, "spec_version": "0123456789ab"}
]}
```

`GET /api/agent/specs/recon` 在上面那一项之外再带 `"deferred_groups": [], "device": "none", "risk_ceiling": "none", "max_steps": 8, "skills": [], "output_schema": "subagent_report", "budget": {"max_cost_per_run": 0.0}, "body": "你是 MAA-API 的只读侦察员。……"`。示例里的 `spec_version` 是占位，实际值由文件内容决定。

#### 4.3.2 错误码

| 码 | 状态码 | `ErrorCode` 上一行的注释 | 位置 |
|---|---|---|---|
| `AGENT_SPEC_NOT_FOUND` | 404 | `# 没有这个代理定义` | `ErrorCode` 里紧跟 `AGENT_TURN_INTERRUPTED`；`ERROR_HTTP_STATUS` 里紧跟 `ErrorCode.AGENT_TURN_INTERRUPTED: 409` |

#### 4.3.3 SSE 事件与 RunEventBus

v2 会话的 SSE 帧格式、事件名、字段、顺序全部不变；keep-alive 注释仍由 `stream_message` 按 `runtime.SSE_KEEPALIVE_SECONDS` 产生。拆分后各事件由谁发出：

| 事件 | data | 发出者（M65 后） |
|---|---|---|
| `message_start` | `session_id, message_id, seq, capability_status, degraded_reason` | 门面 `_run_turn_body`，直接调 v2 的 `emit` |
| `text_delta` | `text` | `collect_completion`；以及 `AgentLoop._finish_stop` 的收尾文本 |
| `tool_start` | `tool_call_id, name, arguments` | `execute_batch` |
| `confirmation_required` | `tool_call_id, name, confirmation_id, audit_id, expires_at` | `PolicyMiddleware` |
| `question_required` | `tool_call_id, question_id, question, options, multiple, expires_at` | `ClarificationMiddleware` |
| `tool_result` | `tool_call_id, name, message_id, audit_id` 加 `result` 或 `error`；批参数错误路径没有 `audit_id` 键 | `AgentLoop` |
| `interrupted` | `session_id, finish_reason="interrupted"` | `AgentLoop._finish_interrupted` |
| `message_end` | `session_id, finish_reason` | `AgentLoop`（正常回答、收尾、中断三处） |
| `session_title` | `session_id, title` | 门面 `_generate_session_title` |
| `error` | `code, message, details?` | 门面 `_produce_turn` 与 `stream_message` |

harness 里一律 `await ctx.emit(name, data)`，`ctx.emit` 是 `RunEventBus.emit`，订阅者把 `(name, data)` 原样交给 v2 的 `emit`，后者照旧 `queue.put(sse_event(name, data))`。M70 的 SSE v3、WS `agent_run`、Trace 都以新订阅者接在总线上，不改这里。

### 4.4 配置项

无。recon 的预算继续用 `agent.recon_max_steps`、`agent.recon_output_chars`、`agent.recon_max_chars`。

### 4.5 行为与算法

#### 4.5.1 `AgentLoop.run`

```
run(ctx):
    await chain.on_run_start(ctx)
    try:    outcome = await steps(ctx)
    except CancelledError:
        if not ctx.interrupted_now(): raise                     # 服务关闭等：原样抛，不写任何东西
        await _finish_interrupted(ctx); await _safe_run_end(ctx, RunOutcome("interrupted", "interrupted")); raise
    except Exception:
        await _safe_run_end(ctx, RunOutcome("failed", "error")); raise   # _safe_run_end 吞掉并记录自身异常
    await chain.on_run_end(ctx, outcome); return outcome

steps(ctx):
    loop:
        if ctx.cancel_event.is_set(): await _finish_interrupted(ctx); return RunOutcome("interrupted", "interrupted")
        ctx.draft.reset(); ctx.pending_stop = None
        req = ModelRequest(messages=await transcript.load(ctx), tools=tool_schemas(ctx))   # 每步重读历史
        try:
            req = await chain.before_model(ctx, req)
            resp = await call_model(ctx, req)
            if resp.raw_tool_calls:
                try: resp.tool_calls = [parse_tool_call(raw) for raw in resp.raw_tool_calls]
                except AppError:
                    append(assistant, content=resp.text or INVALID_TOOL_CALL_TEXT, reasoning_content=resp.reasoning or None,
                           prompt_tokens, completion_tokens, finish_reason=resp.upstream_finish_reason, latency_ms); raise
            resp = await chain.after_model(ctx, resp)
        except StopRun as stop:
            await _finish_stop(ctx, stop.reason, stop.text); return RunOutcome(stop.status, stop.reason, stop.text)
        累加 usage
        if not resp.tool_calls:
            append(assistant, content=resp.text, reasoning_content=resp.reasoning or None, 用量, finish_reason, latency_ms)
            reason = resp.upstream_finish_reason or "stop"
            await ctx.emit("message_end", {"session_id": ctx.session_id, "finish_reason": reason})
            return RunOutcome("succeeded", reason, resp.text)
        append(assistant, content=resp.text or None, reasoning_content=resp.reasoning or None,
               tool_calls=resp.raw_tool_calls, 用量, finish_reason, latency_ms)
        ctx.draft.pending_tool_calls = list(resp.tool_calls)
        try: outcomes = await execute_batch(ctx, resp.tool_calls, chain=chain, registry=registry)
        except AppError as exc:
            每个调用：append(tool, content=json_text({"error": app_error_wire(exc)}), tool_call_id, tool_name)
                     emit("tool_result", {"tool_call_id", "name", "message_id", "error"})      # 没有 audit_id 键
            ctx.draft.pending_tool_calls = []; raise
        for i, (call, o) in enumerate(zip(resp.tool_calls, outcomes, strict=True)):
            stored = append(tool, content=o.content if o.content is not None else json_text(o.body()),
                            tool_call_id=call.id, tool_name=call.name, audit_id=o.audit_id)
            o.message_id = getattr(stored, "id", None)
            ctx.draft.pending_tool_calls = list(resp.tool_calls[i + 1:])
            await ctx.emit("tool_result", {"tool_call_id": call.id, "name": call.name,
                                           "message_id": o.message_id, "audit_id": o.audit_id, **o.body()})
        stop = select_stop(outcomes, ctx.pending_stop)
        if stop: await _finish_stop(ctx, *stop); return RunOutcome("succeeded", stop[0], stop[1])
        ctx.steps += 1
        if ctx.spec.max_steps and ctx.steps >= ctx.spec.max_steps: return RunOutcome("succeeded", "max_steps")

_finish_stop(ctx, reason, text):
    if text is not None: append(assistant, content=text, finish_reason=reason); emit("text_delta", {"text": text})
    emit("message_end", {"session_id": ctx.session_id, "finish_reason": reason})

_finish_interrupted(ctx):                                        # v2 _finish_interrupted_turn 原样，每一步吞异常只记日志
    persisted = await transcript.persisted_tool_call_ids(ctx)
    pending 里不在 persisted 的调用各补一条 tool 消息：content=json_text(INTERRUPTED_TOOL_STUB)
    清空 pending；content = 部分文本.strip() + "\n\n" + INTERRUPTED_NOTICE（没有部分文本时只有 INTERRUPTED_NOTICE）
    append(assistant, content, reasoning_content=思考或 None, finish_reason="interrupted")
    emit("interrupted", {...}); emit("message_end", {...})       # 不重发已经流出去的文本
```

所有 `append` 都是 `await transcript.append(ctx, role=…, …)`，只传关键字。`legacy_chat`、`legacy_incident` 的 `max_steps` 是 0，不设上限（v2 §8.16）；构造参数 `max_iterations` 照旧存而不比。

#### 4.5.2 边界情况

| 输入 | 期望行为 |
|---|---|
| 一批里 `ask_user` 被跳过，后面还有一个写工具 | 后面的调用照常执行（v2 原样）；本轮以 `question_unanswered` 收尾，正文 `QUESTION_SKIPPED_TEXT` |
| 一批里两次 `ask_user` 都没答成 | 收尾正文取最后一次的原因 |
| 同一批既有没答成的提问又有被拒的确认 | `question_unanswered` 优先 |
| 串行批第 1 个确认被拒 | 第 2 个起各补 skipped（错误码同第 1 个，message「前一项操作未获确认，本项未执行。」），以 `confirmation_resolved` 收尾 |
| 第 3 次出现完全相同的一批调用（只换 id） | 这一批照常执行、落库，然后以 `stuck_loop` 收尾；整轮只请求模型 3 次 |
| 整批只有 `wait_pipeline` / `wait_update` | 不计数，也不清零 |
| 诊断会话里调用白名单外的工具（含 `ask_user`） | tool 消息 `TOOL_NOT_FOUND`「此自动诊断会话不允许调用该工具」；不进确认服务、不落审计 |
| 视觉不可用时调 `annotate_screenshot` / `click` | `LLM_REQUEST_FAILED`，文案分别是「当前模型不支持视觉输入；圈选截图已禁用」与「当前模型不支持视觉输入；截图和原子屏幕操作已禁用」 |
| 某个调用的参数 JSON 坏了 | 带 `tool_calls` 的 assistant 已落库；每个调用一条 error tool 消息与 `tool_result`；本轮抛 `LLM_REQUEST_FAILED`，门面发 `error` 事件 |
| 工具调用缺 id 或 name 不是字符串 | assistant 落成纯文本（无 `tool_calls`），正文是模型文本或「LLM 返回了无效的工具调用。」，然后抛错 |
| 中断落在一批中间（第 1 条 tool 消息已落库） | 已落库的不重复，余下补 `INTERRUPTED_TOOL_STUB`，再落一条 `interrupted` 助手消息；下一轮请求的历史结构完整 |
| 等确认期间中断 | 确认被撤回为 rejected，但本轮按中断收尾（`PolicyMiddleware` 每次轮询前后都查 `ctx.interrupted_now()`） |
| 不是用户中断的取消（服务关闭） | 原样抛出，不写任何消息 |
| recon 第一步文本就超字符预算 | 不派发工具，`stopped=budget`，`steps_used=1` |
| recon 中途某个工具输出用尽预算 | 这条 tool 消息照写，同批后面的调用不派发，`stopped=budget` |
| recon `max_steps` 小于 1 | 不调用模型，`stopped=max_steps`，`steps_used=0` |

#### 4.5.3 v2 已知怪行为（本里程碑不修）

1. 普通会话里，没开启的扩展组工具不进 schema，但模型直接点名调用时照样执行（执行期没有闸门，`allowed_tools=None`）。
2. `ask_user` 没答成时，同一批后面的调用仍会执行。
3. `max_iterations` 只存不比。
4. recon 的调用缺 id 时用工具名当 id，同一步可能出现重复 id。
5. legacy_chat 的工具集按注册表现算（`session_allowed_tools`），定义里的 `tools` 只用于校验与展示：测试用自建的小注册表跑会话，工具不在 84 个名字里也要能暴露。

### 4.6 前端

无界面改动。只重新生成 `web/openapi.json` 与 `web/src/types/api.d.ts`（多出 `/api/agent/specs`、`/api/agent/specs/{name}` 与 `SpecList`、`SpecSummary`、`SpecDetail`、`SpecBudget`）。

### 4.7 提示词与代理定义

三份文件由附录 A 的脚本生成：正文直接取 v2 常量，保证逐字一致。`legacy_chat` 与 `legacy_incident` 的正文都是 `runtime.SYSTEM_PROMPT`（3802 字，sha256 `664c3203…2536`），`recon` 的正文是 `recon/runner.py` 的 `SYSTEM_PROMPT`（585 字，sha256 `c321d448…9bda`）。两段原文都没有行尾空白、制表符和单独成行的 `---`，`strip()` 不会改动它们。生成后 `runtime.SYSTEM_PROMPT`、`runner.SYSTEM_PROMPT` 改为读定义正文，提示词类测试（`test_system_prompt_keeps_every_behaviour_and_safety_constraint` 等，以及 `test_tool_catalog.py` 里对 `SYSTEM_PROMPT` 的断言）不改一字照样通过。

`maa_api/agent/specs/builtin/legacy_chat.md` 的 frontmatter：

```yaml
---
name: legacy_chat
title: 旧版会话
kind: worker
description: 旧版会话的代理：沿用原系统提示词与全部工具，扩展工具组按会话开启。只服务旧版会话，不用于工单。
model_role: worker
tools: [get_system_status, get_screenshot, get_versions, get_logs, resolve_stage, get_drop_stats,
  get_stage_level_data, get_activity_stages, get_stage, search_prts, get_operator, get_enemy,
  get_item, get_drop_rates, get_prts_page, get_gacha_pools, get_story, get_operator_box,
  get_depot, plan_upgrade, start_upgrade_plan, list_upgrade_plans, cancel_upgrade_plan, get_skland_status,
  sync_skland_training, get_building_skills, get_infrast_candidates, list_infrast_plans, update_user_profile, search_copilots,
  save_copilot_draft, run_copilot_attempt, record_copilot_result, get_roguelike_info, send_screenshot, annotate_screenshot,
  send_item_icons, send_stage_map, send_chart, list_task_types, get_pipeline, wait_pipeline,
  list_pipelines, get_queue, submit_pipeline, stop_pipeline, set_task_params, cancel_queued,
  get_device_status, list_devices, reconnect_device, trigger_screencap, click, swipe,
  long_press, input_text, key_event, back_to_home, list_copilots, upload_copilot,
  set_infrast_plan, list_custom_tasks, register_custom_task, remove_custom_task, check_updates, update_core,
  update_resource, update_game, scan_proxy_channels, wait_update, restart_core, list_schedules,
  create_schedule, update_schedule, delete_schedule, read_knowledge, ask_user, list_files,
  read_file, search_code, delegate_recon, list_tool_groups, enable_tool_group, check_confirmation]
deferred_groups: [account, estate, ops, recon, schedule]
device: exclusive
risk_ceiling: destructive
max_steps: 0
delegates: []
skills: []
output_schema: null
budget: {max_cost_per_run: 0}
version: 1
---
```

`tools` 是 `build_registry()` 的注册顺序，共 84 个（含模型不可见的 `trigger_screencap`）。`legacy_incident.md`：

```yaml
---
name: legacy_incident
title: 旧版诊断
kind: worker
description: 旧版自动诊断会话的代理：系统提示词同旧版会话，只开放 12 个安全查询与恢复工具。
model_role: worker
tools: [get_system_status, get_device_status, get_logs, get_screenshot, get_pipeline, get_queue,
  list_pipelines, get_drop_stats, read_knowledge, stop_pipeline, reconnect_device, back_to_home]
deferred_groups: []
device: exclusive
risk_ceiling: none
max_steps: 0
delegates: []
skills: []
output_schema: null
budget: {max_cost_per_run: 0}
version: 1
---
```

12 个工具都是 SAFE，所以 `risk_ceiling: none`；含 `back_to_home`（exclusive）与 `get_screenshot`（read），所以 `device: exclusive`。`recon.md`：

```yaml
---
name: recon
title: 侦察
kind: subagent
description: 只读侦察：在工作区里搜代码、读文件、查本地 git，最后回一份带证据的报告。
model_role: subagent
tools: [list_files, read_file, search_code]
deferred_groups: []
device: none
risk_ceiling: none
max_steps: 8
delegates: []
skills: []
output_schema: subagent_report
budget: {max_cost_per_run: 0}
version: 1
---
```

`max_steps: 8` 是 `agent.recon_max_steps` 的默认值，只为满足「subagent 必须大于 0」；实际步数由 `run_recon` 用 `model_copy` 换成 `deps.max_steps`。`output_schema` 写 05 §3.3 的名字，M65 不按它约束输出，报告仍是 v2 的 `_report` 形状。

### 4.8 对契约的细化（T11 写回 05 并记 13 §8）

保持 v2 行为需要以下细化，主 agent 合并本 spec 时若尚未改 05，T11 照此修订 05 并在 13 §8 记一条「M65 契约细化」：

1. 05 §1.2「orchestrator 种类有且只有一个」：M65 没有总控定义，改为「至多一个；M70 起恰好一个」。
2. 05 §2「`AgentLoop.run(spec, session, run)`」：改为 `AgentLoop(chain, transcript, call_model, tool_schemas, registry).run(ctx)`，spec、会话、运行 id 都在 `RunContext` 里。
3. 05 §2.1：`RunContext` 加 `interrupted_now`、`allowed_tools`、`vision_enabled`、`parallel_tools`、`draft`、`pending_stop` 与 `stop_after_batch()`；`ModelResponse` 加 `raw_tool_calls`；`ToolCall.arguments` 在 `execute_batch` 解析前为 `None`（v2 先落 assistant 再解析参数）；`ToolOutcome` 加 `stop_text`、`content` 与 `body()`；`RunOutcome.status` 在 M66 前是字符串。
4. 05 §2.2「`if any(o.stop_run …): finish(first stop_reason)`」：改为 `select_stop` 的优先级（没答成的提问取最后一个 → 确认被拒或过期 → 其余按位置 → `stop_after_batch` 预约的结束）。
5. 05 §5.2：`LoopGuardMiddleware` 在 `after_model` 记指纹、本批执行完再结束；recon 在 M70 之前不挂 `ContextLayoutMiddleware`、`LoopGuardMiddleware`、`PolicyMiddleware`，改挂 `ReconBudgetMiddleware` 与 `ReconDispatchMiddleware`，工具不进审计（与 v2 侦察一致）。
6. 05 §3.1：legacy_chat 的运行期工具集按注册表现算，定义里的 `tools` 用于校验与展示；`recon` 的 `max_steps` 运行期取 `agent.recon_max_steps`。
7. 04 §2.1：`SpecSummary` 的 `version` 是 frontmatter 的整数版本，另加 `spec_version`（哈希）。
8. 02 §4.1：REST 路由清单加 `agent_specs.py（M65）`。
9. 12 §6「加 legacy_chat 的录制回放评测」：M65 用行为对齐 golden 测试承担，M68 再迁成评测用例。

## 5. 实施任务

通用约定：每个任务做完先跑「验证」，再按「提交」提交到本分支；pytest 命令不带 `-q`（见 §0 第 16 条）。每个任务的验证都包含 `tests/agent/` 全量与行为对齐测试，末行汇总里 passed 数不得少于上一个任务记在台账里的数，且没有 failed、error。

### T1 行为对齐基线（在改任何 `maa_api/` 代码之前）

- 写范围：`tests/agent/harness/test_harness_parity.py`（新）、`tests/agent/harness/golden/legacy_parity.json`（生成）
- 步骤：
  1. 按附录 B 写测试文件：16 个场景、`_Normalizer`、`_request`、`_Harness` 与两个用例。`tests/agent/harness/` 不放 `__init__.py`（与 `tests/agent/` 一致），所以测试文件名必须全仓唯一，本里程碑的新测试一律用 `test_harness_` 或 `test_agent_specs` 前缀。
  2. 录 golden：`MAA_REGENERATE_PARITY=1 .venv/bin/python -m pytest tests/agent/harness/test_harness_parity.py > /tmp/m65-t1-regen.log 2>&1; echo $?`
  3. 连跑两次比对：`for i in 1 2; do .venv/bin/python -m pytest tests/agent/harness/test_harness_parity.py > /tmp/m65-t1-$i.log 2>&1; echo $?; tail -n 1 /tmp/m65-t1-$i.log; done`
- 验证：第 2 步退出码 0；第 3 步两次都是 `0` 与 `17 passed`；`.venv/bin/python -c "import json; print(len(json.load(open('tests/agent/harness/golden/legacy_parity.json'))))"` 输出 `16`；`git status --porcelain` 只有这两个新文件。
- 提交：`git add tests/agent/harness && git commit -m "test(m65): v2 会话、诊断与侦察的行为对齐基线"`
- 完成判据：golden 已提交。此后任何任务都不许设置 `MAA_REGENERATE_PARITY`，也不许手改 golden。

### T2 工具定义扩展字段与 REST 过滤

- 写范围：`maa_api/agent/registry.py`、`maa_api/agent/tools/raw.py`、`status.py`、`media.py`、`copilot_attempt.py`、`ops.py`、`maa_api/api/routers/agent.py`、`tests/agent/test_tool_catalog.py`、`tests/api/test_agent_router.py`
- 步骤：按 §4.2.16 加字段与校验、赋 `device`、加 REST 过滤；写 §6 的 T2 用例（新用例追加在文件末尾，不动既有用例）。
- 验证：`.venv/bin/python -m pytest tests/agent/ tests/api/test_agent_router.py > /tmp/m65-t2.log 2>&1; echo $?; tail -n 1 /tmp/m65-t2.log` → `0`；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → `0`（OpenAPI 不变）
- 提交：`git commit -am "feat(m65): 工具定义增加 device 等扩展字段，REST 目录按 rest_visible 过滤"`

### T3 代理定义：schema、注册表与三份内置定义

- 写范围：`maa_api/agent/specs/`（新）、`maa_api/agent/runtime.py`（`SYSTEM_PROMPT`、`INCIDENT_ALLOWED_TOOLS` 两处）、`maa_api/agent/recon/runner.py`（`SYSTEM_PROMPT`）、`maa_api/main.py`（§4.2.15 的装配）、`tests/agent/specs/test_agent_specs.py`（新）
- 步骤：
  1. 先建空包：`mkdir -p maa_api/agent/specs/builtin`，写 `maa_api/agent/specs/__init__.py`（一行模块说明）。
  2. 在改 runtime 之前运行附录 A：把附录 A 存成 `/tmp/m65/write_builtin_specs.py`，`.venv/bin/python /tmp/m65/write_builtin_specs.py`，输出三行 `wrote …` 与 `OK`。
  3. 按 §4.2.14、§4.2.15 写 `schema.py`、`registry.py`。
  4. `runtime.py`：删掉 `SYSTEM_PROMPT` 字面量，改为 `SYSTEM_PROMPT = builtin_spec("legacy_chat").body`；`INCIDENT_ALLOWED_TOOLS = frozenset(builtin_spec("legacy_incident").tools)`。`runner.py`：`SYSTEM_PROMPT = builtin_spec("recon").body`。
  5. `main.py` 装配 `agent_spec_registry`。
  6. 写 §6 的 T3 用例。
- 验证：
  - §0 第 4、5 条的两条命令输出不变；
  - `.venv/bin/python -m pytest tests/agent/ tests/api/ > /tmp/m65-t3.log 2>&1; echo $?; tail -n 1 /tmp/m65-t3.log` → `0`；
  - `.venv/bin/python scripts/agent_smoke.py > /tmp/m65-t3-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m65-t3-smoke.log` → `0` 与 `SMOKE OK`（lifespan 能加载定义）。
- 提交：`git add maa_api/agent/specs maa_api/agent/runtime.py maa_api/agent/recon/runner.py maa_api/main.py tests/agent/specs && git commit -m "feat(m65): 代理定义注册表与三份内置定义"`

### T4 harness 类型、中间件接口与事件总线

- 写范围：`maa_api/agent/harness/__init__.py`、`context.py`、`middleware.py`、`events.py`（新）；`maa_api/agent/runtime.py`（只删 `sse_event` 定义，改为从 `events` 导入并再导出）；`tests/agent/harness/test_harness_middleware.py`、`test_harness_events.py`（新）
- 步骤：1. 按 §4.2.4–§4.2.6 建三个模块（`context.py` 直接导入 T3 建好的 `AgentSpec`）。2. 把 `sse_event` 原样移到 `events.py`。3. 写 §6 的 T4 用例。
- 验证：`.venv/bin/python -m pytest tests/agent/ > /tmp/m65-t4.log 2>&1; echo $?; tail -n 1 /tmp/m65-t4.log` → `0`
- 提交：`git add maa_api/agent/harness maa_api/agent/runtime.py tests/agent/harness && git commit -m "refactor(m65): harness 核心类型、中间件接口与事件总线"`

### T5 流式解析搬到 chunks.py

- 写范围：`maa_api/agent/harness/chunks.py`（新）、`maa_api/agent/runtime.py`、`tests/agent/harness/test_harness_chunks.py`（新）
- 步骤：1. 搬 `merge_tool_call_deltas`、`_chunk_tool_calls`（改名 `chunk_tool_calls`）。2. 把 `_complete` 改成 `collect_completion`（§4.2.7），删掉方法。3. 删 `_TurnDraft`，旧循环改用 `TurnDraft`。4. 旧循环改为 `resp = await collect_completion(client, model, messages, tools, emit=emit, draft=draft)`，后面用 `resp.text`、`resp.reasoning`、`resp.usage.prompt_tokens`、`resp.usage.completion_tokens`、`resp.upstream_finish_reason`、`resp.latency_ms`、`resp.raw_tool_calls` 替换原来的六元组与 `merge_tool_call_deltas(call_deltas)`；其余逻辑不动。5. `_probe_capabilities` 改用 `chunk_tool_calls`。6. runtime 再导出 `merge_tool_call_deltas`。
- 验证：`.venv/bin/python -m pytest tests/agent/ > /tmp/m65-t5.log 2>&1; echo $?; tail -n 1 /tmp/m65-t5.log` → `0`
- 提交：`git add -A maa_api/agent tests/agent/harness && git commit -m "refactor(m65): 流式 chunk 解析搬到 harness/chunks"`

### T6 上下文组装搬到 builtin_middleware.py

- 写范围：`maa_api/agent/harness/builtin_middleware.py`（新）、`maa_api/agent/runtime.py`、`tests/agent/harness/test_harness_context_layout.py`（新）
- 步骤：1. 把 §4.2.2 中「T6」各行原样搬入，`build_model_context` 的默认 `system_prompt` 改为 `_DEFAULT_SYSTEM_PROMPT`。2. 在同一文件定义 `TraceMiddleware`、`ContextLayoutMiddleware`（还不接线）。3. runtime 改为导入并再导出；`_hydrate_latest_screenshot` 从新模块导入 `_message_dict`、`_screenshot_payload`。旧循环继续直接调 `build_model_context`。
- 验证：同 T5 的命令（日志名换成 t6）→ `0`
- 提交：`git add -A maa_api/agent tests/agent/harness && git commit -m "refactor(m65): 上下文组装搬到 harness/builtin_middleware"`

### T7 工具批执行与提问、确认、卡死三个中间件

- 写范围：`maa_api/agent/harness/toolexec.py`（新）、`builtin_middleware.py`、`chunks.py`、`maa_api/agent/runtime.py`、`tests/agent/harness/test_harness_toolexec.py`（新）、`test_harness_chunks.py`
- 步骤：
  1. `chunks.py` 加 `parse_tool_call`（返回 `ToolCall`）、`parse_arguments`；`toolexec.py` 按 §4.2.8 写，搬入 `SCREEN_TOOL_NAMES` 等常量与 `json_text`、`app_error_wire`、`vision_disabled_message`。
  2. `builtin_middleware.py` 加 `ClarificationMiddleware`、`PolicyMiddleware`、`LoopGuardMiddleware`（还不接线）、`calls_signature`、`exempt_from_stuck_loop`、`request_id_of`、`archive_screenshot_result`、`STUCK_*`、`QUESTION_*` 常量（§4.2.9）。
  3. 旧循环过渡改造：在 `_run_turn_body` 建好客户端后建 `RunContext`（字段同 §4.2.11，`emit` 先直接用 v2 的 `emit`）与链 `MiddlewareChain.for_kind("worker", [ClarificationMiddleware(...), PolicyMiddleware(...)])`；解析改用 `parse_tool_call`，`draft.pending_tool_calls` 存 `ToolCall`；`_execute_tool_batch(...)` 换成 `execute_batch(ctx, calls, chain=chain, registry=registry)`；批后逐个用 `outcome.body()`、`outcome.audit_id` 落库与发事件；收尾判断改成「最后一个 `stop_reason == "question_unanswered"` 的结果 → 第一个 `confirmation_resolved` → 内联的打转检测（改用 `calls_signature`、`exempt_from_stuck_loop`，参数用 `call.raw_arguments`）」，正文用结果的 `stop_text`；`_finish_interrupted_turn` 改用 `call.id`、`call.name`。
  4. 删 `_execute_tool_batch`、`_ask_user`、`_archive_screenshot_result` 与已搬走的模块函数；runtime 再导出 `QUESTION_EXPIRED_TEXT`、`QUESTION_SKIPPED_TEXT`、`STUCK_REPEAT_LIMIT`、`STUCK_EXEMPT_TOOLS`、`SCREEN_TOOL_NAMES`；`_tool_schemas` 用导入的 `SCREEN_TOOL_NAMES`。
  5. 写 §6 的 T7 用例。
- 验证：同 T5 的命令（t7）→ `0`；另跑 `.venv/bin/python -m pytest tests/agent/test_runtime.py tests/agent/test_incidents.py tests/agent/test_account_resources_flow.py > /tmp/m65-t7b.log 2>&1; echo $?` → `0`
- 提交：`git add -A maa_api/agent tests/agent/harness && git commit -m "refactor(m65): 工具批执行与提问、确认中间件"`

### T8 AgentLoop 接管 v2 会话与自动诊断

- 写范围：`maa_api/agent/harness/loop.py`（新）、`maa_api/agent/runtime.py`、`tests/agent/harness/test_harness_loop.py`、`test_harness_runtime_facade.py`（新）
- 步骤：
  1. 按 §4.2.10、§4.5.1 写 `loop.py`，`INTERRUPTED_*` 三个常量搬入。
  2. runtime 加 `_LegacyTranscript`、`_legacy_chain()`，`_run_turn_body` 改成 §4.2.11 的形状；`_run_turn`、`run_incident` 加 `spec_name`。
  3. 删 `_finish_interrupted_turn`、`_persisted_tool_call_ids`、`_finish_with_text` 与旧循环的全部内联逻辑；再导出 `INTERRUPTED_FINISH_REASON`、`INTERRUPTED_NOTICE`、`INTERRUPTED_TOOL_STUB`。
  4. 更新 runtime 模块说明：v2 会话的门面，会话锁、中断、能力探查、标题与失败说明在这里，一轮里的循环交给 `harness.loop.AgentLoop`；事件列表不变。
  5. 写 §6 的 T8 用例。
- 验证：同 T5 的命令（t8）→ `0`；`wc -l < maa_api/agent/runtime.py` → 小于 1900；`rg -c "_complete\(|_execute_tool_batch|_ask_user\(|_finish_interrupted_turn|^def build_model_context|^class _TurnDraft" maa_api/agent/runtime.py || echo 0` → `0`
- 提交：`git add -A maa_api/agent tests/agent/harness && git commit -m "refactor(m65): AgentLoop 接管 v2 会话与自动诊断"`

### T9 recon 跑在 AgentLoop 上

- 写范围：`maa_api/agent/recon/runner.py`、`tests/agent/harness/test_harness_recon.py`（新）
- 步骤：按 §4.2.12 改写 `run_recon`，加 `ReconBudgetMiddleware`、`ReconDispatchMiddleware`；删掉不再使用的私有函数前先 `rg` 确认测试没有导入它们（`_default_step_runner`、`_merge_tool_calls`、`_chunk_tool_calls`、`_call_parts`、`_question_block`、`_report`、`_parse_report`、`_recon_registry` 都还在用，保留）。
- 验证：同 T5 的命令（t9）→ `0`；`.venv/bin/python -m pytest tests/agent/test_recon_delegate.py tests/agent/test_recon_explore.py > /tmp/m65-t9b.log 2>&1; echo $?` → `0`
- 提交：`git add -A maa_api/agent/recon tests/agent/harness && git commit -m "refactor(m65): 侦察子循环跑在 AgentLoop 上"`

### T10 代理定义端点、错误码与接口清单

- 写范围：`maa_api/api/routers/agent_specs.py`（新）、`maa_api/main.py`、`maa_api/domain/errors.py`、`tests/domain/test_errors.py`、`tests/api/test_agent_specs_router.py`（新）、`docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`（脚本生成）、`web/openapi.json`、`web/src/types/api.d.ts`
- 步骤：
  1. 按 §4.3.1 写路由文件并 include；按 §4.3.2 加错误码。
  2. `tests/domain/test_errors.py`：`ALL_CODE_COUNT = 125`、`HTTP_BOUND_CODE_COUNT = 124`（注释里补一句 M65 新增 `AGENT_SPEC_NOT_FOUND`）。
  3. `docs/v3/14`：§4 正文「共 124 条」改「共 125 条」，`### 4.11 Agent 与 LLM` 表在 `AGENT_TURN_INTERRUPTED` 行后加 ``| `AGENT_SPEC_NOT_FOUND` | 404 | 没有这个代理定义（`GET /api/agent/specs/{name}` 的名字不存在） |``；§6 开头「共 121 条 HTTP 端点」改「共 123 条」；`### 6.15 agent` 表末尾加两行（下方），「（18 条）」改「（20 条）」；同表 `GET /api/agent/tools` 行的用途列末尾补「；只列对 REST 开放的工具」。
  4. 重新生成：`.venv/bin/python scripts/dump_openapi.py && .venv/bin/python scripts/generate_api_guide.py && cd web && pnpm gen:api && cd ..`
  5. 写 §6 的 T10 用例。

  ```markdown
  | GET | `/api/agent/specs` | 内置代理定义列表：名字、种类、工具、版本 | | 200 | — |
  | GET | `/api/agent/specs/{name}` | 一个代理定义的完整内容，含正文提示词 | | 200 | `AGENT_SPEC_NOT_FOUND` |
  ```
- 验证：
  - `.venv/bin/python -m pytest tests/domain/ tests/api/ tests/test_api_guide_generator.py > /tmp/m65-t10.log 2>&1; echo $?; tail -n 1 /tmp/m65-t10.log` → `0`
  - `.venv/bin/python scripts/dump_openapi.py --check; echo $?` → `0`；`.venv/bin/python scripts/generate_api_guide.py --check; echo $?` → `0`；`cd web && pnpm gen:api:check; echo $?; cd ..` → `0`
- 提交：`git add maa_api tests docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md web/openapi.json web/src/types/api.d.ts && git commit -m "feat(m65): 代理定义列表与详情端点"`

### T11 冒烟、全量验证与文档

- 写范围：`scripts/agent_smoke.py`、`docs/v3/02-系统架构.md`（§4.1 一处）、`docs/v3/05-运行时与代理定义.md`（§4.8 未合并时）、`docs/v3/13-决策记录.md`（§8）、`docs/v3/ledgers/m65-agentspec-harness.md`（新）
- 步骤：
  1. `scripts/agent_smoke.py`：顶部从 runtime 导入 `SYSTEM_PROMPT`；在打印「REST 工具鉴权…」那行 `[ok]` 之后加一步：`GET /api/agent/specs` 的名字列表是 `["legacy_chat", "legacy_incident", "recon"]`；`GET /api/agent/specs/legacy_chat` 的 `body == SYSTEM_PROMPT`；`GET /api/agent/specs/nope` 是 404 且 `error.code == "AGENT_SPEC_NOT_FOUND"`；用现有的 `_check_response`、`_require`，最后 `print("[ok] 代理定义列表、legacy_chat 正文与未知名字的 404")`。
  2. 跑 §7 的全部命令。
  3. 文档按 §9 同步；写台账（含 §0 第 14 条与各任务的 passed 数、Rulings I made）。
- 验证：§7 全部通过。
- 提交：`git add scripts/agent_smoke.py docs/v3 && git commit -m "docs(m65): 冒烟、契约细化与台账"`

### T12 合并与 tag

- 步骤：
  ```bash
  git status --porcelain            # 应为空
  git checkout dev && git merge --no-ff agentspec-harness -m "merge: 代理定义与通用循环（M65，见 docs/v3/13 §8.<N>）"
  git tag v3-m65
  git branch -d agentspec-harness
  ```
  `§8.<N>` 换成 T11 记的那一条的编号；没有偏离时写「无偏离」。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/harness/test_harness_parity.py`（T1） | `test_legacy_behaviour_matches_the_recorded_golden[<场景>]`（16 个） | 规范化后的 SSE 帧、模型请求、落库消息、会话计数、审计、返回值与 golden 完全相等 |
| 同上 | `test_golden_covers_exactly_the_scenarios` | golden 的键集合等于场景集合 |
| `test_harness_middleware.py`（T4） | `test_chain_runs_before_model_in_order_and_after_model_in_reverse`、`test_on_run_end_runs_in_reverse` | 记录调用顺序 |
| 同上 | `test_wrap_tool_is_an_onion_with_the_first_middleware_outermost`、`test_wrap_tool_without_an_executor_raises` | A 前、B 前、执行器、B 后、A 后；链里没有执行器时 `RuntimeError` |
| 同上 | `test_for_kind_keeps_only_middlewares_that_apply` | `ClarificationMiddleware` 不进 subagent 链 |
| `test_harness_events.py`（T4） | `test_bus_forwards_events_to_every_sink_in_order` | 两个 sink 顺序收到同一个 `RunEvent` |
| 同上 | `test_sse_event_frame_is_byte_identical_to_v2` | `sse_event("tool_result", {"api_key": "x", "n": 1, "text": "中文"})` 等于 `'event: tool_result\ndata: {"api_key":"[REDACTED]","n":1,"text":"中文"}\n\n'` |
| 同上 | `test_run_context_defaults_match_the_contract` | caller INTERNAL、steps 0、vision 与 parallel 为真、`interrupted_now()` 为假、`stop_after_batch` 写入 `pending_stop` |
| `tests/agent/test_tool_catalog.py`（T2） | `test_device_levels_follow_the_contract`、`test_extension_fields_keep_v2_defaults`、`test_tool_definition_rejects_an_unknown_device_level` | exclusive 恰为 8 个、read 恰为 4 个，其余 none；全部 `rest_visible=True`，`refetchable`、`untrusted_output` 全为假；`device="write"` 抛 `ValueError` |
| `tests/api/test_agent_router.py`（T2） | `test_rest_hidden_tools_are_absent_from_the_catalog_and_not_invocable` | 照抄该文件第一个用例的装配，注册一个 `rest_visible=False` 的工具：目录里没有；invoke 返回 404 `TOOL_NOT_FOUND`，handler 没被调用 |
| `tests/agent/specs/test_agent_specs.py`（T3） | `test_builtin_specs_load_three_definitions_sorted_by_name`、`test_spec_version_is_the_first_12_hex_of_the_file_sha256` | 名字依次是 legacy_chat、legacy_incident、recon；哈希用文件字节现算 |
| 同上 | `test_legacy_chat_body_is_byte_identical_to_the_v2_system_prompt`、`test_recon_body_is_byte_identical_to_the_v2_recon_prompt` | sha256 分别为 `664c3203…2536`、`c321d448…9bda`；`runtime.SYSTEM_PROMPT`、`runner.SYSTEM_PROMPT` 就是正文对象 |
| 同上 | `test_legacy_incident_shares_the_prompt_and_lists_the_twelve_recovery_tools`、`test_legacy_chat_lists_every_registered_tool_in_registration_order` | 正文与 legacy_chat 相同，`INCIDENT_ALLOWED_TOOLS` 等于 12 个名字的字面量集合；legacy_chat 的 `tools` 等于 `[d.name for d in build_registry().list()]`，`deferred_groups` 等于 `sorted(DEFERRED_TOOL_GROUPS)` |
| 同上 | `test_registry_load_validates_tools_against_the_tool_registry`、`test_registry_get_unknown_name_raises_agent_spec_not_found` | 真注册表 load 通过，少一个工具的注册表抛 `AgentSpecError`；`get("nope")` 抛 `AppError(AGENT_SPEC_NOT_FOUND)` |
| 同上 | `test_invalid_definition_fails_loudly`（参数化）、`test_device_levels_are_the_same_in_both_modules` | 在 `tmp_path` 里写坏文件，逐条覆盖 §4.2.15 的规则 1–17，各抛 `AgentSpecError`；两处 `DEVICE_LEVELS` 相等 |
| `test_harness_chunks.py`（T5、T7） | `test_collect_completion_streams_text_and_fills_the_draft`、`test_collect_completion_keeps_only_non_negative_usage`、`test_collect_completion_rejects_a_non_mapping_chunk` | 每段文本一条 `text_delta`，`draft` 与返回值一致；负数与非整数用量被忽略；非 Mapping 块报 `LLM_REQUEST_FAILED` |
| 同上 | `test_parse_tool_call_requires_a_name_and_an_id`、`test_parse_arguments_rejects_invalid_json_and_non_objects` | 四种报错文案与 v2 相同 |
| `test_harness_context_layout.py`（T6） | `test_context_layout_middleware_equals_build_model_context`、`test_context_layout_uses_the_spec_body_as_system_prompt` | 同一输入两者输出相等；第一条 system 以定义正文开头 |
| `test_harness_toolexec.py`（T7） | `test_read_only_batches_run_concurrently_and_others_serially`、`test_parallel_tools_false_forces_serial_execution` | 并发峰值 2 与 1；`parallel_tools=False` 时只读批峰值 1 |
| 同上 | `test_tool_start_is_emitted_before_the_gates`、`test_gates_order_hidden_then_allowed_then_vision` | 被拦的调用也先有 `tool_start`；同时命中多道闸门时报第一道的文案 |
| 同上 | `test_rejected_confirmation_skips_the_rest_with_placeholders`、`test_confirmation_terminal_outcomes_request_a_stop` | skipped 的码与文案；`stop_reason="confirmation_resolved"` 与 `CONFIRMATION_TERMINAL_TEXT` |
| 同上 | `test_unanswered_question_does_not_skip_later_calls`、`test_bad_arguments_raise_before_any_dispatch` | 钉住 §4.5.3 第 2 条；坏参数抛 `AppError` 且执行器没被调用 |
| `test_harness_loop.py`（T8，用 `MemoryTranscript` 与假模型） | `test_text_only_run_persists_the_answer_and_emits_message_end`、`test_assistant_is_persisted_before_dispatch_and_tool_messages_follow_call_order` | 落库顺序与 `tool_result` 字段 |
| 同上 | `test_stop_precedence`（参数化）、`test_loop_guard_stops_after_the_third_identical_batch_is_executed` | 最后一个没答成的提问 → 确认 → 其余 → `pending_stop`；相同批执行 3 次、请求 3 次 |
| 同上 | `test_stop_run_from_after_model_finishes_without_persisting`、`test_max_steps_finishes_after_the_batch`、`test_cancel_event_finishes_as_interrupted` | transcript 不变且 outcome 带原因；finish_reason `max_steps`；返回 interrupted |
| 同上 | `test_interrupt_writes_stubs_then_the_interrupted_message`、`test_non_interrupt_cancellation_propagates_without_writes` | `interrupted_now` 为真时补桩、落中断消息、发两个事件后再抛；为假时 transcript 不变 |
| 同上 | `test_invalid_tool_call_persists_plain_assistant_then_raises`、`test_bad_arguments_write_an_error_reply_for_every_call_then_raise`、`test_on_run_end_runs_on_success_and_failure` | 无 `tool_calls`；错误路径的 `tool_result` 没有 `audit_id` 键；两条路径都调 `on_run_end` |
| `test_harness_runtime_facade.py`（T8） | `test_runtime_reexports_moved_names_from_their_new_home`、`test_harness_never_imports_the_runtime_facade` | §4.2.2「再导出」列的每个名字与新位置是同一个对象；`maa_api/agent/harness/*.py` 文本里没有 `maa_api.agent.runtime` |
| 同上 | `test_legacy_chain_order_matches_the_contract`、`test_chat_and_incident_turns_run_with_their_definitions` | `_legacy_chain().names == ("trace", "context_layout", "loop_guard", "clarification", "policy")`；monkeypatch 包一层 `AgentLoop.run` 记下 `ctx.spec.name`：`stream_message` 是 legacy_chat，`run_incident` 是 legacy_incident |
| `test_harness_recon.py`（T9） | `test_recon_runs_on_agent_loop_with_the_recon_definition`、`test_recon_chain_skips_context_layout_loop_guard_and_policy` | spec 名 recon，`max_steps` 等于 deps；链名为 trace、recon_budget、recon_dispatch |
| 同上 | `test_recon_output_budget_stops_the_rest_of_the_batch`、`test_recon_with_zero_max_steps_never_calls_the_model`、`test_recon_dispatches_tools_one_by_one` | 第一个输出用尽预算后第二个不派发，`stopped=budget`；`steps_used=0`、`stopped=max_steps`；并发峰值 1 |
| `tests/api/test_agent_specs_router.py`（T10，照 `test_agent_knowledge_router.py` 建 `FastAPI()` 与 `tmp_settings`） | `test_list_specs_returns_the_three_builtin_definitions`、`test_get_spec_returns_the_prompt_body`、`test_get_unknown_spec_is_404` | 字段集合分别等于 `SpecSummary` 的 9 个、`SpecDetail` 的 17 个字段；legacy_chat 的 `body == SYSTEM_PROMPT`；未知名字 404 `AGENT_SPEC_NOT_FOUND` |
| 同上 | `test_specs_are_unavailable_without_a_registry`、`test_specs_require_the_token_when_one_is_configured` | 503 `SERVICE_UNAVAILABLE`；`tmp_settings` 参数化成有 token，不带头时 401 |
| 同上 | `test_spec_routes_publish_pydantic_models_in_openapi` | `create_app().openapi()` 里 operationId 为 `agent_list_specs`、`agent_get_spec`，200 的 schema 引用 `SpecList`、`SpecDetail` |
| `tests/domain/test_errors.py`（T10） | 既有用例 | 计数常量 125 / 124 |

## 7. 验收

照 12 §4，全部亲自重跑：

```bash
.venv/bin/python -m pytest > /tmp/m65-pytest.log 2>&1; echo $?; tail -n 1 /tmp/m65-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
.venv/bin/python scripts/generate_api_guide.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m65-pnpm.log 2>&1 && pnpm gen:api:check > /tmp/m65-genapi.log 2>&1 && pnpm typecheck > /tmp/m65-tc.log 2>&1 && pnpm test --run > /tmp/m65-vitest.log 2>&1 && pnpm build > /tmp/m65-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m65-fsmoke.log 2>&1; echo $?; tail -n 1 /tmp/m65-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m65-asmoke.log 2>&1; echo $?; tail -n 1 /tmp/m65-asmoke.log
.venv/bin/python scripts/check_doc_links.py 2>&1 | tail -n 1
git diff --check dev...HEAD; echo $?
```

期望：pytest 退出码 0，与本分支起点相比不新增失败；三个 `--check` 与前端四步退出码 0；两个冒烟末行 `SMOKE OK`；断链 `BROKEN 0`；`git diff --check` 退出码 0。

本里程碑特有：

1. `git log --format=%h -- tests/agent/harness/golden/legacy_parity.json | wc -l` → `1`（golden 只在 T1 提交过）。
2. `wc -l < maa_api/agent/runtime.py` → 小于 1900；`rg -c '^SYSTEM_PROMPT = """' maa_api || echo 0` → `0`。
3. §0 第 4、5 条命令输出不变。
4. 既有测试一行没删没改：`git diff dev...HEAD -- tests/agent/test_*.py tests/api/test_agent_router.py | rg "^-[^-]" | wc -l` → `0`（只允许 `test_tool_catalog.py`、`test_agent_router.py` 末尾的新增用例）。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 搬家引入细微行为差异（事件顺序、落库字段、收尾文案） | T1 的 golden 在搬家前录制，每个任务都比对；比对失败只能改代码，不能重录 |
| 测试对门面内部的依赖被拆断（实例属性替换、模块全局 monkeypatch、记录器名） | §4.2.3 的表逐项对照；T8 的门面用例钉住再导出 |
| 循环导入 | 依赖方向写死在 §4.2.1，并有文本检查用例 |
| 打包漏掉 `specs/builtin/*.md` | 与 `knowledge/builtin` 同一机制；M64 若改了打包方式，按 `knowledge/builtin` 的处理方式把 `specs/builtin` 也加进去 |
| 执行者顺手修 v2 怪行为 | §4.5.3 列明不修，§10 要求停下来问 |
| recon 预算语义漂移 | `test_recon_delegate.py` 的 25 个用例加 T9 的新用例 |

回滚：没有迁移。单个任务出问题 `git revert <该任务提交>`；合并后整体回滚 `git revert -m 1 <merge>`。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：§4.11 加一行、错误码总数 124 → 125；§6.15 加两行、（18 条）→（20 条）、HTTP 端点总数 121 → 123；`GET /api/agent/tools` 行补「只列对 REST 开放的工具」（T10）。
- `docs/v3/15-开放API接入指南.md`：用 `scripts/generate_api_guide.py` 重新生成（T10）。
- `docs/v3/05-运行时与代理定义.md`：§4.8 的 1–6 条若主 agent 尚未合并，T11 修订 §1.2、§2、§2.1、§2.2、§3.1、§5.2。
- `docs/v3/04-接口与事件协议.md` §2.1：`SpecSummary` 字段补 `spec_version`（同上，未合并时）。
- `docs/v3/02-系统架构.md` §4.1：路由清单补 `agent_specs.py（M65）`（同上）。
- `docs/v3/13-决策记录.md` §8：记「M65 契约细化」一条，列 §4.8 的内容与理由；实施中若还有别的偏离，另记。
- 台账 `docs/v3/ledgers/m65-agentspec-harness.md`：每个任务的验证命令与结果、各任务末的 passed 数、golden 的 sha256、runtime 行数前后对比、Rulings I made。
- 计数：工具 84 个（模型可见 83）不变；HTTP 端点 121 → 123；错误码 124 → 125（有 HTTP 状态的 123 → 124）；表与 tag 数不变。`scripts/copy_inventory.py` 不扫描 `specs/builtin`，提示词从文案清单里消失属预期，不作为门禁。

## 10. 停下来问

- §0 任何一条对不上，尤其是两段提示词的哈希、错误码与端点计数。
- 任一任务后行为对齐测试失败，且同一提交连跑两次结果一致（结果时好时坏才算测试本身的不确定性，同样要报告）。不许重录 golden。
- 必须删改既有测试的断言才能通过（T2、T10 写明的新增用例与计数常量除外）。
- 发现 v2 行为疑似有缺陷（包括 §4.5.3 以外的新发现）：不修，记台账并问。
- 需要新增依赖、表、配置键、工具，或 `AGENT_SPEC_NOT_FOUND` 以外的错误码。
- 需要改 `AgentRuntime` 的构造参数或公开方法的签名，或改 `run_recon`、`dispatch_tool`、`ReconDeps`、`ReconStep` 的签名。
- §4.8 的细化与开工时的 05 冲突、且无法同时满足 05 与本 spec 的行为要求。
- M64 改变了测试读取清单的路径、打包方式或 pytest 配置，与 §0 的期望不同。

## 附录 A：`/tmp/m65/write_builtin_specs.py`

在仓库根目录运行；只在 T3 第 2 步用一次，不进仓库。

```python
"""一次性：把 v2 的提示词原文与工具清单写成三份内置代理定义。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from maa_api.agent import runtime
from maa_api.agent.recon import runner
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS

OUT = Path("maa_api/agent/specs/builtin")
CHAT_SHA = "664c320349ef13cf3fdbc1f1d6969b85eebf7c7ffe980cd656c1b03381b72536"
RECON_SHA = "c321d4483a9824cdd517d8844a37a558c4d3269a2f51151c45d960be214b9bda"
INCIDENT_TOOLS = [
    "get_system_status", "get_device_status", "get_logs", "get_screenshot", "get_pipeline", "get_queue",
    "list_pipelines", "get_drop_stats", "read_knowledge", "stop_pipeline", "reconnect_device", "back_to_home",
]
TAIL = ["delegates: []", "skills: []"]
END = ["budget: {max_cost_per_run: 0}", "version: 1"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def flow(names: list[str]) -> str:
    rows = [", ".join(names[i : i + 6]) for i in range(0, len(names), 6)]
    return "[" + ",\n  ".join(rows) + "]"


def write(name: str, lines: list[str], body: str) -> None:
    path = OUT / f"{name}.md"
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + body + "\n", encoding="utf-8")
    print("wrote", path)


assert sha(runtime.SYSTEM_PROMPT) == CHAT_SHA, "SYSTEM_PROMPT 与 spec 记录的不一致，停下来问"
assert sha(runner.SYSTEM_PROMPT) == RECON_SHA, "侦察提示词与 spec 记录的不一致，停下来问"
assert frozenset(INCIDENT_TOOLS) == runtime.INCIDENT_ALLOWED_TOOLS
chat_tools = [definition.name for definition in build_registry().list()]
assert len(chat_tools) == 84
OUT.mkdir(parents=True, exist_ok=True)
write("legacy_chat", [
    "name: legacy_chat", "title: 旧版会话", "kind: worker",
    "description: 旧版会话的代理：沿用原系统提示词与全部工具，扩展工具组按会话开启。只服务旧版会话，不用于工单。",
    "model_role: worker", f"tools: {flow(chat_tools)}",
    f"deferred_groups: [{', '.join(sorted(DEFERRED_TOOL_GROUPS))}]",
    "device: exclusive", "risk_ceiling: destructive", "max_steps: 0", *TAIL, "output_schema: null", *END,
], runtime.SYSTEM_PROMPT)
write("legacy_incident", [
    "name: legacy_incident", "title: 旧版诊断", "kind: worker",
    "description: 旧版自动诊断会话的代理：系统提示词同旧版会话，只开放 12 个安全查询与恢复工具。",
    "model_role: worker", f"tools: {flow(INCIDENT_TOOLS)}", "deferred_groups: []",
    "device: exclusive", "risk_ceiling: none", "max_steps: 0", *TAIL, "output_schema: null", *END,
], runtime.SYSTEM_PROMPT)
write("recon", [
    "name: recon", "title: 侦察", "kind: subagent",
    "description: 只读侦察：在工作区里搜代码、读文件、查本地 git，最后回一份带证据的报告。",
    "model_role: subagent", "tools: [list_files, read_file, search_code]", "deferred_groups: []",
    "device: none", "risk_ceiling: none", "max_steps: 8", *TAIL, "output_schema: subagent_report", *END,
], runner.SYSTEM_PROMPT)
print("OK")
```

## 附录 B：行为对齐测试 `tests/agent/harness/test_harness_parity.py`

写 spec 时已在 dev（`06e12b1`）上用同样的结构实跑：16 个场景录制约 1 秒，连跑三次与 golden 一致，golden 约 90 KB。测试不能导入 `maa_api.agent.harness` 与 `maa_api.agent.specs`（搬家前它们还不存在）；门面与 recon 只用公开名字：`AgentRuntime`、`INCIDENT_ALLOWED_TOOLS`、`ReconDeps`、`ReconStep`、`budget_from_settings`、`run_recon`。

骨架（其余照此补齐）：

```python
GOLDEN_PATH = Path(__file__).parent / "golden" / "legacy_parity.json"
REGENERATE_ENV = "MAA_REGENERATE_PARITY"
SESSION_ID, MODEL, BASE_URL = "parity", "parity-model", "https://llm.test/v1"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NOW = re.compile(r'"now": "[^"]*"')
class _Normalizer:
    """每次运行都会变的值换成稳定占位；同一场景里同一个值总是同一个占位。"""
    def __init__(self, literals: dict[str, str] | None = None) -> None:
        self._literals = sorted((literals or {}).items(), key=lambda item: -len(item[0]))   # 长的先换
        self._uuids: dict[str, str] = {}
        self._audits: dict[int, str] = {}
    def text(self, value: str) -> str:            # 先换 literals，再把 "now" 换成 <now>，再按出现顺序把 uuid 换成 <uuid:n>
    def audit(self, value: Any) -> Any:           # int 按出现顺序换成 <audit:n>，其余原样
    def value(self, data: Any) -> Any:            # 递归：键 expires_at 非空时换成 <ts>；键 audit_id 走 audit()；字符串走 text()
def _request(model, messages, tools, norm) -> dict:
    # messages 先规范化；第一条若是字符串内容的 system，换成 {"role": "system", "sha256", "length", "tail": 最后 300 字}
    return {"model": model, "messages": ..., "tools": norm.value(tools)}
class _Harness:
    def __init__(self, tmp: Path) -> None: ...    # 库用 f"sqlite+aiosqlite:///{tmp / 'parity.sqlite3'}"
    async def setup(self, responses, *, registry=None, questions=False, vision=True, llm=None, **runtime_options): ...
        # create_all；ConfirmationService(factory, registry, policy=PolicyEngine())；questions 时 QuestionService(factory, timeout_seconds=60)；
        # AgentRuntime(factory, confirmations, settings_provider=lambda _r: settings, client_factory=lambda _s: self.llm,
        #              context_provider=lambda _r: {"device": "connected"}, knowledge_service=<固定文本>,
        #              confirmation_poll_seconds=0.01, **runtime_options)；预置 _capability_cache[(MODEL, BASE_URL)]；建会话 SESSION_ID
    async def stream(self, content, on_event=None) -> list[str]: ...
        # acquire_session 后迭代 stream_message(SESSION_ID, content, None)；每个非注释帧 parse_events 后交给 on_event
    async def capture(self, frames, norm, *, result=None) -> dict: ...
        # sse: [[事件, 规范化 data]]（parse_events 已排除 keep-alive）；requests: [_request(...)]；
        # messages: 按 seq 取 id、seq、role、content、reasoning_content、tool_calls、tool_call_id、tool_name、audit_id、
        #   prompt_tokens、completion_tokens、finish_reason（不取 latency_ms、created_at）；session: title 与三个计数；
        # audits: tool_name、status、error_code、risk_level、caller、result_summary，按 JSON 文本排序；result 可选
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_legacy_behaviour_matches_the_recorded_golden(name: str, tmp_path: Path) -> None:
    captured = json.loads(json.dumps(asyncio.run(SCENARIOS[name](tmp_path)), ensure_ascii=False, sort_keys=True, default=str))
    if os.environ.get(REGENERATE_ENV) == "1":
        # 读出已有 golden（没有就 {}），写入 data[name]，按 indent=1、sort_keys=True、ensure_ascii=False 写回并补换行
        return
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert captured == golden[name]
```

两条实测得来的硬要求：会话库必须用 `tmp_path` 下的文件库（内存库加 `StaticPool` 时，拒绝确认的场景会挂死）；step 脚本与假客户端记录 messages 时用 `json.loads(json.dumps(...))` 深拷贝。

16 个场景（假工具用一个 `_tool(name, *, risk, read_only, params, group, model_visible)` 工厂造，handler 返回 `{"tool": name, "arguments": params.model_dump()}`）：

| 场景 | 注册表 | FakeLLM 脚本 | 驱动 | 覆盖 |
|---|---|---|---|---|
| `text_turn` | 空 | `text_response("你好", prompt_tokens=19, completion_tokens=3)` | `stream("早上好")` | 文本轮、用量、标题 |
| `parallel_read_only_batch` | `lookup`（只读，参数 `key`） | 一批 `lookup` 两个（call-1 a、call-2 b），再文本 | stream | 只读批并发 |
| `serial_write_with_reasoning` | `write_value`（非只读，group ops） | 第一步 content「先写一下」与 reasoning「要先写值」再加一个调用，再文本 | stream | 思考落库、串行批 |
| `confirmation_approved` | `click`（DANGEROUS，`ClickParams`，group raw） | `click` x=31 y=42，再文本 | 收到 `confirmation_required` 即批准 | 原子操作授权 |
| `confirmation_rejected_skips_rest` | `drop_value`（DANGEROUS，group ops） | 一批两个 `drop_value` | 收到 `confirmation_required` 即拒绝，reason「不要」 | 拒绝、跳过、收尾 |
| `ask_user_answered` | 真 interaction 组；`questions=True` | `ask_user`，再文本 | `question_required` 时回答「官服（Official）」 | 提问链路 |
| `ask_user_skipped` | 同上 | `ask_user` | `question_required` 时 skip | `question_unanswered` |
| `stuck_loop` | `lookup` | 5 个相同的批（只换 id） | stream | 第 3 批后 `stuck_loop` |
| `malformed_arguments` | `lookup` | arguments 为 `{"broken":` | stream | 坏 JSON |
| `vision_and_hidden_gates` | 假 `get_screenshot`、`annotate_screenshot`、`trigger_screencap`（`model_visible=False`）、`lookup`；`vision=False` | 一批：`trigger_screencap`、`annotate_screenshot`、`click`（未注册） | stream | 三道闸门 |
| `llm_timeout_notice` | 空 | `AppError(LLM_TIMEOUT, "LLM 上游请求超时")` | stream | 失败说明 |
| `context_overflow_notice` | 空；`max_context_chars=10` | 文本 | stream | 溢出说明 |
| `interrupt_mid_stream` | 空；先吐一段文字再挂住的假客户端 | — | 收到 `text_delta` 后等它挂住，再 `interrupt_turn` | 中断收尾 |
| `incident_run` | `get_logs`（只读）、`submit_pipeline`（DANGEROUS） | 一批两个调用，再文本 | 先插一条 user 消息，再 `run_incident(SESSION_ID, None, INCIDENT_ALLOWED_TOOLS \| {"submit_pipeline"})`，结果放进 `result` | 诊断白名单 |
| `recon_step_script` | 记录型三件套 | 第一步 `list_files` 加 `delegate_recon`，第二步 JSON 报告 | `run_recon(..., step_runner=script)`，记下 `seen` 与报告 | recon 过滤与报告 |
| `recon_runtime_budget` | 真 recon 三件套 | 每次都回一个 `list_files` 调用的假客户端 | `AgentRuntime(None, None, ...).recon("列一下文件")`，`recon_max_steps=2`，记下请求与报告 | 默认步进与步数预算 |

两个 recon 场景的工作区是 `tmp_path`，规范化时把 `str(tmp_path.resolve())` 与 `str(tmp_path)` 都换成 `<ws>`（`list_files` 的结果里有绝对路径）。

## 需要补进契约的名字

| 名字 | 位置 | 用途 |
|---|---|---|
| `maa_api/api/routers/agent_specs.py` | 02 §4.1 | 两个代理定义端点 |
| `SpecList`、`SpecBudget`；`SpecSummary.spec_version` | 04 §2.1 | 列表外层与预算；内容哈希 |
| `AgentSpecFrontmatter`、`AgentSpecBudget`；`AgentSpec.body`、`spec_version`、`source_file`；`SPEC_NAME_PATTERN`、`AGENT_KINDS`、`MODEL_ROLES`、`DEVICE_LEVELS`、`RISK_CEILINGS` | `specs/schema.py`，05 §1 | 定义的数据形状 |
| `AgentSpecError`、`parse_spec_file`、`load_builtin_specs`、`builtin_specs`、`builtin_spec`、`AgentSpecRegistry.load/list/get`、`DEFERRED_GROUP_SPECS` | `specs/registry.py`，05 §1.2 | 加载与查询 |
| `TurnDraft`、`Usage`（`prompt_tokens`、`completion_tokens`）、`StopRun` | `harness/context.py`，05 §2.1 | 中断补历史、用量、中间件提前结束 |
| `RunContext.interrupted_now`、`allowed_tools`、`vision_enabled`、`parallel_tools`、`draft`、`pending_stop`、`stop_after_batch()` | 05 §2.1 | 见 §4.2.4 |
| `ModelResponse.raw_tool_calls`；`ToolOutcome.stop_text`、`content`、`body()` | 05 §2.1 | 原样落库的调用；收尾文本；recon 的 tool 正文；v2 结果体 |
| `MiddlewareChain.for_kind`、`names`；`ALL_AGENT_KINDS`、`CallNext` | `harness/middleware.py`，05 §5.1 | 按种类裁剪链 |
| `RunEvent(name, data, run_id)`、`RunEventBus.subscribe/emit`、`RunEventSink`、`sse_event` | `harness/events.py`，05 §2 | 事件出口 |
| `merge_tool_call_deltas`、`chunk_tool_calls`、`parse_tool_call`、`parse_arguments`、`collect_completion` | `harness/chunks.py` | 流式解析 |
| `execute_batch`、`app_error_wire`、`json_text`、`SCREEN_TOOL_NAMES`、`vision_disabled_message`、`HIDDEN_TOOL_MESSAGE`、`TOOL_NOT_ALLOWED_MESSAGE`、`SKIPPED_AFTER_REJECTION_MESSAGE`、`CONFIRMATION_TERMINAL_CODES`、`CONFIRMATION_TERMINAL_TEXT` | `harness/toolexec.py`，05 §2 | 工具批执行 |
| `Transcript`、`MemoryTranscript`、`ModelCall`、`ToolSchemaProvider`、`select_stop`、`INTERRUPTED_FINISH_REASON`、`INTERRUPTED_NOTICE`、`INTERRUPTED_TOOL_STUB`、`INVALID_TOOL_CALL_TEXT` | `harness/loop.py`，05 §2 | 循环的可替换部件与收尾 |
| `archive_screenshot_result`、`request_id_of`、`calls_signature`、`exempt_from_stuck_loop`、`STUCK_LOOP_TEXT` | `harness/builtin_middleware.py` | 中间件的辅助函数 |
| `ReconBudgetMiddleware`（name `recon_budget`）、`ReconDispatchMiddleware`（name `recon_dispatch`） | `recon/runner.py`，05 §5.2 | M65–M69 的 recon 链 |
| `_LegacyTranscript`、`AgentRuntime._legacy_chain()`、`_run_turn(spec_name=…)` | `runtime.py`（门面内部） | v2 会话接 AgentLoop |
| `ToolDefinition.device` 的取值常量 `DEVICE_LEVELS` | `maa_api/agent/registry.py`，05 §6 | 与 schema 同值 |
| recon 运行的内部 finish_reason `budget` | 05 §2.3 | 只在内存里映射成 `stopped=budget`，不落库 |
| `tests/agent/harness/golden/legacy_parity.json`、环境变量 `MAA_REGENERATE_PARITY` | 测试 | 行为对齐基线与重录开关（只在 T1 用） |
