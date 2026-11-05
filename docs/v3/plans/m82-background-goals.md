# M82 后台工单与目标模式 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 后台目标（goal）：总控 `create_goal`/REST 建目标并建规划工单（`planner`），规划 ready 写入里程碑并关联养成计划/定时任务；每个 active 目标按 `review_cron` 复盘（逾期标 missed、完成判定、`goal.review_due`/`goal.completed`/`goal.deadline_passed`），内置规则 `goal-review`（auto）研判后建复盘工单；暂停/恢复/取消联动计划、定时任务与复盘 job；任意工单 ready/blocked/failed/需决定的 waiting 在无可见页面时推 Web Push（同工单 10 分钟一次）；目标列表/详情/新建页。

**Architecture:** G1 枚举/错误码/设置 + 表/迁移/仓储（T1+T2）→ G2 GoalService（T3）→ G3 事件与复盘规则 + 汇报监听/推送（T4+T5）→ G4 目标工具/策略/总控定义（T6）→ G5 REST 端点与装配（T7）→ G6 前端（T8）→ G7 冒烟与评测（T9）→ G8（我：文档、验收、合并 tag）。

**Spec:** [m82-background-goals](../specs/m82-background-goals.md)（606 行）

## 开工基线（2026-11-05）

- 分支 `background-goals`，基点 `dev` = `561ef13`（M81 合并）；tag `v3-m81`；迁移 head `0034_triggers`，下一号 `0035`。
- §0 1–14 符合：M81 事件服务/登记表/规则常量在；建单入口已有 `source_trigger_id`；`TicketService` 有 `create`/`attach_worker_session`/`update_result`；`planner` 带养成计划工具；总控 v5；`UpgradePlan.id` 为 str36；养成计划服务 `start/list_plans/refresh/cancel`；`ScheduleService.get/patch/delete`；`WebPushService.broadcast`、`ConnectionManager.has_visible_client`；工具 100/99；v2 错误码 `AGENT_DISABLED`/`SCHEDULE_CRON_INVALID` 在。基线见台账（预期 3 条真资源失败）。
- **已确认（用户 2026-11-05 选择「按 spec」）**：
  1. 规划工单用**显式委托**（`strength=explicit`，可发起需要确认的操作）——与 V3-D14「目标推断」不同，记 13 §8。
  2. 内置规则 `goal-review` 默认 **auto**（可自动建复盘工单、花研判调用）。
- **接缝裁决（待落地后记 13 §8.132–8.142）**：
  1. `ThreadRouter`/`TriggerService`/汇报监听都要求真实服务；测试用真实实例接临时库。
  2. 汇报落库入口实际是 `TicketService.record_report`（不是 `update_result`）；在 commit 之后、转发之前对 `_report_listeners` 起后台跟踪任务（异常只日志），加 `add_report_listener`。
  3. `GoalService.get_schedule_service` 惰性取 `app.state.schedule_service`（main 后段才建）。
  4. 拆分 `pytest` 文件按 spec §6；工具注册后计数 104/103；总控定义 version 6。
  5. 盘点：错误码 +2（`GOAL_NOT_FOUND`/`GOAL_ENDED`，162/161）；端点 +4（176，agent 73）；业务表 +2（51）；工具 +4（104/103）；配置键 +2；迁移 head 0035；总控定义 version 6。
  6. 更正 spec 形状：`TicketService.create` 返回 `(Ticket, Mandate)` 二元组（须解包）；`report.human_action` 是 Pydantic 模型（用 `.ref_id`/`.type`，不是 dict）；`TicketService.cancel(ticket_id, reason=...)` 可由服务调用（spec §10 第 3 条风险不成立）。

## Global Constraints

