# M75 工作记忆、外置与计划模式 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 超长工具输出整段外置为 `artifact` 并在上下文只留指针（`spilled` 指针 + `read_artifact` 按偏移续读）；Worker 用 `write_note` 记中间结论（`durable.notes` 尾部列出）；多步工作走 `update_plan` 计划（达到阈值时待用户批准，批准前只能调用只读工具），计划与笔记进动态尾部；HTTP 产物/计划端点、保留任务、前端 `PlanPanel`/`ArtifactList` 与事件失效；迁移 `0029_artifacts_plans`。

**Architecture:** G1 迁移/模型/枚举/错误码 → G2 产物服务+Spill 中间件+`read_artifact`/`write_note` → G3 计划服务+Plan 中间件+`update_plan`+计划端点 → G4 产物端点+尾部 plan/notes 段+保留 → G5 前端+7 份定义+common.md+工具目录 → G6（我：计数、文档、全量验收、合并 tag）。

**Spec:** [m75-workspace-plan](../specs/m75-workspace-plan.md)（287 行）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `workspace-plan`，基点 `dev` = `34ffd3f`（M74 合并）；tag `v3-m74`；迁移 head `0028_compaction`，下一个空号 `0029`。
- 基线：`tests/agent` = `1089 passed, 1 skipped, 2 xfailed`（退出码 0）。
- **规格 vs 实际布局的裁决（记 13 §8.65–8.72）**：
  1. 路径改写：模型加到单文件 `maa_api/db/models.py`；中间件放 `maa_api/agent/harness/{spill,plan}.py`；服务 `maa_api/agent/{artifacts,plans}/service.py`；工具 `maa_api/agent/tools/workspace.py`；路由 `maa_api/api/routers/{artifacts,plans}.py`；schema 放 `maa_api/api/schemas/agent_v3.py`；前端接入点 `web/src/features/tickets/TicketDetailPage.tsx`（无 `TicketPage.tsx`）。
  2. 外置要同时替换 `ToolOutcome.content` 与 `.result`（SSE `tool_result` 发 `result`，只改 content 前端仍收全文）；审计由内层 Policy/toolexec 在替换前记录，天然保全全文。
  3. 链位：`PlanMiddleware` 与 `SpillMiddleware` 插在 `tool_result_clearing`（8）之后、`loop_guard` 之前，实际为第 9、10 位；同步 `tests/agent/workers/test_worker_middleware.py` 的三份链序断言。
  4. 钩子名：用现有 `before_model`/`on_run_end`（无 `wrap_model`/`after_agent`）。`on_run_end` 逆序 → `ReportMiddleware.ensure_report` 先跑：让 `PlanMiddleware` 在待批准时把 `human_action` 写进 `ctx.state["plan.pending_human_action"]`，`ensure_report` 检测到它时改发 waiting 汇报（只发一条，不重复）。
  5. 保留并入现有 `RetentionService.run_retention`（加 `artifacts_deleted` 计数与清理逻辑），不另起 `maa-artifact-retention` 调度 job；文件存储根用现有 `resource/` 推导方式。
  6. 配置：`context.spill_threshold_chars` 加进 `ContextSettings`；新建 `PlanSettings`（`complex_min_steps`/`approval_mode`）加到 `Settings`、`SETTING_KEYS`、`_FIELDS`（group Agent）、模板、`ENV_OVERRIDES`。
  7. 错误码：新增 4 个（`ARTIFACT_NOT_FOUND` 404、`ARTIFACT_TOO_LARGE` 413、`PLAN_NOT_FOUND` 404、`PLAN_NOT_AWAITING_APPROVAL` 409）→ 计数 142/141→146/145；`PLAN_APPROVAL_REQUIRED` 只作工具结果字符串，不进 `ErrorCode`。
  8. 工具：三个新工具（`update_plan`/`write_note`/`read_artifact`，group `workspace`、SAFE、`rest_visible=false`）→ 总数 93→96、模型可见 92→95；7 个 Worker（general 与六个专职）在 `report` 前追加三名并 version+1；总控与四个子代理**不动**（T5 写范围的「四个子代理」按 §4.7 非目标忽略）。
  9. 计数：端点 146→**152**（+6）、业务表 38→**41**（+2 + M74 后为 40？以实际 `BUSINESS_TABLES` 为准 +2）、`run_plan` 唯一索引名 `uq_run_plan_session_id_active`。

## Global Constraints

