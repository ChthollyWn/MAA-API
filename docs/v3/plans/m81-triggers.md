# M81 事件源与两级过滤 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一事件入口：流水线/内核/设备/定时/更新/理智事件先进 `agent_trigger`，经程序准入与便宜模型研判两级过滤，按规则 off/observe/auto 处理；M73 失败诊断改走两条 auto 内置规则；observe 写观察记录并可审阅；规则可覆盖/新建（含定时）；每日研判限额与启动补处理。

**Architecture:** G1 枚举/错误码/设置 + 三表/迁移/仓储（T1+T2）→ G2 事件种类/规则/内置规则 + 研判代理（T3+T4）→ G3 TriggerService 管线与建单/收件箱接缝（T5）→ G4 接缝接线与诊断迁移（T6）→ G5 定时 job/保留 + REST 端点（T7+T8）→ G6 前端（T9）→ G7 冒烟/评测（T10）→ G8（我：文档、验收、合并 tag）。

**Spec:** [m81-triggers](../specs/m81-triggers.md)（899 行）

## 开工基线（2026-11-05）

- 分支 `triggers`，基点 `dev` = `65c30b2`（M80 合并）；tag `v3-m80`；迁移 head `0033_rewind`，下一号 `0034`。
- §0 1–14 符合：四表/建单入口/ThreadRouter(`deliver`)/InboxService/FinishHookFanout/OrchestratorService/RunService+boot_id/ModelRouter+`complete_structured`/枚举（TRIGGER_EVAL、EventSource、TicketOrigin、InboxKind 含 TRIGGER）/M73 诊断入口/v2 四接缝/14 号文档；全量基线见台账（预期 3 条真资源失败）。
- **接缝裁决（记 13 §8.121–8.132）**：
  1. **`deliver_to_subscribers`**：实际已有等价 `ThreadRouter.deliver`（只投活跃订阅、无订阅返回空列表、不回落总控）。实际：新增 `deliver_to_subscribers` 作为薄封装（内部调 `deliver` 并把返回补齐为工单 id 列表），保留 spec 名字。
  2. **M69 建单入口**：`TicketService.create` 现有签名缺 `brief/strength/risk_ceiling/policy_version/source_trigger_id`。实际：扩展可选关键字参数（默认 None，行为不变）——`source_trigger_id` 写 ticket 列；`brief` 传给 Worker 会话的 `attach_worker_session`；`strength`/`risk_ceiling`/`policy_version` 传给委托构建（缺省沿用现状）。若某参数无法只加不改，报告。
  3. **`boot_id`**：`AgentIncidentService` 当前自造 uuid4；实际 main 的 `incident_options` 加 `boot_id=app.state.boot_id`，保证 `core.crashed` dedupe 跨重启语义。
  4. **收件箱幂等查询**：`AgentInboxRepository` 无按 `kind+ref_id` 查存在性的方法；实际加最小 `exists(kind, ref_id)`（或 `get_by_ref`），供 `_process` 第 1 步与 `forward_to_orchestrator` 复用。
  5. **收件箱渲染**：`InboxService.render` 现把 `THREAD_EVENT` 与 `TRIGGER` 合并渲染；màu 按 spec §4.2 拆开：`trigger` 用三行格式（`【事件】{summary}` / 话题行 / 研判行），`THREAD_EVENT` 保持现状。
  6. **文档 11**：其把观察记录写成独立路由 `/more/triggers/observations`；以 spec 单页三页签为准，G8 同步文档。
  7. **计数**：错误码 157/156→**160/159**（`TRIGGER_RULE_NOT_FOUND` 404、`TRIGGER_RULE_INVALID` 400、`OBSERVATION_NOT_FOUND` 404）；端点 166→**172**（agent 63→69）；业务表 46→**49**；代理定义 17→**18**（`trigger_evaluator`）；配置键 +3；工具不变（100/99）；迁移 head **0034**。
  8. **`agent.auto_diagnose_failures`**：只改 `_FIELDS` label 为「自动诊断流水线与内核故障（两条诊断规则的总开关）」，键与行为不变。
  9. **T10 评测**：三条 regression 场景若 M68 格式无法手写 cassette，只留 pytest 并在台账记录。
  10. **冒烟触发器**：`orchestrator_smoke.py` 新增 `[ok] triggers` 一步（FakeLLM 研判断 accept + 设备不可用 observe + 流水线失败 diagnose）；末行仍 `SMOKE OK`。

## Global Constraints

