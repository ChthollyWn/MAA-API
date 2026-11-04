# M73 既有场景迁移与 P1 收口 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 六份专职 Worker 定义（`daily_ops`/`copilot`/`planner`/`diagnose`/`analyst`/`ops`）按 05 §3.2 落地；流水线失败与内核崩溃改建 `diagnose` 工单（origin=system、推断委托、上限 none、同 `incident_key` 去重）；`run_incident` 生产调用点删除；作业授权仍按 Worker 会话；总控提示词按用户原话选种类；新增 `scripts/orchestrator_smoke.py`；交出 01 §9 的 P1 验收记录。

**Architecture:** G1 六份定义+加载测试 → G2 诊断改建单（incidents.py + 接缝） → G3 总控选种类（orchestrator.md + 路由测试） → G4 `orchestrator_smoke.py` → T5（我：文档、P1 验收记录、全量验收、合并 tag）。

**Spec:** [m73-p1-migration](../specs/m73-p1-migration.md)（352 行）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `p1-migration`，基点 `dev` = `fb598eb`（M72 合并）；tag `v3-m72`；**无迁移**。
- §0：1–3 符合；#4 `TicketService.create` 能传 `origin`（system→inferred→ceiling none），**不能传 `brief`/`result`/`strength`**；#5 `incidents.py` 仍有 `pipeline_failed`/`core_crashed`/`run_incident`；#6 两个钩子在；#7 授权按 `session_id`；#8 指挥台与 `agent_smoke.py` 在；#9 工具数 **93 / 92**（本里程碑不改）；#10 `tests/agent` 基线 `1062 passed, 1 skipped, 2 xfailed`。
- **接缝裁决（记 13 §8.51–8.55）**：
  1. **诊断工单经 `WorkerService.open_ticket` 建**（不是裸 `TicketService.create`）：工单必须有 `worker_session_id`、任务 brief 收件箱与唤醒，否则诊断 Worker 不会跑（spec §4.5 伪代码只写 create，未覆盖运行链路）。
  2. 去重按 spec §4.1：`ticket.kind='diagnose'` + 活动状态 + `json_extract(result,'$.incident_key')` 相等；在 `IncidentService._key_lock` 内查-建，并发安全。**不用** `agent_session.incident_key` 唯一索引做诊断去重（该索引给 `create_ticket` 工具用，M73 建会话传 `incident_key=None` 以免旧已结束会话撞索引）。
  3. `TicketService` 加内部方法 `update_result(ticket_id, patch: dict)`（合并写 `ticket.result`，不删已有键）；`create` 签名不动。
  4. `AgentIncidentService` 在 `worker_service` 之前装配（main.py ~1347 vs ~1439），因此 `worker_service` 在**调用时**从 `self.request.app.state.worker_service` 解析；`ticket_service`/`inbox_service` 同样调用时解析。
  5. `WorkerService.open_ticket` 加可选 `brief_header`（默认「总控交给你的任务：」），诊断用「自动诊断：」；前端种类中文标签由 `specTitles`（`/api/agent/specs` 的 `title`）自动带出，无需改前端（spec §4.6 假设的「已有种类映射」不存在）。
  6. `IncidentResult.session_id` 字段保留（调用方签名不变），诊断改建单后填**工单 id**；`_refresh_existing_pipeline_context` 改为向工单 Worker 会话写 `system` 项。

## Global Constraints