- 不加 03 没有的表/列/索引；`chart`/`table`/`form`/`dashboard` 本里程碑不写入。
- 总控没有 `update_plan`/`write_note`/`read_artifact`；子代理没有 `read_artifact`（其工具输出不外置）。
- 工具函数本身仍返回完整结果；外置只发生在中间件。
- 外置前后审计必须留全文（顺序由测试锁死）。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m75/`。
- 不 push；提交 `<类型>(m75): <中文摘要>`；最后合并回 `dev` 打 `v3-m75`。

## Review Focus

1. **外置**：长输出（> 阈值）→ 指针（`spilled/artifact_id/tool/total_chars/preview/next_offset/hint`），上下文与 SSE 只有指针、审计全文；短输出/不外置名单/无 `read_artifact` 的定义不外置；`preview` 前 3000 + 后 1000；审计顺序。
2. **产物存储**：≤256 KiB 存 DB、>256 KiB 写 `resource/artifacts/<id>`（先 `.part` 再 `os.replace`）、>16 MiB 413；`read_artifact` 偏移/上限/next_offset/工单范围；`write_note` 新建/更新/20000 字限制；保留只删 30 天未置顶 spill 与文件。
3. **计划状态机**：4 步待批准、3 步直接 active、只改状态不重审、改标题重审、全部完成 done、工单结束 abandoned、同刻最多一项 in_progress；批准前工具过滤在策略之前（`PLAN_APPROVAL_REQUIRED`、不弹确认）；待批准自动 waiting 汇报（只一条）；approve 写 Worker 收件箱并唤醒。
4. **尾部**：`plan`/`notes` 段（无则不插）与 06 §7.2 文本；`durable.notes` 只列 `{id,title}`。
5. **接口**：6 端点 operationId 与 400/404/409/413、分页形状；`PLAN_NOT_*`、`ARTIFACT_*` 错误码。
6. **前端**：PlanPanel 批准/退回（请求中禁用、退回原因非空）、ArtifactList 抽屉与截断下载、`plan_updated`/`artifact_created` 失效。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `workspace-plan`，跑 §0 七条；19 处差异裁决写入本计划；提交计划。

### Task 1（G1）: 迁移、模型、枚举与错误码（spec T1 + §9 错误码部分）

**Files:** `maa_api/db/models.py`（`Artifact`/`RunPlan`）、`maa_api/domain/enums.py`、`maa_api/domain/errors.py`、新建 `maa_api/db/migrations/versions/0029_artifacts_plans.py`、`tests/db/test_models.py`、`tests/db/test_initial_migration.py`、`tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`docs/v3/14`（§4）

- [ ] 四个枚举 + `EXPECTED_VALUES`；`Artifact`/`RunPlan` 逐列照 03 §8.2/§8.3（含两个索引与 `uq_run_plan_session_id_active` 部分唯一）；迁移 `0029` 可逆；`BUSINESS_TABLES`/索引/外键/文本/JSON 列登记表。
- [ ] 四个错误码 + 绑定 + 14 清单 4 行与计数（142/141→146/145）。
- [ ] 验证：`pytest tests/db tests/domain` → 0。
- [ ] 提交：`feat(m75): 产物与计划表、枚举和错误码`

### Task 2（G2）: 产物服务、外置与工具（spec T2）

**Files:** 新建 `maa_api/agent/artifacts/service.py`、新建 `maa_api/agent/harness/spill.py`、`maa_api/agent/tools/workspace.py`（`read_artifact`/`write_note`）、`maa_api/agent/tools/__init__.py`、`maa_api/agent/runtime.py`（链位 9/10 之一：Spill）、`tests/agent/test_spill.py`、`tests/agent/test_artifacts.py`、`tests/agent/test_tool_catalog.py`（工具计数 96/95 与 `EXPECTED_TOOLS` +3）、`tests/agent/workers/test_worker_middleware.py`（链序）

- [ ] `ArtifactService.put/get/list/download/pin/update_note/retention_cleanup`（存储阈值、`.part`+`os.replace`、413、sha256）；`SpillMiddleware.wrap_tool`（不外置名单/无 `read_artifact` 不外置/阈值/preview/同时替换 content 与 result）；`read_artifact`/`write_note` 工具（`note_too_long`、`ARTIFACT_NOT_FOUND`、工单范围、分页）；`ctx.state["durable"].notes` 更新路径。
- [ ] 验证：`pytest tests/agent/test_spill.py tests/agent/test_artifacts.py tests/agent/test_tool_catalog.py tests/agent/workers/test_worker_middleware.py` → 0；parity 17。
- [ ] 提交：`feat(m75): 产物服务、工具结果外置与续读工具`

### Task 3（G3）: 计划服务、中间件与计划端点（spec T3 + plans 端点）