- `TriggerService.publish` 是事件入表唯一入口；同 `dedupe_key` 只处理一次；事件先入表再准入/研判/路由。
- 订阅直通不回退：有订阅话题的事件先投 Worker 收件箱；无订阅才按规则、再按「交总控」集合。
- `triggers.enabled=false`：仍入表、订阅直通照常，但不按规则研判/建单；`core.failed` 仍交总控。
- 防御事件风暴：设备只在状态变化时发、规则冷却、`eval_daily_limit` + M67 预算双闸；研判输出非法两次按 fallback。
- 不新增工具；总控/Worker 定义不变；`trigger_evaluator` 是后台定义、无工具。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m81/`。
- 不 push；提交 `<类型>(m81): <中文摘要>`；最后合并回 `dev` 打 `v3-m81`。

## Review Focus

1. **表/迁移/仓储**：三表列/索引/FK 精确；`ticket.source_trigger_id` 循环外键与 M69 做法一致；迁移可逆、离线可渲染、降级不丢其他数据；仓储方法与 spec 语义（`last_routed`、`count_evaluated_since`、`stats_since`、`purge`）。
2. **种类/规则**：12 种事件、前缀与来源对应、四类无效原因、摘要「未知」截断；20 条内置规则字段与 08 §3.2 一致；覆盖语义（NULL 继承、改回删行、内置 source/kind_pattern 不可改）、条件四种写法、定时规则只匹配自己、gate_setting、`version`。
3. **研判**：运行 `trigger_eval`、`trigger_decision` schema、两次非法 UNAVAILABLE、worker 不匹配按规则、事件卡顺序与上限（8 单/5 事件/4000 字）。
4. **管线**：publish 的锁/去重/rejected；`_process` 六步（订阅直通→规则→冷却→研判→分支→finish）；fallback；建单复用与失败；forward 幂等；observe 记录与广播；recover 24h/不重复；同话题顺序。
5. **接线**：FinishHookFanout→publisher、core crash→core.crashed、core failed→core.failed、设备状态变化、update 广播、boot_id、诊断适配器；M73 用例意图全部有对应新用例。
6. **job/保留**：`maa-trigger-sanity`、定时规则 job 增删、`_fire_rule` 去重键；`run_retention` 两个新键与两条清理、vacuum。
7. **端点/前端**：6 端点/3 错误码/DTO/时间 Z/分页校验/`observation_created`；三分页签、切档 PUT 完整内容、覆盖/恢复/删除、建议标签、空态、事件失效。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `triggers`；§0 1–14；10 项接缝裁决写入本计划；提交计划。

### Task 1（G1）: 枚举/错误码/设置 + 表/迁移/仓储（spec T1+T2）

**Files:** `domain/enums.py`、`tests/domain/test_enums.py`、`domain/errors.py`、`tests/domain/test_errors.py`、`settings.py`、`services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`db/models.py`、新建 `migrations/versions/0034_triggers.py`、新建 `db/repositories/triggers.py`、`tests/db/test_models.py`、新建 `tests/db/test_triggers_migration.py`、新建 `tests/db/test_repositories_triggers.py`、`docs/v3/14`（错误码）

- [ ] 四枚举；三错误码（160/159）；`TriggersSettings` 三键 + `_FIELDS`/模板/测试；`agent.auto_diagnose_failures` label。
- [ ] 三表 + Ticket `source_trigger_id`（循环外键）；迁移 0034（可逆/离线/数据保留）；三仓储（含 `AgentInboxRepository.exists(kind, ref_id)` 最小方法可放 G3，本任务先做 trigers 仓储）。
- [ ] 验证：`pytest tests/db tests/domain tests/test_settings.py tests/test_vapid_settings.py` → 0；`alembic upgrade head --sql | grep -c agent_trigger` ≥1。
- [ ] 提交：`feat(m81): 事件枚举、三表、迁移与设置`

### Task 2（G2）: 种类/规则/内置规则 + 研判代理（spec T3+T4）

**Files:** 新建 `agent/events/{__init__,kinds,rules,builtin_rules,evaluator}.py`、新建 `specs/builtin/trigger_evaluator.md`、新建 `llm/schemas/trigger_decision.py`、`llm/schemas/__init__.py`、新建 `tests/agent/events/{test_kinds,test_rules,test_builtin_rules,test_evaluator}.py`、`tests/agent/specs/*`、`tests/api/test_agent_specs_router.py`

- [ ] `kinds.py`（12 种/四校验/摘要/attention）；`builtin_rules.py`（5 条 + 草稿构造）；`rules.py`（`EffectiveRule`/`RuleSet`/条件/`TriggerRuleService` 覆盖语义/校验）；`evaluator.py`（运行 + schema + 事件卡）；定义与 schema 逐字；定义名单测试 18。
- [ ] 验证：`pytest tests/agent/events tests/agent/specs tests/api/test_agent_specs_router.py` → 0；真实 `GET /api/agent/specs` 含 `trigger_evaluator`。
- [ ] 提交：`feat(m81): 事件种类、规则与研判代理`

