# M70 总控与 Worker 运行 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让工单动起来：常驻总控会话 + Worker 会话与运行、收件箱转消息与唤醒、`create_ticket`/`report`/`delegate` 等七个工具、委托风险上限、三种 Worker 中间件与设备门、SessionPump 并发闸、DelegationService 并行子代理、SSE v3 与 `agent_run` WS、FinishHookFanout（流水线终态→话题→收件箱）、总控/工单九条端点与 lifespan 装配。**无迁移。**

**Architecture:** G1 配置/错误码 + FinishHookFanout → G2 运行时接缝补齐 → G3 共用约定/角色卡/系统提示分层 → G4 六份代理定义与 schema + 七个工具 → G5 三个 Worker 中间件与设备门 → G6 SessionPump 与并发闸 → G7 WorkerService 与 TicketTitler → G8 DelegationService → G9 OrchestratorService → G10 SSE v3 流与 WS 广播 → G11 路由与 lifespan 装配 → G12 端到端测试（连跑 3 次）→ T15 文档与合并。

**Tech Stack:** 现有 M65–M69 全部基础（AgentLoop、RunService、ModelRouter、SpanRecorder、TicketService/ThreadRouter/InboxService、settings、FastAPI SSE/WS、pytest）。

**Spec:** [m70-orchestrator-workers](../specs/m70-orchestrator-workers.md)（1467 行；每个任务照 spec 对应小节做）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `orchestrator-workers`，基点 `dev` = `edc31f5`（M69 合并）；tag `v3-m69`；无迁移（本里程碑不建表）。
- M65–M69 接缝全部在位（§0 #3–#10 全过）；工具数 `84 83`；内置定义三份；`bind_finish_hook(upgrade_plans...)` 在 `main.py`；`agent_session_id=ctx.session_id` 在 pipeline 工具。
- §0 #12 的 grep 命中均来自 M65/M68/M69 的既有名字（`SpanKind.DELEGATE`、`ReportStatus.REPORT`、`DELEGATE_TOOLS`），不是 M70 新名字；§0 #16 无匹配 → T13 第 6 步要完整补「v2 会话路由按 kind 过滤」。
- 后端基线：见台账（预期与 M69 一致：3 条真资源既有失败）；`agent_smoke.py` 基线 `SMOKE OK`。
- 本里程碑不新增迁移、不改 v2 SSE/事件；`legacy_chat` 系统提示必须逐字节不变；`agent_eval --suite regression --mode replay` 必须继续 6/6。

## Global Constraints

- **v2 行为不变**：M65 golden parity `17 passed`；v2 会话接口/SSE/工具声明不变；六个旧代理定义行为不变；回归评测 6/6。
- **本里程碑不加迁移**：数据模型改动只在 M69 已建的表上写数据。
- **只加 spec 列的**：错误码 3 个、配置键 3 个、工具 7 个（总数 84→91、可见 83→90，REST 目录不含）、端点 9 条、WS 频道 `agent_run`、定义九份。
- **测试文件名全局唯一**（`tests/agent/` 无 `__init__.py`）。
- 不 push；提交 `<类型>(m70): <中文摘要>`；最后合并回 `dev` 打 `v3-m70`。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，命令行不加 `-q`；长输出重定向 `/tmp/m70/`。

## Review Focus

1. **主链路**：用户消息→总控收件箱→总控运行→`create_ticket`→Worker 运行→`submit_pipeline`→流水线终态→`FinishHookFanout`→话题→Worker 收件箱→Worker 唤醒→`report`→总控转述；端到端连跑 3 次稳定。
2. **并发与唯一性**：总控单例会话语义（`current_session_id`）；Worker 并发闸与 SessionPump 的合并/优先级/中断宽限；同一会话至多一个活动运行。
3. **委托与风险**：`delegate` 的名单/深度/并行/降级/随父中断；每个工具调用先过委托 `risk_ceiling`，越界返回 `MANDATE_SCOPE_EXCEEDED` 且不弹确认。
4. **v2 隔离**：七个新工具 `rest_visible=False`，v2 会话工具声明不含它们；`legacy_chat` 系统提示逐字节不变；v2 路由按 kind 过滤后行为不变。
5. **装配与关闭**：lifespan 第 8 步顺序、`finish_hooks` 两个订阅者、关闭序列与启动失败清理；SSE v3 的转发/关流/兜底与 WS 映射。

---

### Task 0: 分支、§0 前置检查与计划入库（已完成）

- [x] 建分支 `orchestrator-workers`，跑 §0 的 19 条检查；#12 的命中是既有名字、#16 无匹配记入台账。
- [x] 提交本计划：`docs(m70): M70 总控与 Worker 运行实施计划`。

### Task 1（G1）: 配置项与错误码 + FinishHookFanout（spec T1、T2）

