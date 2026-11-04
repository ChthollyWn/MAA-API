# M74 摘要压缩与工具结果清理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新代理按 token 预算判断（窗口来自模型档案，`context.compaction_ratio` 阈值、`W − O` 溢出线）；总控与 Worker 自动压缩、`/compact` 与 `POST /api/agent/sessions/{id}/compact` 手动压缩；检查点落库累积可列出（迁移 `0028_compaction`）；压缩前确定性收集 durable 状态、摘要 ID 确定性补全；`summarizer` 后台代理输出 `compaction_summary`，模型不可用时自动退回机械摘要、手动返回 `COMPACTION_FAILED`；48 个工具标 `refetchable` 并按 6 往返成批清理旧结果；总控动态尾部带「进行中的工单」；前端显示压缩标记并可展开摘要、支持 `/compact`。

**Architecture:** G1 配置/枚举/错误码+模型与迁移 → G2 `refetchable` 赋值 → G3 摘要 schema+summarizer+common → G4 durable/clearing/预算+链位 6/8 → G5 压缩/预算/布局+链位 7+`app.state.compactor` → G6 REST 端点与事件 → G7 前端 → T9（我：长会话用例、文档、全量验收、合并 tag）。

**Spec:** [m74-compaction](../specs/m74-compaction.md)（748 行）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `compaction`，基点 `dev` = `b591046`（M73 合并）；tag `v3-m73`；迁移 head `0027_device_lease`。
- §0：1–4、6–16 全部符合；#5 `trim_history_to_char_budget` 在 `builtin_middleware.py` 152（调用）/304（定义）/1318（二处调用，legacy 路径），M74 只删非 legacy 分支的调用；#11 `<list_tickets>` = `TicketService.list`（tickets/service.py:377）；#12 `<run_start>`/`<run_finish>` = `RunService.start`/`finish`；#13 `ctx.emit` = `RunContext.emit(name,data)`（harness/events.py:106 转发）；#15 `<stream_views>` = `web/src/features/console/conversation/ConversationView.tsx`（总控与工单页共用）+ `transcript-v3.ts`，`<slash_table>` = `conversation/commands.ts`（`SLASH_COMMANDS`），SSE 分派 = `conversation/sse.ts`。
- 基线：全量 pytest 结果记台账（预期 3 条既有真资源失败）。

## Global Constraints

