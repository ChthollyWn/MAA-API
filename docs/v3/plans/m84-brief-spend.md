# M84 每日简报与消耗护栏 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消耗护栏（`spend_ledger`、计划量按参数上限、入队预留/改参差额、agent+scheduled 受限而 manual 计入不限、默认 -1）与每日简报（`BriefService` 到点直接建 analyst 简报工单、事实/渲染/收尾/推送、`POST /api/agent/brief/run`），以及 `GET /api/agent/spend`、`/more/spend`、待你处理汇总、设置页「生成今日简报」。

**Architecture:** G1 表/迁移/枚举 + 错误码/设置（T1+T2）→ G2 SpendGuard（T3）→ G3 接入提交路径与预检（T4）→ G4 SpendMiddleware + 简报事实/渲染（T5+T6）→ G5 BriefService（T7）→ G6 接口与装配（T8）→ G7 前端（T9）→ G8 冒烟脚本（T10）→ G9（我：文档、验收、合并 tag）。

**Spec:** [m84-brief-spend](../specs/m84-brief-spend.md)（881 行）

## 开工基线（2026-11-05）

- 分支 `brief-spend`，基点 `dev` = `14be1d6`（M83 合并）；tag `v3-m83`；迁移 head `0036_watchdog`，下一号 `0037`。
- §0 1–9、12–18 符合；**#10/#11 与 spec 写法不符**（`MandateMiddleware` 在 `workers/middleware.py:82`、`LeaseMiddleware` 在 `lease.py:644`，链在 `runtime.py`；MANDATE 拦截**不写审计**）；#19 用户已确认「按 spec 的 R1–R6/B1/Q4/Q7」。
- 全量基线：3 条真资源失败（expected）。
- **裁决（记 13 §8.153–8.162）**：
  1. **用户确认**：R1 适用范围（agent/scheduled 受限、manual 计入不限）、R2 过期药不计入、R3 按参数上限不回填 actual、R4 北京时间 04:00 分日、R5 改参按调高差额、R6 入队预留领取不查；B1 BriefService 直接建单不经事件规则；Q4 默认 -1；Q7 v2 通知不变。
  2. **§0 #10/#11 的接缝修正**：`MandateMiddleware`/`LeaseMiddleware` 在 `workers/middleware.py`/`lease.py`，链在 `runtime.py`（Mandate 之后、Lease 之前插 SpendMiddleware）；MANDATE 拦截不写审计，因此 `SpendGuard` 自带 `record_spend_rejection(...)`（经 `AuditRepository.create(AgentAudit(...))`，`status=rejected`、`risk_level=consume`，入参用仓储自动裁剪）。
  3. **产物服务**：`ArtifactService.put(body: bytes, ...)` 无 `update`；实际新增 `update(artifact_id, *, content: str, meta: dict)`（替换正文与 meta、重算 size/sha/update）。spec 伪码的 `content` 按实际参数名 `body` 调用。
  4. **计数**：错误码 163/162→**164/163**（`SPEND_CAP_EXCEEDED` 403）；端点 179→**181**（agent 76→78）；业务表 52→**53**；配置键 +7（`brief.*` 4 + `spend.*` 3）；工具/规则/定义数不变；迁移 head **0037**。
  5. **行号全部按符号定位**（spec 行号已漂移：policy +49、pipeline_runner +40、main 关停 600+ 行）。
  6. **简报事实**：跨里程碑的表用 `sqlalchemy.text()` 只读 03 定死的表名列名，不 import 模型类；每块单独 try，失败该块为空。
  7. **R1 的限制与计入**：预检（`SpendMiddleware`/`invoke_tool`）+ 入队预留 + 改参差额三处；manual 只计入不拦。
  8. **保留**：`spend_ledger` 不在 03 §14 保留策略里、不自动清理。
  9. **docs/09 §6 与 spec 冲突**：按用户确认以 spec 为准，G9 把 09 §6 改写成 R2–R4/R6 口径并记 13。
  10. **冒烟**：新增 `scripts/brief_spend_smoke.py`（四步 + `SMOKE OK`），不改既有冒烟步骤。

## Global Constraints

