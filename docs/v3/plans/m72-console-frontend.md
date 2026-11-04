# M72 指挥台前端 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把产品形态切到指挥台：首页 `/` 是总控对话 + 「待你处理」+ 进行中工单；`/tickets` 与 `/tickets/:id` 是工单列表与工单页；`/more/agents` 只读代理定义；v2 Agent 页变 `/more/sessions` 只读查看器（`/agent*` 重定向）；后端删四个 v2 写端点与 `AgentRuntime.stream_message`；输入栏支持 `/stop` `/tickets` `/help` 与五类 @ 提及（服务端展开成【引用】段）；WS `agent_run`/`ticket_status`/`lease_changed` 驱动 query 失效。

**Architecture:** 后端先做（G1–G3：删除 v2 写路径与测试驱动、旧会话列表/wire 字段、提及展开），再冒烟适配（G4），然后前端分层（G5 基础、G6 会话核心、G7 输入辅助、G8 指挥台、G9 工单页、G10 代理定义页、G11 导航/路由/旧会话查看器/设置），最后 T12（我：文档、全量验收、合并、tag）。

**Tech Stack:** FastAPI（后端删除与 wire 字段）、React 19 + Vite + query（前端）、vitest/Testing Library、pytest。

**Spec:** [m72-console-frontend](../specs/m72-console-frontend.md)（1199 行）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `console-frontend`，基点 `dev` = `86b33c8`（M71 合并）；tag `v3-m70`/`v3-m71` 在。
- A1（21 个端点）`MISSING 0`；A2 七个 schema 全在（属性名已抄：`TicketSummary`[created_at, ended_at, human_action, id, kind, last_report_status, origin, priority, status, summary, title, updated_at]；`TicketDetail`[lease, mandate, recent_reports, result, subscriptions, ticket, turn_running, worker_session_id]；`RunSummary` 25 属性；`RunDetail`[..., children]；`SpecSummary`、`SpecDetail`、`Mention`）。
- §0 #5/#6/#7/#8/#9/#10/#12/#13/#14 全部符合预期：
  - `mentions` 写入点：`api/routers/orchestrator.py:108`、`api/routers/tickets.py:403`、`agent/orchestrator/service.py` 的 `post_user_message(mentions)`；#8 的 v3 消息模型 `api/schemas/agent_v3.py:215` 的 `AgentMessageItem` 与构造函数 `agent_message_item`。
  - #12：`AgentCapabilityCheck.tsx`、`settings.test.tsx` 仍在调 `capabilities/check` → T11 第 7 步执行（改 probe 端点）。
  - #13：`runs`/`modelRoles`/`specs` key 已在；`more/runs`、`more/costs`、`more/status` 路由与导航已在。
  - #14 `tests/fakes/legacy_turn.py` 不存在。
- **§0 #11 的规格缺口（裁决，记 13 §8）**：`stream_message` 除路由与测试外，还有 `maa_api/agent/evals/runner.py` 调用（评估驱动 v2 场景）。裁决：把方法体抽成模块级 `maa_api/agent/legacy_turn.py::stream_legacy_turn(runtime, session_id, content, request)`，evals runner 与测试驱动 `tests/fakes/legacy_turn.py` 都复用它；`AgentRuntime.stream_message` 删除。这满足「v2 行为测试改由测试驱动跑」且评估基线不破。
- 基线：后端 `3 failed / 4462 passed`（3 条既有真资源失败）；前端 `78 files / 801 tests` 全过。

## Global Constraints