- **不新增**03 没有的表/列/索引；不加除 `COMPACTION_FAILED` 外的错误码；不改 legacy 定义的布局与字符预算（`legacy_chat`/`legacy_incident` 逐字节不变，parity 17 必须过）。
- **不压缩**子代理与后台代理（只做清理与溢出检查）；不改记忆/技能/回溯（M79/M78/M80）。
- 工具总数不变 **93**；`refetchable` 集合恰为 48 个且全部 `read_only=True`。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m74/`。
- 不 push；提交 `<类型>(m74): <中文摘要>`；最后合并回 `dev` 打 `v3-m74`。

## Review Focus

1. **token 预算替换字符预算**：新代理（非 legacy）走 `budget_for` + `load_history`；legacy 路径与 M73 请求逐字节相同（parity 17）；`DEFAULT_MAX_CONTEXT_CHARS`/`trim_history_to_char_budget` 只剩 legacy 引用。
2. **压缩正确性**：`select_upto_seq` 保留最近 `keep_recent_turns` 轮与 ≥4 个工具往返（长单轮保护缩到 4）；检查点在同一事务 `supersede_active`+`add`；累积（新摘要输入含旧摘要）；同一会话最多一个 active（不加唯一索引）。
3. **durable 与 ID**：Worker 的工单/委托/本工单待确认/本会话待回答提问；总控 `open_tickets`（open/waiting/blocked，20 个）；尾部「进行中的工单」段只在有进行中工单时插；`complete_refs` 确定性补全 7 类 ID。
4. **清理**：B=((R−K)//6)×6，R=8,K=3→0、9→6、14→6、15→12；只清 >200 字、可重取、非错误结果；三种占位格式；批间稳定；清理后仍超溢出线 → `LLM_CONTEXT_OVERFLOW` → `finish_reason=context_overflow`。
5. **摘要代理与降级**：`summarizer` 定义（background、max_steps 1、output_schema compaction_summary、budget 0）；自动失败两次 → 机械摘要 `degraded=true`、model 空、运行继续；手动失败 → 502 不写检查点；手动预算用完 → 429。
6. **接口与前端**：两个 operationId `agent_compact_session`/`agent_list_checkpoints`；SSE `compaction`、WS `compaction_created`；前端标记插在 `upto_seq` 之后、摘要可展开、`/compact` 三种 Toast；`compaction_created` 只失效对应 checkpoints key。
7. **长会话**：`test_orchestrator_long_session_stays_within_budget`（窗口 16000、200 条用户消息 + 20 条汇报、每次请求 estimated_tokens 低于溢出线、至少压缩一次、摘要含全部 20 个工单 ID）。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `compaction`，跑 §0 十六条；`<...>` 占位符的实际符号记入本计划；提交计划。

### Task 1（G1）: 配置项、枚举、错误码 + 模型与迁移（spec T1、T2）

**Files:** `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`maa_api/domain/enums.py`、`tests/domain/test_enums.py`、`maa_api/domain/errors.py`、`tests/domain/test_errors.py`、`docs/v3/14`（§4）、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0028_compaction.py`、新建 `maa_api/db/repositories/compaction.py`、`maa_api/db/repositories/agent.py`（加 `list_after_seq`）、`tests/db/test_models.py`、新建 `tests/db/test_compaction_migration.py`、新建 `tests/db/test_repositories_compaction.py`

- [ ] §4.4 三键（`context.compaction_ratio` 0.75/0.3–0.95、`keep_recent_turns` 2/1–10、`clear_tool_results_after` 3/1–20）与 `_FIELDS`/模板/ENV；`CompactionTrigger`/`CheckpointStatus` 与 `EXPECTED_VALUES`；`COMPACTION_FAILED`（502）+ 14 清单 +1 与计数。
- [ ] `CompactionCheckpoint` 表、迁移 `0028_compaction`（可逆）、`CompactionCheckpointRepository`、`AgentMessageRepository.list_after_seq`；`test_models.py` 六类登记表更新；迁移/仓储测试。
- [ ] 验证：`pytest tests/test_settings.py tests/api/test_settings_router.py tests/domain tests/db` → 0；`GET /api/settings/schema` 多 3 项。
- [ ] 提交：`feat(m74): 压缩配置、枚举、错误码与 0028 检查点表`

### Task 2（G2）: `refetchable` 赋值（spec T3）

**Files:** spec T3 表的 18 个工具文件（`tools/{status,stage_level,activity,game,account,estate,copilot_site,roguelike,pipeline,device,resource,ops,schedule,knowledge,catalog,__init__,tickets}.py`、`recon/explore.py`）、`tests/agent/test_tool_catalog.py`

- [ ] 「是」列 48 个工具加 `refetchable=True`；「否」列与不改文件保持默认 False。
- [ ] `EXPECTED_REFETCHABLE`（48 名字集合）与 `test_refetchable_tools_are_pinned`（集合相等且都 `read_only=True`）。
- [ ] 验证：`pytest tests/agent/test_tool_catalog.py` → 0；工具数仍 93/92。
- [ ] 提交：`feat(m74): 可重取工具标记`

### Task 3（G3）: 摘要 schema、summarizer 与共用约定（spec T4）

**Files:** 新建 `maa_api/agent/llm/schemas/compaction_summary.py`、`maa_api/agent/llm/schemas/__init__.py`、新建 `maa_api/agent/specs/builtin/summarizer.md`、`maa_api/agent/specs/common.md`、新建 `tests/agent/llm/test_compaction_summary_schema.py`、`tests/agent/specs/`（只改内置定义清单断言）

- [ ] `CompactionSummary` 与子模型（`extra="forbid"`、长度/枚举逐字照 §4.2.1），注册 `compaction_summary`；`summarizer.md` frontmatter 逐字（background、max_steps 1、tools/deferred_groups 空、output_schema、budget 0、version 1）与正文要点；`common.md` 末尾加【压缩摘要】段（逐字）；九→十六份登记同步（只改计数/名单）。
- [ ] 验证：`pytest tests/agent/llm tests/agent/specs` → 0；`summarizer.output_schema` 能在 `SCHEMAS` 找到。
- [ ] 提交：`feat(m74): 压缩摘要 schema 与摘要代理定义`

### Task 4（G4）: durable、清理与预算 + 链位 6/8（spec T5）

**Files:** 新建 `maa_api/agent/context/durable.py`、`maa_api/agent/context/clearing.py`、`maa_api/agent/context/tokens.py`（加 `ContextBudget`/`budget_for`，只加 §4.2.3）、中间件链装配处（`runtime.py` 的 `build_agent_chain`，按 05 §5.2 顺序放第 6、8 位；第 7 位留给 G5）、`maa_api/agent/harness/loop.py`（`LLM_CONTEXT_OVERFLOW` 终止映射）、`tests/agent/context/test_durable.py`、`tests/agent/context/test_clearing.py`

- [ ] `DurableState`/`collect_durable_state`/`render_durable`/`render_open_tickets`/`DurableContextMiddleware`；`tool_rounds`/`plan_clearing`/`apply_clearing`/`clearing_placeholder`/`ToolResultClearingMiddleware`；`ContextBudget`/`budget_for`；AgentLoop 映射 `context_overflow`；测试 §6 的 durable 两条与 clearing 六条。
- [ ] 验证：`pytest tests/agent/context` → 0。
- [ ] 提交：`feat(m74): durable 状态、工具结果清理与 token 预算`

### Task 5（G5）: 压缩与布局 + 链位 7 + `app.state.compactor`（spec T6）

**Files:** 新建 `maa_api/agent/context/compaction.py`、`maa_api/agent/context/layout.py`（加 `load_history`、断点原因）、`maa_api/agent/harness/builtin_middleware.py`（只改 `ContextLayoutMiddleware` 非 legacy 分支）、链装配处（插 `CompactionMiddleware` 第 7 位）、`maa_api/main.py`（`app.state.compactor = Compactor(...)` 放第 9 行之首）、`tests/agent/context/test_compaction.py`、`tests/agent/context/test_compaction_flow.py`

- [ ] §4.2.5 全部（常量、`CompactionResult`、`select_upto_seq`、`render_transcript`、`render_checkpoint_message`、`mechanical_summary`、`complete_refs`、`Compactor.compact/compact_session`、`CompactionMiddleware`）；`load_history` + 非 legacy 路径切换并删字符预算；`ContextLayout.apply` 断点原因。
- [ ] 测试 §6：`test_compaction.py` 六条 + `test_compaction_flow.py` 五条（含 legacy 逐字节不变、token 预算替代字符预算）。
- [ ] 验证：`pytest tests/agent` → 0；`grep -rn "DEFAULT_MAX_CONTEXT_CHARS\|trim_history_to_char_budget" maa_api/agent/harness maa_api/agent/context` 只剩 legacy 引用。
- [ ] 提交：`feat(m74): 摘要压缩与检查点`

### Task 6（G6）: REST 端点与事件（spec T7）

**Files:** 新建 `maa_api/api/routers/agent_sessions.py`、`maa_api/main.py`（`include_router`）、新建 `tests/api/test_agent_sessions_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`（§6）

- [ ] 两个端点（`POST /api/agent/sessions/{session_id}/compact`、`GET .../checkpoints`）、响应模型与 400/404/409/429/502 语义、operationId；SSE `compaction` 与 WS `compaction_created` 已由 G5 发出（端点只补 REST）；快照/类型/清单 +2 行与计数。
- [ ] 验证：`pytest tests/api tests/domain` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m74): 压缩与检查点端点`

