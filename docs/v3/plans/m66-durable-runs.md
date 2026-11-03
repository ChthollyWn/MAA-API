# M66 运行树与持久化运行 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建 `agent_run` 运行树（迁移 `0023_agent_runs`），把会话锁、中断状态、运行心跳落库；服务重启后未完成的运行标为 `interrupted` 并给会话补一条恢复提示；新增运行查询端点；v2 会话与自动诊断的行为与 SSE 不变（M65 行为对齐 golden 必须继续绿）。

**Architecture:** T1 枚举/模型/迁移 → T2 错误码与配置项 → T3 仓储与 `RunService`（+`recovery.py` 骨架）→ T4 启动恢复、`main.py` 装配与 `build_model_context` 的 system 行映射 → T5 门面接入（锁/中断改按 `run_id`、运行由发起方开始与结束、消息带 `run_id`）→ T6 运行查询端点与快照/前端类型 → T7 冒烟与保留用例 → T8 文档、台账、全量验收与合并。

**Tech Stack:** SQLModel + Alembic（0023）、asyncio、pytest（内存库 + `StaticPool`）、FastAPI/Pydantic 响应模型、pnpm/Vite。

**Spec:** [m66-durable-runs](../specs/m66-durable-runs.md)（1671 行；每个任务照 spec 的对应小节做）

## 开工基线（2026-11-03，§0 已跑）

- 分支 `durable-runs`，基点 `dev` = `7d9e044`（M65 合并提交），工作区干净；tag `v3-m64`、`v3-m65`。
- `docs/v3/14-现行接口清单.md` 与 `scripts/check_doc_links.py` 在位，断链 `BROKEN 0`。
- M65 的 `AgentLoop`（`harness/loop.py:147`）与 `RunContext`（`harness/context.py:138`）在位；`RunContext` 已含 `run_id`/`root_run_id`/`parent_run_id`/`cancel_event`；`main.py` 有 `agent_spec_registry`。
- v2 门面的 12 个方法都在（`build_model_context` 在 `harness/builtin_middleware.py`）；`_run_turn_body` 已构造 `RunContext`（runtime.py:915）；`AgentMessage(` 出现在 runtime.py:1018 与 incidents.py:214/227。
- 迁移 head：`0022_skland_only_operators.py`；本里程碑要的名字（`AgentRun`、`agent_run`、`run_heartbeat_seconds`、`run_stale_seconds`、`RUN_NOT_FOUND`、`RUN_ALREADY_ACTIVE`）都不存在。
- 计数基线：错误码 125 / 124 / 20 节；HTTP 端点 123；`### 6.15 agent` 20 条；业务表 28、`EXPECTED_INDEXES` 31 等（spec §9 列了目标值）。
- 后端基线：`3 failed, 3973 passed, 5 skipped, 2 xfailed`（3 条真资源用例既有失败）；`agent_smoke.py` 末行 `SMOKE OK`。
- M65 的行为对齐 golden 在 `tests/agent/harness/golden/legacy_parity.json`：本里程碑不许重录；任何任务后 `test_harness_parity.py` 必须仍是 `17 passed`。

## Global Constraints

- **v2 行为不变**：`tests/agent/test_runtime.py`、`tests/agent/test_incidents.py`、`tests/agent/test_recon_delegate.py`、`tests/api/test_agent_router.py` 一个用例都不改、全部通过；SSE 事件名/字段/顺序不变。
- **golden 冻结**：不许 `MAA_REGENERATE_PARITY`、不许手改 `legacy_parity.json`；T4 的 `build_model_context` 改动只影响 `role='system'` 行（v2 历史没有这种行），golden 应保持不变。
- **迁移可逆**：`0023_agent_runs` 的 `downgrade()` 必须能回到 `0022_skland_only_operators`；运行数据随表删除，会话与消息完整保留；T1 在真实库副本上预演（补充检查）。
- 不改既有公共契约：不改 `AgentRuntime` 构造参数与公开方法签名（新增 `bind_run_service` 与关键字参数按 spec）；不改 v2 错误码语义（见裁决 A1）。
- 不新增 spec 之外的端点、字段、错误码、配置键；工具 84 / 模型可见 83 不变；OpenAPI tag 19 个不变。
- 不 push；提交信息 `<类型>(m66): <中文摘要>`；每个任务一个提交，最后按 spec T8 合并与打 tag。
- 一律 `.venv/bin/python`；本机没有 `rg`，用 `grep -rn`；长输出重定向到 `/tmp/maa_m66/`。