- **不新增**表、迁移、端点、错误码、配置项、工具、依赖；**不删**错误码；不改 v2 设计系统与 `Page` 框架、不改 `ConfirmationDock` 口径（只列确认）。
- 删除 v2 写端点后：`POST /api/agent/sessions` 405、`POST .../messages` 405、`POST .../interrupt` 404 `NOT_FOUND`、`POST /api/agent/capabilities/check` 404 `NOT_FOUND`。
- v2 行为测试数量不减（改由 `tests/fakes/legacy_turn.py` 驱动）；M65 parity 17 必须过；regression/capability 评测不低于 M71 基线（capability 6/6）。
- 前端：事件名用 SSE v3；没有 origin 的卡片渲染与请求保持同前；未知 SSE 事件忽略；断网/换页/换工单行为照 spec §4.5.8。
- 文案照 spec §4.6.7 逐字；中文标点（，。：；「」）。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；前端命令在 `web/`；长输出 `/tmp/m72/`。
- 不 push；提交 `<类型>(m72): <中文摘要>`；最后合并回 `dev` 打 `v3-m72`。

## Review Focus

1. **v2 行为不丢**：`stream_legacy_turn` 抽出的驱动与 evals runner 复用；`tests/api/test_agent_router.py` 删除的 6 条写端点用例由 `test_removed_v2_session_write_routes` 与驱动覆盖；`test_runtime.py`/`test_account_resources_flow.py` 的 v2 行为数量不减。
2. **wire 字段**：`session_wire.kind/archived_at`、`message_wire.run_id/inbox_id/inbox_kind/inbox_thread_key`、v3 消息 `inbox_kind/inbox_thread_key`；`GET /api/agent/sessions` 只列 chat/incident/归档总控（旧会话查看器）。
3. **提及展开**：五类输出格式、去重/上限 8/截断 400/读取失败不抛；工单消息给总控的 passive 摘要不带【引用】段；common.md 加一句后 capability 评测不降。
4. **实时副本去重**：`filterLiveHistory` 与 `userMessageArrived` 纯函数；流式回复在页面上只出现一次；中途加入、排队、就地确认/提问、停止与恢复。
5. **导航/路由**：五项 Tab；`/more/status` 运行状态、`/more/agents`、`/more/sessions`；`/agent*` 重定向带 query/hash；`/more` 三组。
6. **门禁**：全量 pytest 不新增失败、openapi/guide/前端类型快照同步、前端五步 0、两冒烟 `SMOKE OK`、regression/capability 通过率不降、断链 0、`git diff --check` 0。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `console-frontend`，跑 §0 16 条；#11 的 evals runner 缺口裁决与 #12/#13 分支选择记入本计划；提交计划。

### Task 1（G1）: 删除 v2 写端点与 `stream_message`（spec T1）

**Files:** `maa_api/api/routers/agent.py`、`maa_api/agent/runtime.py`、新建 `maa_api/agent/legacy_turn.py`、新建 `tests/fakes/legacy_turn.py`、`maa_api/agent/evals/runner.py`、`tests/agent/test_runtime.py`、`tests/agent/test_account_resources_flow.py`、`tests/api/test_agent_router.py`、`docs/v3/14`（§6）

- [ ] 把 `AgentRuntime.stream_message` 方法体抽成模块级 `stream_legacy_turn(runtime, session_id, content, request)`（`self`→`runtime`；模块级名字从 `runtime` 导入）；`tests/fakes/legacy_turn.py` 转导出；`evals/runner.py` 改调它；删除 `AgentRuntime.stream_message`（独占私有方法保留）。
- [ ] 删四个路由与只被它们用的请求模型、`_LockedSessionStreamingResponse`；`tests/` 下 `.stream_message(` 调用点改 `stream_legacy_turn(...)`；删 §5 T1 列出的 6 条用例；`test_agent_rest_contract...` 改用仓储直接建会话；新增 `test_removed_v2_session_write_routes`；14 文档 agent 小节删 4 行、计数与总数各 −4；重生成 OpenAPI。
- [ ] 验证：`pytest tests/agent/test_runtime.py tests/agent/test_account_resources_flow.py tests/api/test_agent_router.py tests/domain/test_route_inventory.py` → 0；`grep -rnw stream_message maa_api` 无输出；parity 17。
- [ ] 提交：`feat(m72): 删除 v2 写端点与 stream_message（测试驱动保留 v2 覆盖）`

### Task 2（G2）: 旧会话列表与 wire 字段（spec T2）