### Task 7（G7）: 前端（spec T8）

**Files:** 新建 `web/src/features/compaction/**`（`api.ts`/`format.ts`/`CompactionMarker.tsx`/`insert-markers.ts`/`compaction.test.tsx`）、`web/src/features/console/conversation/{ConversationView.tsx,sse.ts,commands.ts}`（标记插入、SSE 失效、`/compact`）、工单页接入（`web/src/features/tickets/TicketDetailPage.tsx`）、`web/src/realtime/query-bridge.ts` 与测试、`web/src/api/keys.ts`

- [ ] 标记（`已压缩较早的消息（约 {before} → {after} token）`、`查看摘要`、degraded 徽章与提示）、`insertMarkers`、`formatTokens`；`/compact` 命令与三种 Toast；SSE/WS 失效 `checkpoints` key；测试三处。
- [ ] 验证：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` → `SMOKE OK`。
- [ ] 提交：`feat(m74): 前端压缩标记与手动压缩`

### Task 8（T9，我）: 长会话用例、文档与全量验收

**Files:** `tests/agent/context/test_compaction_flow.py`（加长会话一例）、`docs/v3/{02,03,04,05,13,14}`、新建 `docs/v3/ledgers/m74-compaction.md`、`docs/v3/README.md`

- [ ] `test_orchestrator_long_session_stays_within_budget`；§9 文档同步（03 §13 枚举、04 §2.3/§4.2、05 §5.2/§6/§3.4、02 §4.1/§5、14 错误码+1/路由+2）；13 §8 记「清理按 6 往返成批」等偏离；README；台账。
- [ ] 跑 §7 全部（全量 pytest 不新增失败、三个冒烟 `SMOKE OK`、regression/capability 不降、OpenAPI/指南/前端五步/断链/diff 检查）。
- [ ] 提交 `docs(m74): 台账、契约同步与状态行`；`git merge --no-ff compaction` → tag `v3-m74` → 删分支 → 合并后复验 → 汇报。