- 不改 v2 养成计划算法/确认卡/完成通知；不改 `planner` 工具名单；不做目标模板/依赖/共享定时任务；推送只走 Web Push；简报属 M84。
- 规划工单用 `strength=explicit`；复盘规则建的工单用 `inferred`、风险上限 `none`（与 V3-D14 一致）。
- `goals.enabled=false`：建目标/复盘都停（`AGENT_DISABLED`）；复盘时间不合法 `SCHEDULE_CRON_INVALID`；目标结束 `GOAL_ENDED`。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m82/`。
- 不 push；提交 `<类型>(m82): <中文摘要>`；最后合并回 `dev` 打 `v3-m82`。

## Review Focus

1. **表/迁移/仓储**：`goal`/`goal_milestone` 列/索引/JSON 默认值；`ticket.goal_id` 循环外键；迁移可逆、降级保 ticket 数据；仓储分页/排序/替换/状态更新。
2. **GoalService**：create 的 cron/时区/显式委托/规划工单/订阅/job；`_apply_plan` 里程碑替换与计划关联（worker_session/notes）；`_apply_review`；复盘 overdue→missed、完成判定（计划完成 + 里程碑）、deadline 只发一次、review_due 去重；暂停/恢复/取消联动（定时任务/计划/目标工单/job）；`GOAL_ENDED`；恢复的消耗确认理由。
3. **事件/推送**：`goal.review_due`/`goal.completed`/`goal.deadline_passed` 登记与 attention；`goal-review` 规则字段/研判提示/草稿；bus 按来源 goal 传 `goal_id`；汇报监听后台任务；推送条件/标签/深链/10 分钟限流/waiting ref 去重。
4. **工具/策略/定义**：四个工具组/风险/描述/返回；`create_goal` 不确认、`update_goal` 恢复需确认（理由带定时任务名，fail closed）；总控 tools/正文/version；计数 104/103。
5. **端点/装配**：四端点/201+Location/错误码/分页；装配顺序与两处 close/delattr；`GET /api/agent/goals` 返回形状。
6. **前端**：列表三分页签、详情（里程碑/进度/工单/notes）、新建（cron 换算/只读 fallback）、暂停/恢复/取消确认与 Toast、空态；`ticket_status` 失效 goals、详情 30s 轮询。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `background-goals`；§0 1–14；两处待确认与 5 项接缝裁决写入本计划；提交计划。

### Task 1（G1）: 枚举/错误码/设置 + 表/迁移/仓储（spec T1+T2）

**Files:** `domain/enums.py`、`tests/domain/test_enums.py`、`domain/errors.py`、`tests/domain/test_errors.py`、`settings.py`、`services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`db/models.py`、新建 `migrations/versions/0035_goals.py`、新建 `db/repositories/goals.py`、`db/repositories/ticket.py`（`list_by_goal`）、`tests/db/test_models.py`、新建 `tests/db/test_goals_migration.py`、`docs/v3/14`（错误码 162/161）

- [ ] `GoalStatus`/`MilestoneStatus`；两错误码；`GoalsSettings` 两键 + 模板 + 测试；`goal`/`goal_milestone` 表 + `ticket.goal_id`；迁移 0035（可逆/离线/数据保留）；两仓储 + 工单 `list_by_goal`。
- [ ] 验证：`pytest tests/db tests/domain tests/test_settings.py tests/test_vapid_settings.py` → 0；`alembic upgrade head --sql | grep -c goal_milestone` ≥1。
- [ ] 提交：`feat(m82): 目标枚举、两表、迁移与设置`

### Task 2（G2）: GoalService（spec T3）

**Files:** 新建 `agent/goals/{__init__,models,service}.py`、`tickets/service.py`（建单加 `goal_id`）、新建 `tests/agent/goals/{__init__,test_service}.py`

- [ ] `models.py` 全部 Pydantic 模型/响应模型；`service.py` 的 create/list/get/update/resume_consumption_reasons/on_ticket_report/review/start_jobs/close 与 `_apply_plan`/`_apply_review`/`_snapshot`/`_is_complete`/`_complete`/暂停恢复取消/`planning_brief`/`planning_scope`。
- [ ] 验证：`pytest tests/agent/goals/test_service.py` → 0（§6 十二条）。
- [ ] 提交：`feat(m82): 目标服务与里程碑状态机`

### Task 3（G3）: 事件/复盘规则 + 汇报监听/推送（spec T4+T5）