### Task 3（G3）: TriggerService 管线（spec T5）

**Files:** 新建 `agent/events/bus.py`、`tickets/threads.py`（`deliver_to_subscribers`）、`tickets/service.py`（create 可选参数）、`tickets/inbox.py`（render 拆分）、`db/repositories/ticket.py`（`exists`）、新建 `tests/agent/events/test_bus.py`、相关测试适配

- [ ] `TriggerService`（publish/_process/fallback/create_ticket/forward/write_observation/recover）；建单入口扩展；收件箱 render 两格式；`deliver_to_subscribers` 薄封装；条件与冷却/日限额/信号量。
- [ ] 验证：`pytest tests/agent/events/test_bus.py tests/agent/tickets tests/agent/orchestrator` → 0；§4.5.7 每行有对应用例。
- [ ] 提交：`feat(m81): 事件总线与两级过滤管线`

### Task 4（G4）: 接缝接线与诊断迁移（spec T6）

**Files:** 新建 `agent/events/wiring.py`、`agent/incidents.py`、`main.py`、新建 `tests/agent/events/test_wiring.py`、`tests/agent/test_incidents.py`（改）、`tests/api/test_agent_assembly_wiring.py`

- [ ] wiring 四件套（pipeline/device/core/update）；FinishHookFanout/main 接缝；incidents 建单改发事件（合并判断不变）；boot_id；M73 测试断言改为「事件入表 + 诊断工单」，逐用例新旧对照。
- [ ] 验证：`pytest tests/agent tests/api/test_agent_assembly_wiring.py` → 0。
- [ ] 提交：`feat(m81): 事件接缝接线与诊断迁移`

### Task 5（G5）: 定时 job/保留 + REST（spec T7+T8）

**Files:** `agent/events/bus.py`（job 部分）、`agent/events/wiring.py`（`SanityWatcher`）、`services/retention_service.py`、新建 `tests/agent/events/test_jobs.py`、`tests/services/test_retention_service.py`、新建 `api/routers/triggers.py`、`main.py`（include）、新建 `tests/api/test_triggers_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`（路由 172/agent 69）

- [ ] `start_jobs/_sync_rule_jobs/_fire_rule/close`；`SanityWatcher`；retention 两键；6 端点 + DTO + `observation_created` 广播；14 号文档计数。
- [ ] 验证：`pytest tests/agent/events/test_jobs.py tests/services/test_retention_service.py tests/api/test_triggers_router.py tests/domain/test_route_inventory.py` → 0；`dump_openapi --check` 0。
- [ ] 提交：`feat(m81): 定时规则、保留与事件端点`

### Task 6（G6）: 前端（spec T9）

**Files:** 新建 `web/src/features/triggers/**`、`router.tsx`、`nav.ts`、`api/keys.ts`、`realtime/query-bridge.ts` + 测试

- [ ] 三分页签（规则/待审阅/事件记录）、编辑/恢复/删除、切档 PUT、建议标签/统计/gate hint、空态；`observation_created` 失效。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` `SMOKE OK`。
- [ ] 提交：`feat(m81): 事件规则页与观察审阅`

### Task 7（G7）: 冒烟与评测（spec T10）

**Files:** `scripts/orchestrator_smoke.py`、`tests/evals/scenarios/*`（及 cassette 若支持）

- [ ] 冒烟 `[ok] triggers`（研判断 accept/observe 记录/流水线失败建 diagnose 且 source_trigger_id 指回）；三条 regression 场景（能 replay 就加 cassette，否则 pytest-only 并报告）。
- [ ] 验证：`orchestrator_smoke.py` 末行 `SMOKE OK`；`agent_eval --suite regression --mode replay` → 0。
- [ ] 提交：`test(m81): 事件冒烟与评测场景`

### Task 8（G8，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,03,04,05,08,11,13,14,16,README}`、新建 `docs/v3/ledgers/m81-triggers.md`

- [ ] 02 §2.2/§4.1/§5 单槽位钩子表；03 表/枚举；04 六端点与事件；05 trigger 收件箱；08 实现同步；11 事件规则页（单页三页签）；13 §8.121–8.132；16 triggers 键；README；台账。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、三冒烟、regression/capability、断链、diff；手动项记「未验证」。
- [ ] 提交 `docs(m81): 台账、契约同步与状态行`；`git merge --no-ff triggers` → tag `v3-m81` → 删分支 → 合并后复验 → 汇报。
