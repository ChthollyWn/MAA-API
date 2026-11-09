# 移除消耗护栏与旧会话查看器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec 彻底删除 M84 消耗护栏（SpendGuard、账本表、三个配置、`GET /api/agent/spend`、简报消耗小节）与 M72 旧会话查看器（`/more/sessions` 页面、导航/标题栏入口、4 个 v2 只读端点），并同步现行文档与生成物。

**Architecture:** 一次维护性补丁、单分支 `remove-spend-legacy`。三个写者并行、写范围互不重叠：后端（Task 1 → Task 2 顺序做，因为都动 `api/routers/agent.py`）、前端（Task 3）、文档（Task 4）。后端完成后由主 agent 重生成 OpenAPI/前端类型/错误码指南并改 14 清单（Task 5），最后全量验证与合并（Task 6）。**所有 git 写操作（commit/switch/merge）只由主 agent 做，写者不碰 git。**

**Tech Stack:** FastAPI + SQLModel + Alembic + pytest；React 18 + TypeScript + TanStack Query + Vitest；OpenAPI 生成类型（`openapi-typescript`）。

**Spec:** [docs/v3/specs/remove-spend-legacy-sessions.md](../specs/remove-spend-legacy-sessions.md)

## Global Constraints

- 一律用 `.venv/bin/python`；没有 `ripgrep`，用 `grep -rn`；长输出重定向到 `/tmp/*.log`，只看 `echo $?` 与 `tail`。
- 写者不改 git（不 commit/switch/stash/merge）；只编辑文件并跑验证命令。主 agent 负责提交与合并。
- 不新增端点、事件、错误码、配置键、表；不顺手重构无关代码。
- 逐次确认、确认冒泡、`RISK_FIELDS`、成本账本 `cost_ledger`、每日简报本体、`DELETE /api/agent/sessions/{id}/atomic-grant|copilot-grant`、`/orchestrator/*`、v3 会话端点、诊断工单、`legacy_turn`/`legacy_incident` 回放全部不动。
- 历史文档 `docs/v3/specs/m84-brief-spend.md`、`docs/v3/plans/m84-brief-spend.md`、`docs/v3/ledgers/m84-brief-spend.md`、M72 的 spec/plan/台账一行不动。
- 中文文案照 spec 与既有 `docs/copy-audit/规范-文案规范.md`；提交信息用中文，类型前缀照仓库习惯（`refactor:`/`test:`/`docs:`）。
- 任务内验证只跑本任务相关命令；全量门禁由主 agent 在 Task 6 独占复跑。

## Review Focus

- **入队事务与锁序**：`QueueService.create` 删预留块后必须仍是「同一事务创建流水线+任务，提交前不放开准入锁」；并发入队/取消/幂等冲突用例不得变红。
- **中间件链位置**：删 `SpendMiddleware` 后 Mandate/Lease/Clarification/Report/Policy 的相对顺序不变；`test_v3_chain_puts_worker_middlewares_at_14_16_18...` 改成 14/15/17 且总控链不含它们。
- **v3 消息 wire 不被误删**：`message_wire` 只服务 v2 端点，但 `load_inbox_meta`、`AgentMessageRepository.list_page`、v3 `agent_v3.py` 的字段形状仍被 `/orchestrator/messages` 与 `/tickets/{id}/messages` 使用，删前逐个 grep 调用点。
- **旧书签与深链**：`/agent`、`/agent/*` 深链在删查看器后仍要走一个重定向到 `/`（`replace`），不能 404；`frontend_smoke.py` 的深链检查同步。
- **简报非消耗内容不变**：`brief/facts.py` 的「今日/昨日」仍按北京时间 04:00 分日（本地常量），理智、流水线、掉落、工单、成本、待处理、今日建议各节与 `meta` 键逐字不变；`brief_spend_smoke.py` 改名后仍从 `POST /api/agent/brief/run` 验到产物与工单。
- **迁移可回滚**：`0039` upgrade 删表、downgrade 按 `0037` 的 DDL 原样重建；迁移测试覆盖 upgrade→downgrade→upgrade。

---

### Task 1: 后端消耗护栏拆除（生产代码 + 迁移 + 测试）