## Review Focus

1. **迁移与降级**：`agent_session`/`agent_message` 重建、`kind` 回填（`incident_key` 非空 → `incident`）、`agent_run` 外键与索引；T1 的 `upgrade → downgrade → upgrade` 用例与真实库副本预演。由 `tests/db/test_agent_runs_migration.py` 钉住。
2. **锁与中断语义**：锁改按 `run_id` 后不能出现「会话一直 409」；终态先写、占位后放；上一进程留下的过期运行在 `start` 冲突与 `acquire_session` 时被接管。由 `test_run_service.py`、`test_legacy_runs.py` 与既有 `test_runtime.py` 中断用例钉住。
3. **重启恢复顺序**：先补未配对的 tool 桩消息，再落一条 `role='system'` 的恢复提示；喂模型时转成 `【系统】…` 的 user 消息（`build_model_context` 新分支）。由 `test_run_recovery.py`、`test_build_model_context_maps_system_rows_to_prefixed_user` 钉住。
4. **运行归属**：一次 v2 会话轮次恰好一条 `agent_run`（`kind=legacy_turn`、`agent_name=legacy_chat`），自动诊断一条 `legacy_incident`；用户消息 `run_id` 为空；侦察与标题不记运行。由 `test_legacy_runs.py` 与冒烟新增检查钉住。
5. **计数与快照**：错误码 127 / 126、HTTP 端点 125、`### 6.15` 22 条、业务表 29、索引 36、tag 19 不变；OpenAPI 快照与 `api.d.ts` 重新生成。由 `test_errors.py`、`test_route_inventory.py`、`test_models.py`、三个 `--check` 钉住。

## 主 agent 裁决（§0.9 两条「停下来问」的先行裁决，T8 记 13 §8）

- **A1（0.9 第一条）**：`RUN_ALREADY_ACTIVE` 与 v2 的 `AGENT_SESSION_BUSY` **并存**。v3 的运行端点用前者（409）；v2 会话发消息端点继续返回 `AGENT_SESSION_BUSY`，不改语义。T2 在 `docs/v3/04-接口与事件协议.md` §5 补一句说明，T8 记 13 §8。
- **A2（0.9 第二条）**：运行生命周期归**发起方**：v2 门面（`_begin_legacy_run`/`_end_legacy_run`）与未来的 orchestrator 调用 `RunService.start`/`finish`，`AgentLoop` 不建也不结束运行。T8 同步 05 §2.2 的注释并记 13 §8；`RunContext` 不新增运行生命周期方法。

---

### Task 0: 分支、§0 前置检查与计划入库（已完成）

- [x] 建分支 `durable-runs`，跑 spec §0 的 11 条检查；0.9 两条已按上面 A1/A2 裁决。
- [x] 提交本计划：`docs(m66): M66 运行树与持久化运行实施计划`。

### Task 1: 枚举、模型与迁移（spec T1）