**Files:** `agent/events/{kinds,builtin_rules,bus}.py`、`agent/tickets/service.py`（`add_report_listener` + 后台任务）、新建 `agent/tickets/push.py`、新建 `tests/agent/events/test_goal_events.py`、新建 `tests/agent/tickets/{test_push,test_report_listeners}.py`、`tests/agent/events/test_builtin_rules.py`（名单同步）

- [ ] 三个 goal 事件登记（completed/deadline_passed attention 恒真）；`goal-review` 内置规则（auto/planner/研判提示/`goal_review_draft`）；bus 按 `row.source=="goal"` 传 `goal_id`；report listeners；`TicketPushNotifier`（条件/载荷/限流/ref 去重）。
- [ ] 验证：`pytest tests/agent/events tests/agent/tickets` → 0。
- [ ] 提交：`feat(m82): 目标事件、复盘规则与后台推送`

### Task 4（G4）: 目标工具/策略/总控定义（spec T6）

**Files:** 新建 `agent/tools/goals.py`、`tools/__init__.py`、`agent/policy.py`、`agent/specs/builtin/orchestrator.md`、`tests/agent/test_tool_catalog.py`、新建 `tests/agent/goals/test_tools.py`、`tests/agent/test_registry_policy.py`、`tests/agent/specs/*`

- [ ] 四个工具（组 goals/风险/描述/返回）；`create_goal` 无需确认、`update_goal` 恢复确认（fail closed）；总控 tools+正文+version 6；工具 104/103。
- [ ] 验证：`pytest tests/agent/test_tool_catalog.py tests/agent/goals tests/agent/test_registry_policy.py tests/agent/specs` → 0。
- [ ] 提交：`feat(m82): 目标工具、策略与总控定义`

### Task 5（G5）: REST 端点与装配（spec T7）

**Files:** 新建 `api/routers/goals.py`、`main.py`、新建 `tests/api/test_goals_router.py`、`tests/api/test_agent_assembly_wiring.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`（路由 176/agent 73）

- [ ] 四端点（201+Location/错误码/分页/时间 Z）；装配 `goal_service`/`ticket_push_notifier`/report listeners/jobs/两处 close+delattr；14 号文档；OpenAPI/类型。
- [ ] 验证：`pytest tests/api/test_goals_router.py tests/api/test_agent_assembly_wiring.py tests/domain/test_route_inventory.py` → 0；`dump_openapi --check` 0。
- [ ] 提交：`feat(m82): 目标端点与装配`

### Task 6（G6）: 前端（spec T8）

**Files:** 新建 `web/src/features/goals/**`、`router.tsx`、`nav.ts`、`api/keys.ts`、`realtime/query-bridge.ts` + 测试

- [ ] 列表/详情/新建；暂停/恢复/取消确认与 Toast；cron 换算/只读 fallback；空态；`ticket_status` 失效 goals、详情 30s 轮询。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` `SMOKE OK`。
- [ ] 提交：`feat(m82): 目标页与新建目标`

### Task 7（G7）: 冒烟与评测（spec T9）

**Files:** `scripts/orchestrator_smoke.py`、`tests/evals/scenarios/*`、`tests/evals/cassettes/*`

- [ ] 冒烟 `[ok] goals`（建目标→规划工单→写 goal_plan 汇报→里程碑→review→`goal.review_due`）；regression `orchestrator_create_goal`（手写 cassette）。
- [ ] 验证：smoke 末行 `SMOKE OK`；`agent_eval --suite regression --mode replay` → 0；capability 不低于基线。
- [ ] 提交：`test(m82): 目标冒烟与评测场景`

### Task 8（G8，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,03,04,05,08,11,13,14,16,README}`、新建 `docs/v3/ledgers/m82-background-goals.md`

- [ ] 02 §2.2/§4.1/§5 装配；03 §6.2 strength 说明与 §11.4/§13；04 M82 四行与错误码；05 工具/§8 汇报结构；08 事件/推送同步；11 目标页；13 §8.132–8.142；16 goals 键；README；台账（含两处待确认的处理结果）。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、三冒烟、regression/capability、断链、diff；手动项记「未验证」。
- [ ] 提交 `docs(m82): 台账、契约同步与状态行`；`git merge --no-ff background-goals` → tag `v3-m82` → 删分支 → 合并后复验 → 汇报。