**Files:** 新建 `maa_api/agent/plans/service.py`、新建 `maa_api/agent/harness/plan.py`、`maa_api/agent/tools/workspace.py`（`update_plan`）、`maa_api/agent/workers/middleware.py`（`ensure_report` 检测 `plan.pending_human_action`，只发一条汇报）、`maa_api/agent/runtime.py`（链位 10/9 之另一）、新建 `maa_api/api/routers/plans.py`、`maa_api/main.py`（include_router + `app.state.plan_service`）、`tests/agent/test_plans.py`、`tests/api/test_agent_plans_router.py`、`tests/agent/workers/test_worker_middleware.py`（链序）

- [ ] `PlanService`（create/update/list_active/approve/reject/abandon/`is_active`）；状态机逐条；`PlanMiddleware`（wrap_tool 批准前过滤在策略之前、`human_action` 进工具结果、`before_model` 尾部 plan 段）；`update_plan` 工具；approve 端点写 Worker `system` 收件箱并唤醒；`ensure_report` 待批准时发 waiting 汇报（human_action=decision）。
- [ ] 验证：`pytest tests/agent/test_plans.py tests/api/test_agent_plans_router.py` → 0；parity 17。
- [ ] 提交：`feat(m75): 计划状态机、批准前工具过滤与计划端点`

### Task 4（G4）: 产物端点、尾部 notes 段与保留（spec T4）

**Files:** 新建 `maa_api/api/routers/artifacts.py`、`maa_api/api/schemas/agent_v3.py`（`ArtifactOut` 等）、`maa_api/agent/context/durable.py`（plan/notes 尾部段）、`maa_api/agent/context/layout.py`（`TAIL_SEGMENT_ORDER` 加 `notes`）、`maa_api/services/retention_service.py`（spill 清理）、`maa_api/main.py`（include_router/装配）、`tests/api/test_agent_workspace.py`、`tests/agent/test_dynamic_tail.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`（§6 +6 行与计数）

- [ ] 四个产物端点（列表/元数据 64 KiB 截断/下载/pin）；尾部 `plan` 与 `notes` 段（worker 也插）；保留并入 `run_retention`（`artifacts_deleted`）；快照/类型/清单。
- [ ] 验证：`pytest tests/api/test_agent_workspace.py tests/agent/test_dynamic_tail.py` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m75): 产物端点、动态尾部与保留`

### Task 5（G5）: 前端、定义与工具目录（spec T5）

**Files:** 新建 `web/src/features/tickets/{PlanPanel,ArtifactList}.tsx` 与测试、`TicketDetailPage.tsx`（侧栏接入）、`web/src/realtime/{events.ts,query-bridge.ts}` 与测试、`web/src/api/keys.ts`、`maa_api/agent/specs/common.md`、7 份 Worker 定义（`general`/`daily_ops`/`copilot`/`planner`/`diagnose`/`analyst`/`ops`）、`tests/agent/test_builtin_workers.py`、`tests/agent/test_tool_catalog.py`

- [ ] PlanPanel（标题/状态/步骤；待批准批准/退回，请求中禁用、退回原因非空）、ArtifactList（列表/抽屉/正文/截断下载）；`plan_updated`/`artifact_created` 失效；三个新工具接到 7 个 Worker（`report` 前）且 version+1；`common.md` 三句；工具目录 96/95 与定义测试同步。
- [ ] 验证：`pytest tests/agent/test_builtin_workers.py tests/agent/test_tool_catalog.py` → 0；`cd web && pnpm typecheck && pnpm test --run src/features/tickets` → 0。
- [ ] 提交：`feat(m75): 计划与产物前端、Worker 工具与共用约定`

### Task 6（G6，我）: 计数、文档、全量验收与合并（spec T6/§9）

**Files:** `tests/domain/test_errors.py`、`tests/domain/test_route_inventory.py`、`tests/agent/test_tool_catalog.py`（复核）、`docs/v3/{02,03,04,05,13,14}`、`docs/v3/ledgers/m75-workspace-plan.md`、`docs/v3/README.md`、冒烟与定义清单（`scripts/agent_smoke.py` 等 16→16 不变，若定义数不变则不动）

- [ ] 复核计数（错误码 146/145、端点 152、表 40→41? 以实际为准、工具 96/95、定义 16）；文档同步（03 §8.2/§8.3/§14、04 M75 行、05 §3.2/§5.2/§6、02 §4.1/§5、14）；13 §8.65–8.72；README；台账。
- [ ] 跑 §7：全量 pytest 不新增失败、`dump_openapi`/`gen:api`/指南、前端五步、三个冒烟 `SMOKE OK`、regression/capability 不降、断链 0、`diff --check`；§7 的三条脚本化验收（20000 字外置、批准前失败批准后成功、gen:api:check）。
- [ ] 提交 `docs(m75): 台账、计数与状态行`；`git merge --no-ff workspace-plan` → tag `v3-m75` → 删分支 → 合并后复验 → 汇报。
