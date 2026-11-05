# M83 Watchdog 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确定性巡检 + 少量语义检查 + 有限自愈：表 `watchdog_alert`；08 §6.1 六项检查（`run_heartbeat_stale`/`ticket_stale`/`ticket_churn`/`pipeline_no_callback`/`core_not_ready`/`queue_stuck`）与 §6.2 动作；`mode=record` 只记录、`auto` 才动作；critical 进待你处理/总控收件箱/WS/Web Push；语义检查只对 sticky 工单（每轮≤2、每张每小时≤1）；`/more/watchdog` 值守页。

**Architecture:** G1 表/迁移/记录器/确定性检查（T1）→ G2 服务/动作/周期任务/回调记录点/保留（T2）→ G3 语义检查/定义/schema（T3）→ G4 端点/错误码/计数（T4 后端）→ G5 前端页面/待你处理/失效（T4 前端）→ G6（我：文档、全量验收、合并 tag）。

**Spec:** [m83-watchdog](../specs/m83-watchdog.md)（289 行）

## 开工基线（2026-11-05）

- 分支 `watchdog`，基点 `dev` = `0e9c447`（M82 合并）；tag `v3-m82`；迁移 head `0035_goals`，下一号 `0036`；§0 1–6、8 符合（调度器 `add_job`、`PipelineRunner.request_cancel`、`CoreClient.on`、流水线 runner 基线 42 passed）；#7 的 `services/device/` 目录不存在（实为 `device_service.py`，无 `reconnect()`），本里程碑非目标不实现设备离线/重连，按「含义可满足」处理。
- 全量基线：预期 3 条真资源失败（M82 后 5051 passed）。
- **接缝裁决（记 13 §8.143–8.152）**：
  1. **`interrupt_run` 无按 run 的入口**：现有只有 `WorkerService.interrupt(ticket_id)`/`OrchestratorService.interrupt()`。实际：从 `AgentRun.session_id` 反查会话类型与 `ticket_id`，Worker 会话调 `WorkerService.interrupt(ticket_id)`、总控调 `OrchestratorService.interrupt()`；30 秒后仍 `running` 则把运行 `status='interrupted'`（经运行仓储）。不改 M70 公开契约。
  2. **语义检查执行路径**：spec 要 `agent_run`（`kind=watchdog_check`、`session_id` 空、代理 `watchdog`）但 `run_agent` 需要会话与运行。实际：照 M81 `TriggerEvaluator` 的做法——建 `agent_run` 行（`session_id=None`、`agent_name='watchdog'`、`spec_version` 注册表哈希）后用 `complete_structured`（角色 `worker`、schema `watchdog_findings`），输入是服务侧整理的工单快照卡；定义里的工具清单保留（05 §3.4），但单次结构化调用不实际执行工具。
  3. **`watchdog_findings` schema**：spec 文件清单漏了 schema 文件；实际新增 `maa_api/agent/llm/schemas/watchdog_findings.py` 并注册（照 M80/M81 惯例）。
  4. **去重语义**：spec 的「后一次检查只更新 evidence 与 created_at 不改」自相矛盾；实际：`created_at` 不改（保留首次时间），最新证据写 `evidence["latest"]`。
  5. **保留策略**：03 §14 要求 `watchdog_alert` 保留 90 天，spec §9 未提；实际在 `run_retention` 加 90 天清理，返回键新增 `watchdog_alerts_deleted`（既有键不变）。
  6. **前端落点**：`api/agent/watchdog.py` → `maa_api/api/routers/watchdog.py`；`web/src/features/more/WatchdogPage.tsx` → `web/src/features/watchdog/WatchdogPage.tsx`（仓库无 `features/more/`）；路由 `/more/watchdog`。
  7. **待你处理口径**：页面列全部未解除告警；「待你处理」只加 `severity=critical` 的分支（11 §11），前端 `attention.ts` 新增 `watchdog_alert` 来源（纯前端，无后端来源枚举）。
  8. **调度**：`maa-watchdog` 用 `IntervalTrigger(seconds=60)`（仓库现有 job 用 Cron/Date/Interval 皆有），任务内按 `watchdog.interval_seconds` 自闸。
  9. **计数**：错误码 162/161→**163/162**（`WATCHDOG_ALERT_NOT_FOUND` 404）；端点 176→**179**（agent 73→76）；业务表 51→**52**；代理定义 18→**19**（`watchdog`）；配置键 +6（`watchdog.*`）；工具/规则数不变；迁移 head **0036**。
  10. **迁移测试**：仓库惯例补 `tests/db/test_watchdog_migration.py`（spec §6 未列）。