- **不新增**表、端点、错误码、配置键、工具；不改 `general`/总控/四个子代理的工具名单与 `common.md`；不改 v2 作业授权表/确认卡/次数规则；不改前端信息架构。
- `agent.auto_diagnose_failures=false` 时两个入口直接返回、不建单不写收件箱。
- `run_incident` 在生产路径无调用（`grep -rn "run_incident" maa_api` 只剩定义或回放测试）。
- 旧诊断测试的「失败会被看到、不会重复诊断」两个意图必须保留（可改断言形式）。
- 六份定义的工具名单逐字照 05 §3.2、顺序一致；工具数保持 93/92；错误码/端点/表数不变；无迁移。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m73/`。
- 不 push；提交 `<类型>(m73): <中文摘要>`；最后合并回 `dev` 打 `v3-m73`。

## Review Focus

1. **六份定义**：frontmatter 的 tools/device/risk_ceiling/delegates/version/max_steps/budget 与 §4.7 一致；正文含公共开头 5 句与各自要点；`load()` 不抛；工具名都在 `build_registry()`。
2. **诊断链路**：失败→一张 `diagnose` 工单（origin=system、inferred、none、brief 含 pipeline_id/终态/错误码、`result.incident_key`）；同 key 再失败→不新建、Worker 收件箱 +1 条 `system`；开关关→什么都不发生；内核崩溃键 `core:{core_id}:{boot_id}:{generation}`；诊断 Worker 用 `submit_pipeline` 含理智药→`MANDATE_SCOPE_EXCEEDED` 且无确认行；`run_incident` 生产调用为 0。
3. **总控选种类**：「把日常做了」→ `create_ticket(kind=daily_ops)`；「做日常并更新资源」→ 两张（daily_ops、ops）；提示词含 §4.7.7 段、version+1、工具名单不变。
4. **冒烟**：`scripts/orchestrator_smoke.py` 六步（orchestrator/ticket/mandate/diagnose/dedupe/SMOKE OK）；`agent_smoke.py` 仍 `SMOKE OK`。
5. **P1 验收**：01 §9 四条标准（指挥台对话、建单+执行+确认冒泡+汇报回总控、重启恢复、v2 业务不回退）在台账里各有一条证据。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `p1-migration`，跑 §0 十条；接缝裁决写入本计划；提交计划。

### Task 1（G1）: 六份定义与加载测试（spec T1）

**Files:** 新建 `maa_api/agent/specs/builtin/{daily_ops,copilot,planner,diagnose,analyst,ops}.md`、新建 `tests/agent/test_builtin_workers.py`

- [ ] 按 §4.7 写六份定义（frontmatter 字段逐字、正文含公共开头 5 句与各自要点、diagnose 的 12 工具复用 `INCIDENT_ALLOWED_TOOLS` 的集合）。
- [ ] `test_builtin_workers.py`：六份可被 `AgentSpecRegistry.load()` 加载；每份 `tools` 与 05 §3.2 集合相等（顺序也断言）；`device`/`risk_ceiling`/`delegates` 与 §4.7 相等；工具名都在 `build_registry()`。
- [ ] 验证：`pytest tests/agent/test_builtin_workers.py` → 0；`pytest tests/agent/specs` → 0。
- [ ] 提交：`feat(m73): 六份专职 Worker 定义`

### Task 2（G2）: 诊断改建单（spec T2）

**Files:** `maa_api/agent/incidents.py`、`maa_api/agent/tickets/service.py`（`update_result`）、`maa_api/agent/workers/service.py`（`brief_header`）、`tests/agent/test_incidents.py`、`tests/agent/test_runtime.py`（如需）

- [ ] `TicketService.update_result(ticket_id, patch)`；`open_ticket(..., brief_header=...)`。
- [ ] `incidents.py`：`pipeline_failed`/`core_crashed` 保留类名与签名，改为 `_open_or_append(incident_key, title, brief)`：开关检查 → `_key_lock` 内查活动同 key 诊断工单（`json_extract`）→ 命中则向该工单 `worker_session_id` 写一条 `system` 项（`inbox_service.put`）→ 未命中经 `WorkerService.open_ticket(kind="diagnose", origin="system", strength="inferred", ...)` 建单并 `update_result` 写 `incident_key`；删除 `run_incident` 调用；`IncidentResult.session_id` 填工单 id；`_refresh_existing_pipeline_context` 改向工单收件箱追加。
- [ ] 测试：把旧「建 incident 会话并跑 `run_incident`」用例改为「建 diagnose 工单」；FakeLLM 让诊断 Worker 调 `get_pipeline` 后 `report(ready)`；补 §6 四条（一张工单/同 key append/开关关/inferred 拦截理智药）。
- [ ] 验证：`pytest tests/agent/test_incidents.py tests/agent/test_runtime.py` → 0；`grep -rn "run_incident" maa_api` 无生产路径调用。
- [ ] 提交：`feat(m73): 诊断改建 diagnose 工单`

### Task 3（G3）: 总控选种类（spec T3）

**Files:** `maa_api/agent/specs/builtin/orchestrator.md`、新建 `tests/agent/test_orchestrator_routing.py`

- [ ] orchestrator.md 加 §4.7.7 段落原文，`version` +1，工具名单不变。
- [ ] 测试（脚本化 FakeLLM）：用户「把日常做了」→ 总控 `create_ticket` 的 `kind=daily_ops`；「做日常并更新资源」→ 两张（daily_ops、ops）。
- [ ] 验证：`pytest tests/agent/test_orchestrator_routing.py` → 0；`pytest tests/agent/specs` → 0。
- [ ] 提交：`feat(m73): 总控按原话选择 Worker 种类`

### Task 4（G4）: `orchestrator_smoke.py`（spec T4）

**Files:** 新建 `scripts/orchestrator_smoke.py`

- [ ] 仿 `agent_smoke.py` 起真实 lifespan + 临时库；六步打印 `[ok] orchestrator/ticket/mandate/diagnose/dedupe`，成功末行 `SMOKE OK`，失败 `[orchestrator_smoke][FAIL] <原因>` 退出 1；假客户端注入用 `app.state` 运行时的 `client_factory`（`agent_smoke.py` 约 803 行处）。
- [ ] 验证：`scripts/orchestrator_smoke.py` → `SMOKE OK`；`scripts/agent_smoke.py` → 仍 `SMOKE OK`。
- [ ] 提交：`feat(m73): 总控与诊断冒烟`

### Task 5（G5，我）: 文档、P1 验收记录与全量验收（spec T5/T12 内容）

**Files:** `docs/v3/01`（P1 结束标准下加一句）、`docs/v3/ledgers/m73-p1-migration.md`（新）、`docs/v3/13 §8.51–8.55`、`docs/v3/README.md`、必要时 05/14 核对

- [ ] 01 §9 加「M73 起诊断走工单」；05 §3.1 末句核对一致；台账含 01 §9 四条标准的证据（如何验证、结果）、capability 通过率、§0 结果、接缝裁决。
- [ ] 跑 §7：全量 pytest（不新增失败）、`dump_openapi --check`、`pnpm gen:api:check`、两个冒烟 `SMOKE OK`、regression replay 全过、capability 不低于基线（orchestrator.md 改了提示词）、断链 0、`git diff --check`。
- [ ] 提交 `docs(m73): P1 验收记录与台账`；`git merge --no-ff p1-migration` → tag `v3-m73` → 删分支 → 合并后复验 → 汇报。