**Files:** `maa_api/domain/enums.py`、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0023_agent_runs.py`、`tests/db/test_models.py`、`tests/domain/test_enums.py`、新建 `tests/db/test_agent_runs_migration.py`

- [ ] 按 spec §4.1.1–§4.1.4 写枚举、`AgentRun` 模型、`AgentSession.kind` 与 `AgentMessage.run_id`、迁移（含 `upgrade`/`downgrade`）；改 `tests/db/test_models.py` 的计数常量与索引/外键/列宽清单（spec §9 给目标值）；写迁移用例（upgrade → downgrade → upgrade、可重复升级、降级到底再升级、真实库副本预演的条件跳过）。
- [ ] 验证：`pytest tests/db tests/domain/test_enums.py` → 0；真实库副本预演（`resource/maa_api.db` 存在时）输出 `fk []`、`integrity ('ok',)`、消息数不变、`incident` 数等于升级前 `incident_key` 非空数。
- [ ] 提交：`feat(m66): agent_run 模型与 0023 迁移`

### Task 2: 错误码与配置项（spec T2）

**Files:** `maa_api/domain/errors.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`（只动 §4）、`maa_api/settings.py`、`config.template.yaml`、`tests/test_settings.py`、`tests/test_vapid_settings.py`、`docs/v3/04-接口与事件协议.md`（§5 一句，A1）

- [ ] 加 `RUN_NOT_FOUND`、`RUN_ALREADY_ACTIVE`（含 404/409 绑定与注释）；`test_errors.py` 计数 127 / 126（`SECTION_COUNT` 不变），注释补 M66；`docs/v3/14` §4.11 两行、§4 开头总数 125 → 127；加 `agent.run_heartbeat_seconds`、`agent.run_stale_seconds` 两个配置键（`settings.py`、`config.template.yaml`、两个测试文件、`SETTING_KEYS`/config-only 集合）；04 §5 补 A1 一句。
- [ ] 验证：`pytest tests/domain/test_errors.py tests/test_settings.py tests/test_vapid_settings.py tests/test_api_guide_generator.py` → 0；`scripts/generate_api_guide.py && --check` → current；`git diff --stat docs/v3/15` 只有错误码两行。
- [ ] 提交：`feat(m66): 运行错误码与心跳/失效配置项`

### Task 3: 仓储与 RunService（spec T3）

**Files:** `maa_api/db/repositories/agent.py`、新建 `maa_api/agent/runs/{__init__,service,recovery}.py`、新建 `tests/agent/runs/test_run_service.py`

- [ ] 按 spec §4.2.1–§4.2.3 实现 `AgentRunRepository`（`ACTIVE_RUN_STATUSES`/`TERMINAL_RUN_STATUSES`、树查询）与 `RunService`（`start`、`heartbeat`、`set_status`、`finish`、`active_run`、`is_live`、`stale_after_seconds`、`list_runs`、`get`/`get_tree`、`close`、`RunNode`）；`recovery.py` 先放 `RunRecovery` 骨架与 `recover_run`。测试用内存库 `StaticPool` + 注入时钟/sleep。
- [ ] 验证：`pytest tests/agent/runs/test_run_service.py` → 0。
- [ ] 提交：`feat(m66): 运行仓储与 RunService`

### Task 4: 启动恢复、装配与模型上下文（spec T4）

**Files:** `maa_api/agent/runs/recovery.py`、`maa_api/main.py`、`build_model_context` 所在文件（`harness/builtin_middleware.py`）、新建 `tests/agent/runs/test_run_recovery.py`、新建 `tests/api/test_run_recovery_wiring.py`

- [ ] 补全 `recover`（启动时把活动运行标 `interrupted` / `finish_reason=recovered_after_restart`，补 tool 桩，落 `role='system'` 恢复提示）；`main.py` 装配 `RunService`/`RunRecovery` 并 `app.state.run_service`；`build_model_context` 加 `role == "system"` → `【系统】` user 行的分支。
- [ ] 验证：`pytest tests/agent/runs/test_run_recovery.py tests/api/test_run_recovery_wiring.py tests/api/test_app_skeleton.py tests/api/test_agent_assembly_wiring.py` → 0；`test_harness_parity.py` → `17 passed`。
- [ ] 提交：`feat(m66): 启动恢复与运行服务装配`

### Task 5: 门面接入（spec T5）

**Files:** `maa_api/agent/runtime.py`、`maa_api/agent/incidents.py`、必要时 M65 harness 文件、新建 `tests/agent/runs/test_legacy_runs.py`

- [ ] 按 spec §4.2.4 第 1–11 步：`bind_run_service`；`acquire_session` 改为按库里的活动运行加锁；`turn_running`/`interrupt_turn`/`_interrupted_now`/`_clear_interrupt` 改按 `run_id`；新增 `_begin_legacy_run`/`_end_legacy_run`/`_forget_run`/`_current_runs`/`_run_tasks`；`_run_turn` 去掉 `_active_turns`；`_produce_turn`/`run_incident` 先写终态再放占位、孤儿收尾；`_persist_message` 写 `run_id`；`incidents.py` 的会话加 `kind=INCIDENT`；门面构造的 `RunContext` 用本轮真实运行 id；harness 里落 `AgentMessage` 的地方补 `run_id`（用户消息除外）。
- [ ] 验证：`pytest tests/agent/runs tests/agent/test_runtime.py tests/agent/test_incidents.py tests/agent/test_recon_delegate.py tests/api/test_agent_router.py` → 0（v2 测试一个用例不改）；`grep -rn '_active_turns' maa_api` 无输出；`test_harness_parity.py` → `17 passed`。
- [ ] 提交：`feat(m66): 门面接入持久化运行`

### Task 6: 运行查询端点（spec T6）

**Files:** `maa_api/api/schemas/agent_v3.py`（及 `__init__.py`）、新建 `maa_api/api/routers/runs.py`、`maa_api/main.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`（只动 §6）、新建 `tests/api/test_runs_router.py`

- [ ] 按 spec §4.3.1–§4.3.2 写 `RunSummary`/`RunDetail`/`RunPage` 与 `run_summary`/`run_detail`、路由 `GET /api/agent/runs`、`GET /api/agent/runs/{run_id}`（`_run_service` 缺失 503、找不到 404 `RUN_NOT_FOUND`）；`docs/v3/14` §6.15 两行、（20 条）→（22 条）、端点 123 → 125；`dump_openapi.py` + `pnpm gen:api`。
- [ ] 验证：`pytest tests/api/test_runs_router.py tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py` → 0；`dump_openapi.py --check` 与 `pnpm gen:api:check` → 0；`grep -c '"operationId": "agent_\(list_runs\|get_run\)"' web/openapi.json` → 2。
- [ ] 提交：`feat(m66): 运行查询端点`

### Task 7: 冒烟与保留（spec T7）

**Files:** `scripts/agent_smoke.py`、`tests/services/test_retention_service.py`

- [ ] 冒烟里 `_check_builtin_conversation`/`_check_failure_incident` 返回会话 id，加 `_check_runs`（对话会话恰好 1 条 `legacy_chat` 运行、`status=succeeded`、`finish_reason=stop`、`steps=1`、tokens 与会话用量同源；详情 `children` 为空；诊断会话 1 条 `legacy_incident`），打印 `[ok] 运行记录：对话与自动诊断各一条已结束的运行`；保留用例 `test_retention_keeps_agent_runs`。
- [ ] 验证：`scripts/agent_smoke.py` → 0、末行 `SMOKE OK`；`pytest tests/services/test_retention_service.py` → 0。
- [ ] 提交：`test(m66): 冒烟与保留覆盖运行表`

### Task 8: 文档、台账、全量验收与合并（spec T8）

**Files:** `docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`、`docs/v3/05-运行时与代理定义.md`、`docs/v3/13-决策记录.md`、新建 `docs/v3/ledgers/m66-durable-runs.md`

- [ ] 按 spec §9 同步文档；13 §8 追加（运行生命周期归发起方 A2、重启恢复顺序、用户消息不写 run_id、`agent.run_stale_seconds` 用途、`finish_reason` 宽度例外、侦察与标题不记运行、`RUN_ALREADY_ACTIVE` 与 `AGENT_SESSION_BUSY` 并存 A1）；写台账；`docs/v3/README.md` 状态行改 M66。
- [ ] 验证：spec §7 全部（pytest 与基线比不新增失败、`dump_openapi --check` 0、前端五步 0、两个冒烟 `SMOKE OK`、指南 current、断链 0、`git diff --check` 0）；手动检查做不了的记「未验证」。
- [ ] 提交：`docs(m66): 契约同步、台账与状态行`；然后 `git merge --no-ff durable-runs -m "merge: 运行树与持久化运行（M66，见 docs/v3/13 §8.x）"` → `git tag v3-m66` → `git branch -d durable-runs` → 合并后复验与汇报。
