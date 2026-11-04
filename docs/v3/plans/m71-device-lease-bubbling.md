# M71 设备租约与确认冒泡 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 设备租约（`device_lease` 表 + 迁移 `0027`，容量 1 硬互斥、先到先得、TTL/续期/释放/过期/抢占/重启清理）、执行前回调取租约（确认通过之后、执行之前；等确认/提问不占设备）、`hold_device`/`release_device` 显式租约、队列领取闸门（有租约只领手动与持有者工单的流水线）、五类人工抢占、确认/提问带 `origin` 并冒泡到工单状态与总控收件箱、审计记 `run_id`/`ticket_id`/`agent_name`、子代理策略身份取父 Worker 会话、工单结束释放租约并撤销授权。

**Architecture:** G1 错误码/配置 + 模型/迁移 → G2 origin/审计/确认来源 → G3 DeviceLeaseService → G4 执行前回调与 LeaseMiddleware → G5 租约工具与通用 Worker 定义 → G6 冒泡与子代理策略身份 → G7 队列领取闸门 → G8 人工抢占与端点 → G9 前端 → G10 恢复/装配/文档/全量验收与合并（我）。

**Tech Stack:** SQLModel + Alembic（0027）、asyncio（Condition/事件）、pytest、FastAPI、React。

**Spec:** [m71-device-lease-bubbling](../specs/m71-device-lease-bubbling.md)（752 行）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `device-lease-bubbling`，基点 `dev` = `2eea5cc`（M70 合并）；tag `v3-m70`；迁移 head `0026_tickets`。
- 确认服务已有 `on_audit_prepare`（13 处）、无 `on_before_execute`；`ToolContext` 五字段无 `origin`；`device` 字段在 `ToolDefinition`；三个中间件与 `ClarificationMiddleware(` 构造处在位；`ToolOutcome` 是 dataclass；工单服务五个接口在位；`general.md` 在位；队列领取三处入口在位；`app.state` 装配顺序四行在位。
- §0 #10 **不符合**：M70 的 `MandateMiddleware` 直接比较 `decision.risk`，没有用 `09 §2.3` 的 `ceiling_risk` 规则（`grant_atomic_ops` 应按 consume 计）。**裁决**：这是 M70 的实现缺陷，M71 内修（G6 的 T7 第 4 步前先改 `MandateMiddleware` 用 `ceiling_risk(decision.risk, decision.confirmation_action)`），记 13 §8。
- §0 #12、#13 无匹配 → T7 第 4 步（子代理策略身份）与 T11（工单结束撤销授权）要做。
- §0 #16 的命中只有 M70 文件里的 M71 前瞻注释与 `__pycache__`，不是实现，忽略。
- **规格缺口裁决**（digest 读取代码核对后确认，记 13 §8）：
  1. M70 的 `DeviceGateMiddleware`/`DeviceGate`（`workers/middleware.py`/`workers/device_gate.py`、`app.state.device_gate`、`WorkerService._device_gate`、`pump.py`、`main.py` 装配与测试）在注释里写明「M71 用 DeviceLeaseService 与 LeaseMiddleware 替换并删除」，但 M71 spec 写范围没列。**裁决**：M71 删除旧设备门并改全部接线/测试，否则双层互斥且旧门无 TTL 会阻塞新闸门。
  2. 中间件链构造处在 `maa_api/agent/runtime.py`（`build_agent_chain` 与 v2 `_build_chain`），不是 spec 写的 `builtin_middleware.py`（类定义）。
  3. §0 #16 的命中是注释，不是实现；§0 #7 两条 `ClarificationMiddleware(` 只改 v3 链。
- 基线：全量 pytest 与 capability replay 结果见台账（预期 3 条真资源既有失败、capability 6/6）。

## Global Constraints