- 计划量只看任务参数上限（Fight 的 `medicine`/`stone`，不读 `expiring_medicine`；Recruit 仅 `expedite=true` 时用 `times`，不读 `expedite_times`），字段缺失/非正整数按 0。
- 日界为北京时间 04:00；用量 = `actual` 优先（本里程碑不写）→ 从未开始且流水线已终态为 0 → 否则 `planned`；所有来源计入、只有 agent/scheduled 受限；上限 -1 不限、0 禁止；超一整条拒绝、不部分入队。
- 守卫锁永远最内层（队列 `_admission_lock` / runner `operation_lock` 之后）；锁内不拿别的锁。
- 预检只读、失败放行（入队检查是最终裁决）；入队与改参在同一事务内预留/改账本。
- 简报直接建工单、不经事件规则、不发事件；每日自动至多一张（`origin=schedule`）；手动不受限；30 分钟无汇报收尾 `facts_only`；无可见页面才推 Web Push。
- 不改 `analyst.md`、不新增工具/WS 事件；不改 v2 通知。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m84/`。
- 不 push；提交 `<类型>(m84): <中文摘要>`；最后合并回 `dev` 打 `v3-m84`。

## Review Focus

1. **表/迁移**：`spend_ledger` 9 列/三 FK SET NULL/索引/自增；迁移可逆；枚举；表计数 53。
2. **计划量/日界/用量/判定**：Fight/Recruit 规则、04:00 北京分日、用量 SQL（started_at 为空且已终态=0）、manual 计入不限、-1/0、整条拒绝、50 条拦截 deque。
3. **SpendGuard**：precheck（submit 归一化求和、set_task_params 差额、参数非法放行、读库异常放行、被拦写 rejected 审计）、reserve_submission（同事务、幂等旧行不重复记账）、reserve_patch（只计调高、补行）、summary（三项固定顺序/连续日/remaining null）、blocked_today。
4. **接入**：QueueService 绑定与提交、PipelineRunner 绑定+`spend_source`、工具传 AGENT、定时任务 skipped/立即执行抛错、`invoke_tool` 预检 403 且无确认卡。
5. **SpendMiddleware**：链位 Mandate 后 Lease 前；`SPEND_TOOLS` 才检查；超限先写 rejected 审计再返回 error、不调内层（无确认卡）；其他工具直通。
6. **简报**：facts 各块取法/昨日窗口/理智估算/待处理六类/失败降级；渲染小节省略与 4000 字截断；BriefService 到点/手动/每日一张/30 秒巡检/30 分钟超时/推送条件与 payload；产物 meta 键与 `update`。
7. **接口/前端**：GET spend 默认近 7 天/校验/形状；POST brief/run 202/503；`/more/spend` 三行+7/30 天表格+拦截列表+InfoHint；待你处理 spend 汇总行（排序/文案）；设置页「生成今日简报」；`brief_spend_smoke` 四步。

---

### Task 0: 分支、§0、用户确认、计划入库（已完成）

- [x] 建 `brief-spend`；§0 1–19；用户确认 R1–R6/B1/Q4/Q7；10 项接缝裁决；提交计划。

### Task 1（G1）: 表/迁移/枚举 + 错误码/设置（spec T1+T2）

**Files:** `domain/enums.py`、`tests/domain/test_enums.py`、`db/models.py`、新建 `migrations/versions/0037_spend_ledger.py`、`tests/db/test_models.py`、新建 `tests/db/test_spend_ledger_migration.py`、`domain/errors.py`、`tests/domain/test_errors.py`、`settings.py`、`services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`docs/v3/14`

- [ ] `SpendKind`；`SpendLedger`；迁移 0037；`SPEND_CAP_EXCEEDED` 403（164/163）；`BriefSettings` 4 键 + `SpendSettings` 3 键 + `_FIELDS`/模板/测试；14 号文档错误码。
- [ ] 验证：`pytest tests/db tests/domain tests/test_settings.py tests/test_vapid_settings.py` → 0；`alembic upgrade head --sql | grep -c spend_ledger` ≥1。
- [ ] 提交：`feat(m84): 消耗账本、错误码与简报/消耗设置`

### Task 2（G2）: SpendGuard（spec T3）

**Files:** 新建 `agent/spend.py`、新建 `tests/agent/test_spend.py`

- [ ] 计划量/日界/用量/判定/precheck/reserve_submission/reserve_patch/summary/blocked_today/`record_spend_rejection`；§6 的 13 条用例。
- [ ] 验证：`pytest tests/agent/test_spend.py` → 0。
- [ ] 提交：`feat(m84): 消耗护栏与预检/预留`

### Task 3（G3）: 接入提交路径（spec T4）