**Files:**
- Delete: `maa_api/agent/spend.py`
- Delete: `maa_api/api/routers/spend.py`（`git` 外先复制），Create: `maa_api/api/routers/brief.py`
- Create: `maa_api/db/migrations/versions/0039_drop_spend_ledger.py`
- Create: `tests/db/test_drop_spend_ledger_migration.py`
- Modify: `maa_api/api/routers/agent.py`（只改 `invoke_tool` 的响应码列表与预检块，约 430–455 行；**不要碰文件下半部的会话端点**，Task 2 处理）
- Modify: `maa_api/agent/workers/middleware.py`、`maa_api/agent/runtime.py`、`maa_api/services/queue_service.py`、`maa_api/services/pipeline_runner.py`、`maa_api/agent/tools/pipeline.py`
- Modify: `maa_api/main.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`maa_api/db/models.py`
- Modify: `maa_api/agent/brief/facts.py`、`maa_api/agent/brief/render.py`
- Delete: `tests/agent/test_spend.py`、`tests/api/test_spend_router.py`、`tests/api/test_agent_invoke_spend.py`、`tests/services/test_queue_spend.py`、`tests/services/test_schedule_spend.py`、`tests/agent/harness/test_spend_middleware.py`、`tests/db/test_spend_ledger_migration.py`
- Modify: `tests/test_settings.py`、`tests/db/test_models.py`、`tests/api/test_agent_assembly_wiring.py`、`tests/agent/workers/test_worker_middleware.py`、`tests/agent/test_pipeline_tools.py`、`tests/agent/test_pipeline_runner_set_params.py`、`tests/agent/brief/test_facts_brief.py`、`tests/agent/brief/test_render_facts.py`
- Rename: `scripts/brief_spend_smoke.py` → `scripts/brief_smoke.py`（删消耗检查，保留简报检查）

**Interfaces:**
- Produces: `maa_api/api/routers/brief.py` 暴露 `router`（prefix `/api/agent`、tags `["agent"]`）与 `BriefRunResult`、`run_brief`，路径 `POST /api/agent/brief/run` 与旧实现逐字节一致；operationId 仍为 `agent_<函数名>`，函数名保持 `run_brief`。
- Produces: `maa_api/agent/brief/facts.py` 内的日界常量 `BRIEF_TIMEZONE = "Asia/Shanghai"`、`BRIEF_DAY_RESET_HOUR = 4` 与 `brief_day_window(now) -> tuple[datetime, datetime, date]`（语义同旧 `spend_day_window`：naive UTC 的 04:00 分日）。
- Produces: `Settings` 不再有 `spend` 字段；`ErrorCode` 不再有 `SPEND_CAP_EXCEEDED`；`SpendLedger`/`SpendKind` 不存在。

- [ ] **Step 1: 拆消费服务与调用点。** 删 `maa_api/agent/spend.py`；`QueueService` 删 `_spend_guard`/`bind_spend_guard`，`create` 里恢复「创建后 `await session.commit()`」单路径（保留原 `except IntegrityError` 分支）；`PipelineRunner` 删 `_spend_guard`/`bind_spend_guard`/`spend_source` 参数与 `reserve_patch` 块，恢复直接调用内核并落库；`agent/tools/pipeline.py` 的 `set_task_params` 去掉 `spend_source=` 实参；`api/routers/agent.py` 的 `invoke_tool` 删 `SPEND_CAP_EXCEEDED` 响应声明与 `spend_guard.precheck_tool_call` 块。删后 `grep -rn "spend\|Spend" maa_api --include=*.py | grep -v __pycache__` 只允许命中 `recon/runner.py` 的 `budget.spend`、迁移 0037/0038 与 `docs` 无关字符串。
- [ ] **Step 2: 拆中间件与链。** 删 `SpendMiddleware` 类、`SPEND_TOOLS` import、模块 docstring 条目与 `__all__`；`build_agent_chain` 删 `SpendMiddleware()` 与相关注释，docstring 的链位置改写成「14 Mandate、15 Lease、17 Report」；`test_worker_middleware.py` 的链位置断言改成 14/15/17。
- [ ] **Step 3: 拆配置、枚举、错误码与模型。** 删 `SpendSettings`、`Settings.spend`、settings 热点映射三行、`settings_schema` 三行、`config.template.yaml` 的 `spend:` 段、`SpendKind`、`SPEND_CAP_EXCEEDED`（枚举 + `ERROR_HTTP_STATUS`）、`SpendLedger` 模型与 `db/models.py` 的 5.29 注释段（后续小节编号不回填，留空号）。
- [ ] **Step 4: 新增迁移与迁移测试。** 新迁移 `revision="0039_drop_spend_ledger"`、`down_revision="0038_channels"`：upgrade 依次 `op.drop_index("ix_spend_ledger_kind_created_at", table_name="spend_ledger")`、`op.drop_table("spend_ledger")`；downgrade 按 `0037_spend_ledger.py` 的 DDL 原样重建表、索引与外键（名称一致）。新测试参照 `tests/db/test_spend_ledger_migration.py` 的写法：upgrade 后表不存在、downgrade 后表与索引重建、再 upgrade 再删。
- [ ] **Step 5: 拆简报里与账本相关的部分。** `brief/facts.py`：删 `spend_today`/`spend_yesterday` 字段、`_spend_block`、对 `maa_api.agent.spend` 的 import，新增本地日界常量与 `brief_day_window`，`collect_facts` 改用本地函数；模块 docstring 的日界说明保留但改指本地常量。`brief/render.py`：删 `_SPEND_ORDER`、`_today_spend_text`、`_spend_section`，`_SECTION_BUILDERS` 去掉一行。`test_facts_brief.py`/`test_render_facts.py` 删消耗字段用例（「昨日按消耗日」的日界用例保留、改成构造本地 facts 校验）。
- [ ] **Step 6: 拆路由与装配。** 把 `api/routers/spend.py` 的 `POST /brief/run`、`BriefRunResult`、`_brief_service`、`_require_ticket_service` 原样搬到新文件 `api/routers/brief.py`，删 `GET /spend`、`SpendSummary` 等全部消耗模型与 `_spend_guard`/`_validate_since`；`main.py` 改 import 与 `include_router(brief.router)`，删 `spend_guard` 装配块、`app.state.spend_guard`、两处清理名单里的 `"spend_guard"` 与局部变量声明。
- [ ] **Step 7: 拆测试与冒烟。** 按 Files 清单整删/改写；`scripts/brief_spend_smoke.py` 改名 `brief_smoke.py`（文件内 `[brief_spend_smoke]` 字样改 `[brief_smoke]`、docstring、argparse prog），删 `GET /api/agent/spend` 检查，保留 `POST /api/agent/brief/run`、产物、工单、简报正文检查。`grep -rn "brief_spend_smoke\|spend_guard\|SpendGuard\|SPEND_CAP_EXCEEDED\|daily_medicine_limit" scripts tests maa_api --include=*.py` 清零（迁移 0037/0038 与历史文档除外）。
- [ ] **Step 8: 验证并报告。** 跑：
  ```bash
  .venv/bin/python -m pytest -o addopts= -q tests/agent/brief tests/agent/workers tests/agent/test_pipeline_tools.py tests/agent/test_pipeline_runner_set_params.py tests/api/test_agent_assembly_wiring.py tests/db/test_models.py tests/db/test_drop_spend_ledger_migration.py tests/test_settings.py > /tmp/t1.log 2>&1; echo $?; tail -n 5 /tmp/t1.log
  ```
  期望退出码 0。报告里列出：删了哪些文件、迁移测试结果、`grep` 残留检查输出、仍会红但属于 Task 5 的用例（`tests/domain/test_route_inventory.py`）。

### Task 2: 后端旧会话只读端点拆除

**依赖：Task 1 完成（同一文件 `api/routers/agent.py`）。**

**Files:**
- Modify: `maa_api/api/routers/agent.py`（删 `list_sessions`、`get_session_detail`、`delete_session`、`list_session_messages` 四个路由函数；删 `session_wire`、`message_wire`、`_v2_session_or_404`、`active_copilot_grant_wire`、`_V2_SESSION_KINDS`、`_V2_SESSION_NOT_FOUND_MESSAGE`（删前 grep 确认无其它引用）；清理只被它们使用的 import。**保留** `revoke_atomic_grant`、`revoke_copilot_grant` 与 `/tools/{name}/invoke`）
- Modify: `maa_api/db/repositories/agent.py`（`AgentSessionRepository.list_page` 删 `legacy_only` 参数与过滤分支，`AgentSessionRepository.delete` 若删端点后无调用者则一并删除（grep 确认））
- Modify: `tests/api/test_agent_router.py`（删 `test_session_detail_reports_false_turn_running_without_a_mounted_runtime`、`test_v2_session_routes_ignore_v3_sessions`、`test_legacy_session_list_hides_v3_sessions`、`test_message_wire_exposes_run_and_inbox_fields`；`test_agent_rest_contract_includes_sessions_confirmations_and_read_only_messages` 删列表/详情/消息段、保留原子授权与协作者断言；`test_removed_v2_session_write_routes` 改成断言四个路径都不在 `set(app.openapi()['paths'])`、`POST /api/agent/sessions` 返回 404 `NOT_FOUND`）
- Modify: `scripts/agent_smoke.py`（删 `POST /__smoke/legacy-sessions` 路由与相关调用、会话列表/详情/消息检查；`_check_failure_incident` 等依赖该路由的地方改直连 repository 或用已保留的端点）

**Interfaces:**
- Produces: `set(app.openapi()['paths'])` 不含 `/api/agent/sessions`、`/api/agent/sessions/{session_id}`、`/api/agent/sessions/{session_id}/messages`；仍含 `/api/agent/sessions/{session_id}/atomic-grant`、`/copilot-grant`、`/compact`、`/checkpoints`、`/plan`、`/plan/approval`、`/rewind`。
- Produces: `AgentSessionRepository.list_page(*, page, size)` 不再接受 `legacy_only`；`message_wire`/`session_wire` 不存在。

- [ ] **Step 1: 删端点与只服务它们的辅助。** 先 `grep -rn "session_wire\|message_wire\|_v2_session_or_404\|active_copilot_grant_wire\|_V2_SESSION" maa_api tests --include=*.py` 记录调用点，再删除；`load_inbox_meta`、`AgentMessageRepository`、`AgentSession` 若仍被保留端点使用就保留 import。
- [ ] **Step 2: 改仓储。** 删 `legacy_only` 与 `or_`/`and_` 相关过滤；确认 `delete` 是否还有调用者，没有则删。
- [ ] **Step 3: 改测试与冒烟。** 按 Files 清单改写；`tests/api/test_routes...` 无关文件不动。注意 `test_agent_rest_contract...` 里 `AgentSessionRepository(...).create(...)` 直接建会话的写法可保留用于授权撤销断言。
- [ ] **Step 4: 验证并报告。** 跑：
  ```bash
  .venv/bin/python -m pytest -o addopts= -q tests/api/test_agent_router.py tests/api/test_orchestrator_router.py tests/api/test_tickets_router.py > /tmp/t2.log 2>&1; echo $?; tail -n 5 /tmp/t2.log
  ```
  期望 0；另跑 `.venv/bin/python scripts/agent_smoke.py > /tmp/t2-smoke.log 2>&1; echo $?; tail -n 3 /tmp/t2-smoke.log` 期望 0 且末行 `SMOKE OK`（若因 Task 5 的接口清单未改而路由断言红，记录在报告里）。

### Task 3: 前端两块删除与简报控件迁移

**Files:**
- Delete: `web/src/features/spend/`（`SpendPage.tsx`、`api.ts`、`spend.test.tsx`）
- Delete: `web/src/features/agent/LegacySessionsPage.tsx`、`LegacySessionsPage.test.tsx`、`SessionList.tsx`、`use-message-window.ts`
- Create: `web/src/features/brief/api.ts`（从 `features/spend/api.ts` 搬 `BriefRunResult`、`runBrief`、`useRunBrief`，import 路径不变）
- Modify: `web/src/features/settings/BriefControl.tsx`（import 改 `@/features/brief/api`）、`web/src/features/settings/sections.ts`（agent 分区 summary 去掉「、简报与消耗上限」→「与简报」）
- Modify: `web/src/features/console/attention.ts`、`use-attention.ts`、`AttentionList.tsx`、`test-utils.tsx`、`attention.test.ts`（删 `spend` 来源、`spendAttention*`、`SpendSummary` 假数据与端点；`originLabel` 的「来自旧会话」兜底改「来自后台运行」）
- Modify: `web/src/routes/router.tsx`（删 `SpendPage`/`LegacySessionsPage` lazy import 与 `more/spend`、`more/sessions` 路由；`/agent`、`/agent/*` 改为 `<Navigate to="/" replace />`，保留 `SessionsRedirect` 形态函数或直接内联；删不再使用的 import）
- Modify: `web/src/components/layout/nav.ts`（删「旧会话」「消耗护栏」两项与不再使用的 icon import）、`layout.test.tsx`、`web/src/routes/more/more.test.tsx`
- Modify: `web/src/features/console/ConsolePage.tsx`（标题栏操作菜单删「旧会话」项、「重新开始」确认文案改为「当前会话会归档；进行中的工单不受影响。」）、`ConsolePage.test.tsx`
- Modify: `web/src/realtime/query-bridge.ts`、`query-bridge.test.ts`、`client.test.ts`（删 v2 会话缓存失效分支与用例）
- Modify: `web/src/api/keys.ts`（删 `agent.sessions`、`agent.session`、`agent.messages`、`agent.spend`；`agent.messages` 若仍被 v3 使用则保留，grep 确认）
- Modify: `scripts/frontend_smoke.py`（深链检查删 `/more/sessions`、`/more/spend`、`/agent?session_id=x`，保留其余深链；删 `POST /api/agent/sessions` 405 等已不存在端点的断言，保留仍在的 404/405 断言）
- 共享组件（**保留**）：`features/agent/` 下 `Composer.tsx`、`MessageViews.tsx`、`QuestionCard.tsx`、`ConfirmationCard.tsx`、`types.ts`、`messages.ts`、`transcript.ts`、`format.ts`、`origin.ts`、`media.ts`、`markdown.tsx`、`chart/`、`use-conversation-scroll.ts`、`StatusAlerts.tsx`、`stream.ts` 及其测试。

**Interfaces:**
- Consumes: Task 1 保留的 `POST /api/agent/brief/run` 响应形状不变（`{ticket_id, artifact_id}`，生成类型在 Task 5 重生成后仍存在）。
- Produces: `web/src/features/brief/api.ts` 导出 `runBrief`、`useRunBrief`（签名同旧 `features/spend/api.ts`）。
- Produces: 源码中 `grep -rn "features/spend\|LegacySessions\|more/sessions\|more/spend\|agent.sessions\|agent.session(" web/src --include=*.ts --include=*.tsx` 无输出（测试辅助与注释也清）。

- [ ] **Step 1: 迁移简报控件。** 新建 `features/brief/api.ts` 搬 `runBrief`/`useRunBrief`（含缓存失效逻辑与注释改写：去掉 M84 消耗护栏引用），`BriefControl.tsx` 与测试改 import；删 `features/spend/`。
- [ ] **Step 2: 删旧会话页面与入口。** 删四个文件；改 router、nav、more 测试、layout 测试、`ConsolePage` 标题栏与确认文案及测试。
- [ ] **Step 3: 删指挥台消耗来源与 keys/实时失效。** 按 Files 清单改 `attention*`、`use-attention`、`AttentionList`、`test-utils`、`keys.ts`、`realtime/*`；`originLabel` 兜底文案同步 `attention.test.ts`。
- [ ] **Step 4: 收尾冒烟脚本与残留。** 改 `scripts/frontend_smoke.py`；跑 `cd web && pnpm typecheck > /tmp/t3-tc.log 2>&1; echo $?; tail -n 20 /tmp/t3-tc.log`，期望 0。
- [ ] **Step 5: 跑前端测试。** `cd web && pnpm test --run > /tmp/t3-test.log 2>&1; echo $?; tail -n 15 /tmp/t3-test.log` 期望 0（记录文件数/用例数）。注意：`openapi.json` 由 Task 5 重生成，本任务不要跑 `gen:api:check`。
- [ ] **Step 6: 构建。** `cd web && pnpm build > /tmp/t3-build.log 2>&1; echo $?; tail -n 5 /tmp/t3-build.log` 期望 0。报告删除/修改文件清单与测试计数。

### Task 4: 现行文档同步

**Files（只改这些现行文档；14/15 与 13 由主 agent 处理）：**
- `docs/v3/01-总览与范围.md`、`02-系统架构.md`、`03-数据模型.md`、`04-接口与事件协议.md`、`05-运行时与代理定义.md`、`09-安全与治理.md`、`10-可观测与评测.md`、`11-前端指挥台.md`、`12-实施路线与里程碑.md`、`16-部署指南.md`、`docs/v3/README.md`

**Interfaces:**
- Consumes: spec §4.5 的文档同步表（逐行照做）；实现事实：迁移 `0039_drop_spend_ledger`、路由文件 `api/routers/brief.py`、冒烟脚本改名 `scripts/brief_smoke.py`、中间件新位置 Mandate 14 / Lease 15 / Clarification 16 / Report 17 / Policy 18、`GET /api/agent/spend` 与四个 v2 会话端点删除、`/agent` 深链重定向到 `/`。
- Produces: 现行文档不再描述两块已删功能；历史段落保留处标注「2026-11-09 删除，见 13 §8.175」。

- [ ] **Step 1: 09 与 11 两块主文档。** 09 删 §6 整节并改 §1.2/§12 威胁表相关行；11 按 spec §4.5 表格逐项删改（§1 两处、§3 分组说明、§3.2 消耗行与排序句、§4 标题栏三项改两项、路由表两行、§8 整节、§9 两行、§10 一句、§11 文案表相关行；「来自旧会话」改「来自后台运行」）。
- [ ] **Step 2: 架构/数据/协议/运行时。** 02 删 `SpendGuard`、`spend.py`、旧会话目录说明，路由清单 `spend.py`→`brief.py`，装配顺序删 `spend_guard`；03 迁移历史加 0039 并标注 0037 已删表，删 §11.6；04 删 M72/M84 端点行、`SPEND_CAP_EXCEEDED` 错误码行、三个配置键行、§134 说明句；05 中间件链表重编号并删 Spend 行，全文「旧会话查看器」相关句改。
- [ ] **Step 3: 路线/部署/总览/README。** 12 的 M72/M84 行加删除注记、验收命令里 `brief_spend_smoke.py` 改 `brief_smoke.py`；16 §20 标题与内容删消耗上限段；01 里程碑能力表与红线句；README 决策表删消耗护栏行。
- [ ] **Step 4: 断链与残留检查。** `.venv/bin/python scripts/check_doc_links.py > /tmp/t4-links.log 2>&1; echo $?; tail -n 3 /tmp/t4-links.log` 期望末行 `BROKEN 0`；`grep -rn "消耗护栏\|SpendGuard\|spend_ledger\|/more/sessions\|/more/spend\|旧会话查看器" docs/v3/01*.md docs/v3/02*.md docs/v3/03*.md docs/v3/04*.md docs/v3/05*.md docs/v3/09*.md docs/v3/10*.md docs/v3/11*.md docs/v3/12*.md docs/v3/16*.md docs/v3/README.md` 只允许历史标注句命中。报告改动文件与行数。

### Task 5: 契约生成物与 14/15 清单（主 agent）

**Files:** `web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`

- [ ] **Step 1:** 后端两个任务完成后跑 `.venv/bin/python scripts/dump_openapi.py`，再 `cd web && pnpm gen:api`，最后 `.venv/bin/python scripts/generate_api_guide.py`。
- [ ] **Step 2:** 手改 `docs/v3/14-现行接口清单.md`：删 `GET /api/agent/spend` 与四个 v2 会话端点行、`SPEND_CAP_EXCEEDED` 行；agent 小节计数 84→79、错误码总数 169→168、HTTP 端点总数 187→182；改写「v2 会话只读（M72）」说明段。
- [ ] **Step 3:** 验证 `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`、`.venv/bin/python -m pytest -q tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py` 全部为 0。

### Task 6: 全量验证、决策记录与合并（主 agent）

- [ ] **Step 1:** 三个写者全部停止后，独占复跑 spec §7 全部命令：后端全量 pytest、前端 typecheck/test/build、三处生成物检查、`agent_smoke.py`、`brief_smoke.py`、`frontend_smoke.py`、`orchestrator_smoke.py`、`check_doc_links.py`、残留 grep、`copy_inventory.py --check`（回潮则 `--write-baseline` 并提交 `docs/copy-audit/`）。
- [ ] **Step 2:** 写 `docs/v3/13-决策记录.md` §8.175（用户决定、删除范围、迁移、保留项、实施裁决）与台账 `docs/v3/ledgers/remove-spend-legacy-sessions.md`（分支、提交、验证证据、Rulings）。
- [ ] **Step 3:** 提交并 `git merge --no-ff remove-spend-legacy` 回 `dev`；删除临时分支；报告最终状态。

## 依赖与并行

```
Task 1 ──> Task 2 ──┐
                    ├──> Task 5 ──> Task 6
Task 3 ─────────────┤
Task 4 ─────────────┘
```

- 写范围：Task 1 = 后端消耗护栏文件与 `tests/`/`scripts/brief_smoke.py`；Task 2 = `api/routers/agent.py`、仓储、`test_agent_router.py`、`scripts/agent_smoke.py`；Task 3 = `web/src/` 与 `scripts/frontend_smoke.py`；Task 4 = 现行 docs（除 13/14/15 与历史 spec/plan/ledger）。互不重叠；Task 1 与 Task 2 必须由同一写者顺序做。