**Files:** `maa_api/db/repositories/agent.py`、`maa_api/api/routers/agent.py`、`maa_api/api/schemas/agent_v3.py`、`tests/api/test_agent_router.py`、`tests/api/test_orchestrator_router.py`

- [ ] `list_page(legacy_only=...)`、`load_inbox_meta`、`session_wire` 两字段、`message_wire` 四字段、v3 消息两字段；`list_sessions` 用 `legacy_only=True`；`list_session_messages` 批量取 inbox meta；重生成 OpenAPI。
- [ ] 验证：`pytest tests/api/test_agent_router.py tests/api/test_orchestrator_router.py` → 0；`dump_openapi --check` 0。
- [ ] 提交：`feat(m72): 旧会话列表与消息 wire 字段`

### Task 3（G3）: 提及展开（spec T3）

**Files:** 新建 `maa_api/agent/mentions.py`、`api/routers/orchestrator.py`、`api/routers/tickets.py`、`agent/orchestrator/service.py`（写入点）、`agent/specs/common.md`、新建 `tests/agent/test_mentions.py`、总控/工单消息路由测试

- [ ] 五类展开函数与 `render_reference_block`（去重、上限 8、截断 400、读取失败行、末尾「另有 N 条」）；接总控与工单写入点（工单给总控的 passive 摘要不带引用段）；common.md 加一句；补用例。
- [ ] 验证：`pytest tests/agent/test_mentions.py tests/api/test_orchestrator_router.py tests/api/test_ticket_messages_router.py` → 0；`agent_eval capability` 6/6。
- [ ] 提交：`feat(m72): @ 提及展开为引用段`

### Task 4（G4）: 冒烟脚本（spec T4）

**Files:** `scripts/agent_smoke.py`、`scripts/frontend_smoke.py`

- [ ] `_SmokeLLM` 按请求分派（探查/图片/队列）；`_check_builtin_conversation` 改总控 SSE v3 + 消息 + 运行用量；`_install_internal_invoke_probe` 加 `POST /__smoke/legacy-sessions`；`_check_failure_incident` 相对计数；`frontend_smoke.py` 深链/404/405/构建产物含「指挥台」。
- [ ] 验证：`agent_smoke.py` → `SMOKE OK`（frontend_smoke 在 T12 跑）。
- [ ] 提交：`feat(m72): 冒烟脚本切到指挥台接口`

### Task 5（G5）: 前端基础（spec T5）

**Files:** `web/src/api/keys.ts`、`realtime/events.ts`、`realtime/query-bridge.ts`、`realtime/query-bridge.test.ts`、`features/agent/stream.ts`、`Composer.tsx`、`messages.ts`、`format.ts`（+临时改 `AgentPage.tsx` 调用点）

- [ ] query keys、WS 事件类型与失效分支、`streamSse`（detach）、Composer 三 props、messages 泛型、TOOL_LABELS +9；补 query-bridge 新用例。
- [ ] 验证：`cd web && pnpm typecheck && pnpm test --run src/realtime src/features/agent/messages.test.ts` → 0。
- [ ] 提交：`feat(m72): 前端查询键、WS 失效与 SSE 基础`

### Task 6（G6）: 会话核心（spec T6）

**Files:** `web/src/features/console/conversation/` 全部（types/sse/live-turn/references/transcript-v3/card-resolvers/use-conversation/ConversationView/LiveTurnView/UserMessage/InboxEventCard/DelegateToolBar/ReportCallout/TicketLinkChip + 测试）、`features/agent/MessageViews.tsx`

- [ ] SSE 归一、实时副本 reducer（纯函数先测）、发送/排队/停止 hook、历史条目与历史视图组件；`TypingIndicator` 导出。
- [ ] 验证：`pnpm test --run src/features/console/conversation` → 0。
- [ ] 提交：`feat(m72): 会话核心（SSE 归一、实时副本、发送与停止）`

### Task 7（G7）: 输入辅助（spec T7）

**Files:** `web/src/features/console/conversation/{commands,composer-assist,mention-sources}.ts`、`ComposerAssist.tsx`、`HelpCard.tsx`、`ConversationComposer.tsx` + 测试