- **v2 行为不变**：确认/提问/中断/幂等既有用例原样通过；`on_before_execute` 为 None 时路径与改动前一致；M65 parity 17；regression 评测 6/6、capability 不低于基线（改了 `general.md` 提示词，必须跑 capability）。
- **只加 spec 列的**：错误码 3 个、配置键 2 个、工具 2 个（模型可见 +2）、端点 2 个、表 1 张（`device_lease`）+ 跨表列（confirmation/question/audit 的 origin/run/ticket 列）；不新增其他表/端点。
- **迁移可逆**：`0027_device_lease` 降回 `0026_tickets`，只删结构、旧行完好。
- **不 sleep 真实租约时长**：测试注入可控时钟与 `poll_seconds`。
- 不 push；提交 `<类型>(m71): <中文摘要>`；最后合并回 `dev` 打 `v3-m71`。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m71/`。

## Review Focus

1. **租约正确性**：容量 1 的硬互斥（表主键 `device_id`）、先到先得排队、`wait_seconds` 超时 `DEVICE_LEASE_BUSY`、TTL/续期/过期、重启清理、广播 `lease_changed`；两个持有者串行用例连跑 20 次。
2. **执行前取租约**：`ConfirmationService.invoke(..., on_before_execute=)` 在确认通过后、执行前调用；等确认/提问期间 `GET /api/agent/device-lease` 为 `held=false`；钩子为 None 时 v2 路径不变。
3. **抢占**：手动提交流水线、REST 原子操作、REST 写设备工具、停止运行中流水线、`DELETE /api/agent/device-lease` 五类都抢占；无租约时行为与 v2 一致。
4. **冒泡**：确认/提问 SSE、REST wire、WS 三处带 origin；工单状态 `waiting` 与总控收件箱 `human_request`，批准后回 `open`；卡片显示「来自工单「X」（代理名）」；没有 origin 时渲染与请求完全同前。
5. **策略身份与审计**：子代理用父 Worker 会话过策略/确认/审计；`agent_audit` 记 `run_id`/`ticket_id`/`agent_name` 且可按 `ticket_id` 过滤；`ceiling_risk` 规则落地（修 M70 缺口）。

---

### Task 0: 分支、§0 前置检查与计划入库（已完成）

- [x] 建分支 `device-lease-bubbling`，跑 §0 的 19 条检查；#10 缺口作为 M71 内修复；#12/#13 待做；#16 命中仅注释。
- [x] 提交本计划：`docs(m71): M71 设备租约与确认冒泡实施计划`。

### Task 1（G1）: 错误码与配置 + 模型与迁移 0027（spec T1、T2）

**Files:** `maa_api/domain/errors.py`、`tests/domain/test_errors.py`、`docs/v3/14`（§4）、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0027_device_lease.py`、`tests/db/test_models.py`、新建 `tests/db/test_device_lease_migration.py`

- [ ] 三个 `DEVICE_LEASE_*` 错误码（§4.3 状态码）与计数/文档；两个配置键（`lease.ttl_seconds`/`lease.wait_seconds`）与 `_FIELDS`/模板/ENV；`device_lease` 表、confirmation/question/agent_audit 的新列与索引，迁移可逆；`test_models.py` 登记表与迁移用例。
- [ ] 验证：`pytest tests/domain tests/test_settings.py tests/test_vapid_settings.py tests/db` → 0。
- [ ] 提交：`feat(m71): 租约错误码、配置与 0027 迁移`

### Task 2（G2）: origin、审计与确认来源（spec T3）

**Files:** `maa_api/agent/registry.py`、新建 `maa_api/agent/origin.py`、`maa_api/agent/confirmation.py`、`maa_api/agent/questions.py`、`maa_api/db/repositories/audit.py`、`maa_api/api/routers/agent.py`、新建 `tests/agent/test_confirmation_origin.py`、`tests/api/test_agent_router.py`