**Files:** `services/queue_service.py`、`services/pipeline_runner.py`、`agent/tools/pipeline.py`、`services/schedule_service.py`、`api/routers/agent.py`、新建 `tests/services/{test_queue_spend,test_schedule_spend}.py`、新建 `tests/agent/test_pipeline_runner_set_params.py`、新建 `tests/api/test_agent_invoke_spend.py`、`tests/agent/test_pipeline_tools.py`（适配）

- [ ] 两个 `bind_spend_guard`；`submit` 预留；`set_task_params(..., spend_source)`；工具传 AGENT；定时任务 skipped/立即执行抛；`invoke_tool` 预检 403。
- [ ] 验证：`pytest tests/services tests/agent/test_pipeline_runner_set_params.py tests/agent/test_pipeline_tools.py tests/api/test_agent_invoke_spend.py` → 0。
- [ ] 提交：`feat(m84): 提交路径的消耗预检与预留`

### Task 4（G4）: SpendMiddleware + 简报事实/渲染（spec T5+T6）

**Files:** `agent/harness/builtin_middleware.py`（或实际链文件）、`agent/runtime.py`（链位）、新建 `tests/agent/harness/test_spend_middleware.py`、新建 `agent/brief/{__init__,facts,render}.py`、新建 `tests/agent/brief/{test_facts,test_render}.py`

- [ ] SpendMiddleware（Mandate 后/Lease 前、审计先写、不调内层）；`BriefFacts`/`collect_facts`/`render_facts_markdown`/`render_brief_markdown`/`render_ticket_brief`。
- [ ] 验证：`pytest tests/agent/harness tests/agent/brief` → 0。
- [ ] 提交：`feat(m84): 消耗中间件与简报事实渲染`

### Task 5（G5）: BriefService（spec T7）

**Files:** 新建 `agent/brief/service.py`、`agent/artifacts/service.py`（`update`）、新建 `tests/agent/brief/test_service.py`

- [ ] `BriefService`（attach_scheduler/start/stop/on_cron/run_now/finalize/巡检/推送）；产物 `update`；每日一张/手动/30 分钟超时/重启处理。
- [ ] 验证：`pytest tests/agent/brief tests/agent/artifacts` → 0。
- [ ] 提交：`feat(m84): 简报服务与收尾推送`

### Task 6（G6）: 接口与装配（spec T8）

**Files:** 新建 `api/routers/spend.py`、`main.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`、新建 `tests/api/test_spend_router.py`

- [ ] 两端点（GET spend/POST brief run）；装配 spend_guard 绑定、brief_service 调度/start/两处 stop；14 号文档 181/agent 78；OpenAPI/类型。
- [ ] 验证：`pytest tests/api tests/domain` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m84): 消耗与简报端点、装配`

### Task 7（G7）: 前端（spec T9）

**Files:** `web/src/api/keys.ts`、新建 `features/spend/{api,SpendPage,spend.test}.*`、`nav.ts`、`router.tsx`、`features/console/{attention,use-attention,AttentionList}.ts(x)`、`attention.test.ts`、新建 `features/settings/BriefControl.tsx`、`SettingsPage.tsx`、`sections.ts`

- [ ] `/more/spend`（三行/表格/拦截/InfoHint）；导航；待你处理 spend 汇总（排序/文案）；设置页简报控制与分区 summary。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` SMOKE OK。
- [ ] 提交：`feat(m84): 消耗护栏页与简报控制`

### Task 8（G8）: 冒烟脚本（spec T10 冒烟）

**Files:** 新建 `scripts/brief_spend_smoke.py`

- [ ] 四步：设上限→REST 提交被拦（403/无确认/审计 rejected）→ 手动提交成功并计入→定时任务立即执行被拦→手动生成简报 202 且 60 秒内 `ready`；末行 `SMOKE OK`。
- [ ] 验证：`scripts/brief_spend_smoke.py | tail -n 1` → `SMOKE OK`；`git diff --check` 0。
- [ ] 提交：`test(m84): 简报与消耗护栏冒烟`

### Task 9（G9，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,03,08,09,11,13,14,16,README}`、新建 `docs/v3/ledgers/m84-brief-spend.md`

- [ ] 02 BriefService/`brief/`；03 §11.6 actual 不写、§13 不含过期药；08 §7 内容清单；09 §6 按 R2–R4/R6 改写（用户已确认）；11 来源表 spend 行与页说明；13 §8.153–8.162；16 两段配置；README；台账。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、四冒烟、regression/capability、断链、diff；手动项记「未验证」。
- [ ] 提交 `docs(m84): 台账、契约同步与状态行`；`git merge --no-ff brief-spend` → tag `v3-m84` → 删分支 → 合并后复验 → 汇报。