## Global Constraints

- 检查函数是纯函数：输入快照、输出告警草稿，不读全局、不调模型、不碰设备；条件消失自动 `resolved_at`。
- `mode=record` 只写告警（`action_status=recorded`）、绝不执行动作；`auto` 才动作；`allow_core_restart=false` 时 `restart_core` 恒 skipped；不新建 `diagnose` 工单；不碰租约过期/设备离线。
- 记录器是进程内字典，重启清空；「当前任务开始后还没有 CALLBACK」→ `evidence.reason=no_sample_yet` 不告警；有回调则不告警。
- 语义检查仅本轮被 `ticket_stale`/`ticket_churn` 挑中的工单，每轮≤2、每张每小时≤1；模型失败跳过不写告警。
- critical 新插入：WS `watchdog_alert`、总控收件箱 `system`（message + alert id）、待你处理 critical 分支、无可见页面时 `WebPushService.broadcast`；warn/info 只进列表。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m83/`。
- 不 push；提交 `<类型>(m83): <中文摘要>`；最后合并回 `dev` 打 `v3-m83`。

## Review Focus

1. **表/迁移/记录器**：列/索引/自增；迁移可逆/离线；记录器接口（core callback/pipeline 开始结束进度）与 `no_sample_yet`。
2. **检查**：六项条件逐条对照 08 §6.1；心跳用 `heartbeat_at` 不用 `updated_at`；ticket 条件（无活动运行/无待处理/订阅流水线空闲/最近活动早于阈值）；churn 60min/12 轮/全 progress；pipeline_no_callback 有任务+超时无 CALLBACK；core 状态持续/failed；queue 条件（可领取>10min/未暂停/内核就绪/设备连接/无租约/执行器空闲）；条件消失 resolve；同目标不重复插入、evidence.latest 更新。
3. **动作/周期**：record 不动；auto 的 4 类动作语义与失败处理；interval 自闸；last_run_at 查询与端点；critical 通知四条；无可见客户端才推送。
4. **语义**：只 sticky；≤2/轮、≤1/小时/工单；FakeLLM 输出 schema；`mark_blocked` 仅在 auto 且同轮被确定性检查挑中；不建 diagnose；模型失败跳过。
5. **端点/前端**：`GET watchdog`/`alerts`/`resolve`；分页 04 §1；404 错误码；页面 mode/last_run_at/未解除告警/解除/空态；`watchdog_alert` 失效；待你处理 critical 卡。
6. **定义/计数/文档**：watchdog 定义 frontmatter/正文逐字、18→19；错误码/端点/表计数；14 号文档；08 检查项与 `checks.py` 函数名台账对照。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `watchdog`；§0 1–8（#7 目录名不符但非目标）；10 项接缝裁决；提交计划。

### Task 1（G1）: 迁移、记录器、确定性检查（spec T1）

**Files:** `domain/enums.py`、`tests/domain/test_enums.py`、`db/models.py`、新建 `migrations/versions/0036_watchdog.py`、`tests/db/test_models.py`、新建 `tests/db/test_watchdog_migration.py`、新建 `agent/watchdog/{__init__,recorder,checks}.py`、新建 `tests/agent/test_watchdog_checks.py`

- [ ] 三枚举；`watchdog_alert` 表（自增 PK/索引/JSON evidence）；迁移 0036（可逆/离线）；`WatchdogRecorder`（core callback、pipeline 开始/结束/进度、`no_sample_yet` 判定所需状态）；`checks.py` 六项纯函数 + 快照类型（`Draft`/`Snapshot`）+ 条件消失 resolve 逻辑（服务层调用，可放 checks 辅助）。
- [ ] 验证：`pytest tests/agent/test_watchdog_checks.py tests/db/test_models.py tests/domain/test_enums.py` → 0。
- [ ] 提交：`feat(m83): 值守表、记录器与确定性检查`

### Task 2（G2）: 服务/动作/周期/记录点/保留（spec T2）

**Files:** 新建 `agent/watchdog/service.py`、`main.py`（`maa-watchdog` job 与装配/关闭）、`core/client.py`（callback 记录点）、`services/pipeline_runner.py`（任务开始/结束/进度记录点）、`services/retention_service.py`（90 天清理 + 新键）、新建 `tests/agent/test_watchdog_service.py`

- [ ] `WatchdogService`（run_once/snapshot/interval 自闸/upsert/resolve/apply_action 四类：中断运行、blocked 汇报、request_cancel、restart_core、wake_queue/失败处理）；main 注册 60 秒 job、装配 `app.state.watchdog_service`、关闭清理；回调记录点；retention。
- [ ] 验证：`pytest tests/agent/test_watchdog_service.py tests/services/test_pipeline_runner.py tests/services/test_retention_service.py` → 0。
- [ ] 提交：`feat(m83): 值守服务、动作与周期任务`

### Task 3（G3）: 语义检查与定义（spec T3）

**Files:** 新建 `agent/specs/builtin/watchdog.md`、新建 `llm/schemas/watchdog_findings.py`、`llm/schemas/__init__.py`、`agent/watchdog/service.py`（语义分支）、新建 `tests/agent/test_watchdog_semantic.py`、`tests/agent/specs/*`、`tests/api/test_agent_specs_router.py`、`scripts/agent_smoke.py`（定义数 18→19 期望）

- [ ] 定义 frontmatter/正文逐字；schema 注册；语义检查（建 `watchdog_check` 运行、`complete_structured`、FakeLLM 可注入、≤2/≤1h、`mark_blocked` 条件、失败跳过）；定义名单/版本期望。
- [ ] 验证：`pytest tests/agent/test_watchdog_semantic.py tests/agent/specs tests/api/test_agent_specs_router.py` → 0；`agent_smoke` SMOKE OK。
- [ ] 提交：`feat(m83): 值守语义检查与代理定义`

### Task 4（G4）: 端点、错误码与计数（spec T4 后端）

**Files:** `domain/errors.py`、`tests/domain/test_errors.py`、新建 `api/routers/watchdog.py`、`main.py`（include_router）、新建 `tests/api/test_watchdog.py`、`docs/v3/14`（§4 一码、§6 agent 三行、计数 163/179）、`web/openapi.json`、`web/src/types/api.d.ts`、`tests/domain/test_route_inventory.py`

- [ ] `WATCHDOG_ALERT_NOT_FOUND` 404；三端点（概况/告警分页/resolve）；14 号文档与计数；OpenAPI/类型。
- [ ] 验证：`pytest tests/api/test_watchdog.py tests/domain` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m83): 值守端点与错误码`

### Task 5（G5）: 前端（spec T4 前端）

**Files:** 新建 `web/src/features/watchdog/{api,queries,WatchdogPage,watchdog.test}.*`、`routes/router.tsx`、`nav.ts`、`api/keys.ts`、`realtime/{events,query-bridge}.ts`、`features/console/{attention,use-attention,AttentionList}.tsx`、`console/test-utils.tsx`

- [ ] 值守页（mode/last_run_at/未解除告警/解除/空态）；路由/导航；`watchdog_alert` 失效；待你处理 critical 来源与告警卡（消息/对象/时间/标记已处理）。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` SMOKE OK。
- [ ] 提交：`feat(m83): 值守页与告警卡片`

### Task 6（G6，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,03,04,05,08,10,11,13,14,16,README,specs/接缝速查.md}`、新建 `docs/v3/ledgers/m83-watchdog.md`

- [ ] 02 §2.2/§4.1/§5 校验；03 §11.5/§13/§14；04 三端点/错误码/配置（已预写则校验）；05 §3.4 定义；08 §6 检查项与 `checks.py` 对照；10 指标（若有）；11 值守页/待你处理；13 §8.143–8.152；16 `watchdog.*` 键；README；台账（检查项对照表）。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、三冒烟、regression/capability、断链、diff；手动项记「未验证」。
- [ ] 提交 `docs(m83): 台账、契约同步与状态行`；`git merge --no-ff watchdog` → tag `v3-m83` → 删分支 → 合并后复验 → 汇报。