- [ ] `ToolOrigin` 与 `ToolContext.origin`；确认/提问落 `origin_run_id`/`origin_ticket_id` 并在 wire 带 origin；审计写 `run_id`/`ticket_id`/`agent_name`，列表可按 `ticket_id`；`origin.py` 的冒泡函数（`ticket_titles`/`origin_wire`/`bubble_human_request`/`settle_human_request`）先写好、T6 接入。
- [ ] 验证：v2 确认/提问/中断/幂等用例原样通过；带与不带 origin 两种路径都正确。
- [ ] 提交：`feat(m71): 工具来源、审计归属与确认/提问 origin`

### Task 3（G3）: DeviceLeaseService（spec T4）

**Files:** 新建 `maa_api/db/repositories/lease.py`、新建 `maa_api/agent/lease.py`（服务与数据类）、`tests/agent/lease/{__init__,conftest,test_device_lease_service}.py`

- [ ] `DeviceLeaseService`（`acquire`/`release`/`renew`/`preempt`/`current`/`cleanup_boot`、`LeaseHolder`、`ClaimGate`、`LEASE_DEVICE_ID`、`PreemptReason`/`ReleaseReason`、`holder_for`）按 §4.5.1–§4.5.3；仓储；测试注入时钟/poll/broadcast/假 thread_router。
- [ ] 验证：`pytest tests/agent/lease` → 0；两持有者串行用例连跑 20 次。
- [ ] 提交：`feat(m71): 设备租约服务`

### Task 4（G4）: 执行前回调与 LeaseMiddleware（spec T5）

**Files:** `maa_api/agent/confirmation.py`、`maa_api/agent/lease.py`（`LeaseMiddleware`/`holder_for`）、`maa_api/agent/harness/builtin_middleware.py`（`PolicyMiddleware` 组合钩子）、`maa_api/agent/runtime.py`（`build_agent_chain`/v2 `_build_chain`；v3 链插 `LeaseMiddleware`；**删除 `DeviceGateMiddleware`/`DeviceGate` 与 `app.state.device_gate`/`WorkerService._device_gate`/`pump.py` 引用**，同步 `main.py` 装配与受影响测试）、新建 `tests/agent/test_confirmation_before_execute.py`、`tests/agent/lease/test_lease_middleware.py`

- [ ] `invoke(..., on_before_execute=)` 在确认通过后、执行前调用（None 时路径不变）；`LeaseMiddleware` 经 `ctx.state["policy.before_execute"]` 登记并使用租约；等确认/提问期间不占租约；隐式租约在根运行结束时释放。
- [ ] 验证：`pytest tests/agent` → 0。
- [ ] 提交：`feat(m71): 执行前回调与租约中间件`

### Task 5（G5）: hold_device、release_device 与通用 Worker（spec T6）

**Files:** 新建 `maa_api/agent/tools/lease.py`、`maa_api/agent/tools/__init__.py`、`tests/agent/test_tool_catalog.py`、新建 `tests/agent/lease/test_lease_tools.py`、`maa_api/agent/specs/builtin/general.md`

- [ ] 两个工具（`rest_visible=False`、`HoldDeviceParams.purpose` 1–120）；`general.md` 按 §4.7 改；工具总数与模型可见各 +2；REST 目录看不到、invoke `TOOL_NOT_FOUND`。
- [ ] 验证：`pytest tests/agent/test_tool_catalog.py tests/agent/lease` → 0。
- [ ] 提交：`feat(m71): 设备租约工具与通用 Worker 提示`

### Task 6（G6）: 冒泡与子代理策略身份（spec T7）

**Files:** `maa_api/agent/harness/builtin_middleware.py`（`PolicyMiddleware`/`ClarificationMiddleware`、`MandateMiddleware` 的 ceiling_risk 修复）、新建 `tests/agent/harness/test_policy_origin_bubbling.py`、新建 `tests/agent/lease/test_origin_bubbling.py`