**Files:** `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`maa_api/domain/errors.py`、`docs/v3/14`（§4）、`tests/test_settings.py`、`tests/domain/test_errors.py`、新建 `maa_api/agent/hooks_fanout.py`、`maa_api/main.py`（约 962 行与清理列表）、新建 `tests/agent/test_hooks_fanout.py`、`tests/services/test_upgrade_plans.py`、`tests/api/test_agent_assembly_wiring.py`

- [ ] 三个设置类/键/`_FIELDS`/模板/测试；三个错误码（403/403/409）与文档、计数；`FinishHookFanout` 与 main.py 接线（`app.state.finish_hooks.names == ("upgrade_plans",)`）。
- [ ] 验证：`pytest tests/test_settings.py tests/test_vapid_settings.py tests/domain/test_errors.py tests/agent/test_hooks_fanout.py tests/services/test_upgrade_plans.py tests/api/test_agent_assembly_wiring.py` → 0。
- [ ] 提交：`feat(m70): 总控/Worker 配置、错误码与终态钩子扇出`

### Task 2（G2）: 运行时接缝补齐（spec T3）

**Files:** `maa_api/agent/harness/*.py`、`maa_api/agent/tickets/*.py`、新建 `tests/agent/harness/test_m70_harness_seams.py`、`tests/agent/tickets/test_m70_inbox_seams.py`

- [ ] 按 §3.3 补 `CURRENT_RUN`/`require_run`、`RunEventBus.subscribe`、`run_start` 的 `consumed_inbox_ids`、`cancelled`、`message_persisted`、`AgentLoop` 接受运行行/初始 state/inbox_items/cancel_event、v3 SSE 事件名对齐；收件箱 `put_user_message`/`has_active_pending` 等；`ThreadRouter.deliver`；`TicketService.bind_orchestrator_resolver`。
- [ ] 验证：`pytest tests/agent` → 0（M65–M69 已有测试不变）；parity 17；agent_eval 6/6。
- [ ] 提交：`feat(m70): 总控与 Worker 的运行时接缝`

### Task 3（G3）: 共用约定、角色卡与系统提示分层（spec T4）

**Files:** 新建 `maa_api/agent/specs/common.md`、`maa_api/agent/specs/registry.py`、新建 `maa_api/agent/workers/{__init__,cards}.py`、`maa_api/agent/harness/builtin_middleware.py`、新建 `tests/agent/workers/test_cards.py`

- [ ] `common.md` 与注册表 `common_prompt`（只扫 `builtin/*.md`）；`cards.py`；`ContextLayoutMiddleware` 按 §4.5.10 插段。
- [ ] 验证：`pytest tests/agent/workers/test_cards.py tests/agent/harness` → 0；`legacy_chat` 系统提示逐字节不变。
- [ ] 提交：`feat(m70): 共用约定、角色卡与系统提示分层`

### Task 4（G4）: 六份代理定义与 schema + 七个新工具（spec T5、T6）

**Files:** `maa_api/agent/specs/builtin/` 六份新定义、`maa_api/agent/llm/schemas/` 三个文件、新建 `maa_api/agent/tools/{report,delegate,tickets}.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/runtime.py`、`tests/agent/test_tool_catalog.py`、新建 `tests/agent/tools/test_m70_tools.py`、`tests/agent/specs/test_m70_specs.py`

- [ ] 按 §4.7 写定义与 schema、按 §4.5.4/§4.5.11 写七个工具（`rest_visible=False`）；注册顺序；工具数 91/90；v2 门面隔离；T5 验证在工具注册后跑。
- [ ] 验证：`pytest tests/agent/test_tool_catalog.py tests/agent/tools/test_m70_tools.py tests/agent/specs/test_m70_specs.py tests/agent/test_runtime.py` → 0。
- [ ] 提交：`feat(m70): 六份代理定义、schema 与七个工具`

### Task 5（G5）: 三个中间件与设备门（spec T7）

**Files:** 新建 `maa_api/agent/workers/{middleware,device_gate}.py`、链装配处、新建 `tests/agent/workers/{test_worker_middleware,test_device_gate}.py`

- [ ] 按 §4.5.5/§4.5.6/§4.5.11 实现并按 05 §5.2 序号（14/16/18）入链（总控链没有它们）。
- [ ] 验证：`pytest tests/agent/workers` → 0。
- [ ] 提交：`feat(m70): Worker 中间件与设备门`

### Task 6（G6）: SessionPump 与并发闸（spec T8）

**Files:** 新建 `maa_api/agent/workers/pump.py`、新建 `tests/agent/workers/test_session_pump.py`

- [ ] 按 §4.2.2/§4.5.1 实现（合并窗口、闸、优先级、中断宽限、退避、close）。
- [ ] 验证：`pytest tests/agent/workers/test_session_pump.py` → 0。
- [ ] 提交：`feat(m70): SessionPump 与并发闸`

### Task 7（G7）: WorkerService 与 TicketTitler（spec T9）

**Files:** 新建 `maa_api/agent/workers/service.py`、`titler.py`、新建两个测试

- [ ] 按 §4.5.3/§4.5.7/§4.7.7 实现（Worker 运行生命周期、汇报处理、`ticket_status` 广播补缺、标题生成）。
- [ ] 验证：`pytest tests/agent/workers/test_worker_service.py tests/agent/workers/test_ticket_titler.py` → 0。
- [ ] 提交：`feat(m70): WorkerService 与工单标题`

### Task 8（G8）: DelegationService（spec T10）

**Files:** 新建 `maa_api/agent/workers/delegation.py`、`tests/agent/workers/test_delegation_service.py`

- [ ] 按 §4.5.4 实现（并行/串行/深度/名单/降级/随父中断）。
- [ ] 验证：`pytest tests/agent/workers/test_delegation_service.py` → 0。
- [ ] 提交：`feat(m70): 子代理委派服务`

### Task 9（G9）: OrchestratorService（spec T11）

**Files:** 新建 `maa_api/agent/orchestrator/{__init__,service}.py`、`tests/agent/orchestrator/test_orchestrator_service.py`

- [ ] 按 §4.5.2 实现（单一会话、批处理窗口、提问让位、重置、启动扫描）。
- [ ] 验证：`pytest tests/agent/orchestrator/test_orchestrator_service.py` → 0。
- [ ] 提交：`feat(m70): 总控服务`

### Task 10（G10）: SSE v3 流与 WS 广播（spec T12）

**Files:** 新建 `maa_api/agent/orchestrator/stream.py`、`maa_api/api/ws.py`、前端 `events.ts`/`query-bridge.ts`/`query-bridge.test.ts`、新建 `tests/agent/orchestrator/test_run_event_stream.py`

- [ ] 按 §4.3.2/§4.3.3/§4.5.8/§4.6 实现；前端 `agent_run` 不失效查询的用例。
- [ ] 验证：`pytest tests/agent/orchestrator/test_run_event_stream.py` → 0；`pnpm typecheck && pnpm test --run` → 0。
- [ ] 提交：`feat(m70): 运行事件流与 WS 广播`

### Task 11（G11）: 路由与装配（spec T13）

**Files:** 新建 `maa_api/api/routers/orchestrator.py`、`tickets.py`、`maa_api/api/schemas/agent_v3.py`、`maa_api/api/routers/agent.py`、`maa_api/main.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`（§6）、`docs/v3/15`、三个测试文件

- [ ] 九条端点；lifespan 第 8 步装配六个对象与监听者/广播器；关闭序列；v2 路由按 kind 过滤（§0 #16 无匹配，完整补）；快照/类型/清单/指南重生成。
- [ ] 验证：`dump_openapi --check` 0；`pnpm gen:api:check` 0；`pytest tests/api tests/domain` → 0。
- [ ] 提交：`feat(m70): 总控与工单端点、装配与关闭`

### Task 12（G12）: 端到端测试与测试设施（spec T14）

**Files:** 新建 `tests/fakes/{scripted_llm,lifespan}.py`、`scripts/agent_smoke.py`、新建 `tests/api/test_orchestrator_e2e.py`

- [ ] 搬假 lifespan、`ScriptedLLM`（Hold/route/install_llm）、七个端到端用例；e2e 连跑 3 次全过；agent_smoke 仍 `SMOKE OK`。
- [ ] 验证：e2e `for i in 1 2 3` 三个 0；`agent_smoke.py` 末行 `SMOKE OK`。
- [ ] 提交：`feat(m70): 总控与 Worker 端到端测试设施`

### Task 13（T15）: 文档同步、全量验证与合并（spec T15）

**Files:** §9 列出的文档、新建 `docs/v3/ledgers/m70-orchestrator-workers.md`、`docs/v3/README.md`、`docs/v3/13`（§8 补条）

- [ ] 按 §9 改文档；13 §8 记偏离；台账（每任务证据、Rulings、手动检查未验证项）；README 状态行。
- [ ] 验证：spec §7 全部（pytest 不新增失败、`dump_openapi --check` 0、前端五步 0、两个冒烟 `SMOKE OK`、agent_eval `EVAL OK`、断链 0、`git diff --check` 0）。
- [ ] 提交：`docs(m70): 契约同步、台账与状态行`；然后 `git merge --no-ff orchestrator-workers -m "merge: 总控与 Worker 运行（M70，见 docs/v3/13 §8.x）"` → `git tag v3-m70` → `git branch -d orchestrator-workers` → 合并后复验与汇报。