- [ ] 斜杠命令、@ 提及解析/候选源/面板、状态行优先级与发送按钮、帮助卡。
- [ ] 验证：`pnpm test --run src/features/console/conversation/composer-assist.test.ts` → 0。
- [ ] 提交：`feat(m72): 输入栏命令与 @ 提及`

### Task 8（G8）: 指挥台（spec T8）

**Files:** `web/src/features/console/`（api/attention/use-attention/AttentionList/AttentionPanel/AttentionSheet/AttentionSummaryBar/ActiveTicketsPanel/ActiveTicketsStrip/ConsolePage/test-utils + 测试）

- [ ] 测试设施与端点模拟；「待你处理」合并排序与来源文案；桌面两列/手机 Sheet；快捷开场/空态/菜单（重新开始、旧会话、运行状态）；移植 M21/M22/M23/M59/AG2/AG4/AG5 用例到 `ConsolePage.test.tsx`（事件名换 v3）。
- [ ] 验证：`pnpm test --run src/features/console` → 0。
- [ ] 提交：`feat(m72): 指挥台页`

### Task 9（G9）: 工单页（spec T9）

**Files:** `web/src/features/tickets/` 全部（api/format/页面/卡片/时间线/活动树/租约/状态徽章 + 测试）

- [ ] 列表筛选与加载更多；详情两列/手机 tab；汇报时间线、活动树（递归子运行）、委托卡、租约指示与释放、取消工单、结束态；发消息流；适配函数按 §0 #4 属性名核对。
- [ ] 验证：`pnpm test --run src/features/tickets` → 0。
- [ ] 提交：`feat(m72): 工单列表与工单页`

### Task 10（G10）: 代理定义页（spec T10）

**Files:** `web/src/features/agents/` 全部 + 测试

- [ ] 四组列表；详情基本信息/设备风险/可委派/工具/技能/提示词折叠；404 空态。
- [ ] 验证：`pnpm test --run src/features/agents` → 0。
- [ ] 提交：`feat(m72): 代理定义页`

### Task 11（G11）: 导航、路由、旧会话与设置（spec T11）

**Files:** `layout/nav.ts`、`SideNav.tsx`、`layout.test.tsx`、`routes/router.tsx`、`routes/more/{index,more.test}.tsx`、`routes/dashboard/index.tsx`、`features/agent/` 相关文件（`AgentPage`→`LegacySessionsPage`、`AgentPage.test`→`LegacySessionsPage.test`、types/SessionList/MessageViews/StatusAlerts）、`settings/SchemaSection.tsx`、`settings.test.tsx`、`AgentCapabilityCheck.tsx`

- [ ] 五项 Tab 与三组侧栏/「更多」页；路由与 `/agent*` 重定向；旧会话只读查看器（删写交互、`ConversationView`、只读说明）；`/more/status` 运行状态；SchemaSection 链接；能力检查改 probe 端点；测试按 §6 增删改。
- [ ] 验证：`cd web && pnpm typecheck && pnpm test --run` → 0；`grep -rn "streamMessage\|capabilities/check\|AgentPage" web/src`（排除 types）无输出。
- [ ] 提交：`feat(m72): 导航、路由、旧会话查看器与设置`

### Task 12（G12，我）: 全量验证、文档与台账（spec T12）

**Files:** `docs/v3/11`（仅不一致处）、`docs/v3/13 §8`、新建 `docs/v3/ledgers/m72-console-frontend.md`、`docs/v3/README.md`、`docs/v3/14`/`15`/`web/openapi.json` 复核

- [ ] 跑 §7 全部；写台账（capability 通过率、§0 #4 属性名、#12/#13 分支选择、#11 裁决）；13 §8 记偏离；README 状态行。
- [ ] 提交 `docs(m72): 台账、决策记录与状态行`；`git merge --no-ff console-frontend` → tag `v3-m72` → 删分支 → 合并后复验（全量 pytest + 前端五步 + 两冒烟 + 两评测 + 快照/断链）→ 汇报。