- [ ] 构造 `ToolContext` 时写 origin；待确认/提问分支：SSE 带 origin、冒泡（工单 `waiting` + 总控收件箱 `human_request`）、`finally` 收尾（`settle_human_request`），批准后回 `open`；子代理策略身份取父 Worker 会话（`ctx.state["policy.session_id"]`）；**顺手修 §0 #10**：`MandateMiddleware` 用 `ceiling_risk(decision.risk, decision.confirmation_action)` 比较。
- [ ] 验证：`pytest tests/agent` → 0。
- [ ] 提交：`feat(m71): 确认冒泡与子代理策略身份`

### Task 7（G7）: 队列领取闸门（spec T8）

**Files:** `maa_api/db/repositories/pipeline.py`、`maa_api/services/pipeline_runner.py`、`tests/db/test_repositories_pipeline.py`、`tests/services/test_pipeline_runner.py`

- [ ] `PipelineRunner.bind_lease_gate` 与 `claim_next(..., allowed_session_ids=)`；有租约时只领手动与持有者工单的流水线；无闸门时行为与 v2 完全一致（既有用例不改）。
- [ ] 验证：`pytest tests/db/test_repositories_pipeline.py tests/services/test_pipeline_runner.py tests/core` → 0。
- [ ] 提交：`feat(m71): 租约期间的队列领取闸门`

### Task 8（G8）: 人工抢占与端点（spec T9）

**Files:** 新建 `maa_api/api/routers/device_lease.py`、`pipelines.py`、`atomic.py`、`device.py`、`agent.py`、`tickets.py`、`maa_api/main.py`、新建 `tests/api/test_device_lease_router.py`、新建 `tests/api/test_lease_preemption_routes.py`、`tests/api/test_tickets_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`（§6）

- [ ] `GET`/`DELETE /api/agent/device-lease`；五类人工抢占；`TicketDetail.lease`；快照/类型/清单（+2 行、审计端点请求要点补 `ticket_id`）。
- [ ] 验证：`pytest tests/api` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m71): 设备租约端点与人工抢占`

### Task 9（G9）: 前端（spec T10）

**Files:** `web/src/api/keys.ts`、`query-bridge.ts`、`query-bridge.test.ts`、`features/agent/types.ts`、新建 `features/agent/origin.ts`、`features/confirmations/ConfirmationsPanel.tsx`、`confirmations.test.tsx`、`features/agent/QuestionCard.tsx`、`QuestionCard.test.tsx`

- [ ] origin 类型与来源展示（「来自工单「X」（代理名）」）；`lease_changed` 失效键；没有 origin 时渲染与请求完全同前。
- [ ] 验证：`pnpm typecheck` 0；`pnpm test --run` 0。
- [ ] 提交：`feat(m71): 确认与提问来源展示`

### Task 10（G10）: 恢复、装配、文档、全量验收与合并（spec T11）

**Files:** `maa_api/agent/runs/recovery.py`、`maa_api/main.py`（lifespan）、`tests/agent/tickets/test_ticket_recovery.py`、`tests/api/test_agent_assembly_wiring.py`、`docs/v3/02`/`03`/`04`/`05`/`14`、新建 `docs/v3/ledgers/m71-device-lease-bubbling.md`、`docs/v3/13`、`docs/v3/README.md`

- [ ] 启动时清理非本进程租约、装配 `device_lease_service`/`lease_middleware`/`bind_lease_gate`/`preempt_device`、工单结束释放租约与撤销授权监听；文档同步（05 §5.2 `on_prepare`→`on_before_execute` 等）；13 §8 记偏离；台账；README 状态行。
- [ ] 验证：spec §7 全部（pytest 不新增失败、openapi 0、前端五步 0、两个冒烟 `SMOKE OK`、regression 0、capability 不低于基线、断链 0、`git diff --check` 0）。
- [ ] 提交：`docs(m71): 契约同步、台账与状态行`；然后 `git merge --no-ff device-lease-bubbling -m "merge: 设备租约与确认冒泡（M71，见 docs/v3/13 §8.x）"` → `git tag v3-m71` → `git branch -d device-lease-bubbling` → 合并后复验与汇报。
