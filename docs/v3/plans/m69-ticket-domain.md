# M69 工单领域模型 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建工单层的数据与服务：迁移 `0026_tickets` 建 `ticket`/`mandate`/`ticket_report`/`thread_subscription`/`agent_inbox` 五表并给 `agent_session`/`agent_run`/`agent_message` 加外键列；七个枚举与两个错误码；`TicketService`/`ThreadRouter`/`InboxService` 三个服务；三个只读工单端点；`ticket_status` WebSocket 频道；收件箱保留与重启恢复。

**Architecture:** T1 枚举/错误码 → T2 模型与迁移 → T3 仓储与测试建库工具 → T4 委托/汇报/话题键纯函数 → T5 InboxService 与 ThreadRouter → T6 TicketService → T7 REST 端点 → T8 实时通道与前端 → T9 保留、恢复与装配 → T10 全量验收与合并。

**Tech Stack:** SQLModel + Alembic（0026，batch 重建三张已有表）、pydantic 响应模型、pytest、React realtime。

**Spec:** [m69-ticket-domain](../specs/m69-ticket-domain.md)（899 行；每个任务照 spec 对应小节做）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `ticket-domain`，基点 `dev` = `94fc14d`（M68 合并）；tag `v3-m63`…`v3-m68`；迁移 head `0025_trace_spans`。
- `AgentRun` 表与三个枚举在位；0023 已用 `batch_alter_table` + `copy_from` 给已有表加外键列（T2 照抄写法）；`AgentSpecRegistry.get` 返回对象带 `risk_ceiling`；`main.py` 已挂 `agent_spec_registry` 与 `span_recorder`；`RunRecovery` 有 `recovered_after_restart`。
- 工单相关名字都不存在；`docs/v3/14` 有 `RUN_NOT_FOUND`。
- 后端基线：`3 failed, 4208 passed, 5 skipped, 2 xfailed`（3 条真资源用例既有失败；本里程碑不新增失败）。
- **裁决（主 agent）**：`03 §6.2` 的 `mandate.source` 列宽按 spec §4.1/§11 从 str24 改为 **str16**（`test_string_columns_declare_documented_widths` 把名为 `source` 的列钉死 16 位，`EventSource` 最长取值 11 字符）。T2 用 16，T10 改 03 并在 13 §8 记一条。其余按 spec。

## Global Constraints

- **不改 v2 行为**：M65 golden parity 继续 `17 passed`；v2 SSE/事件/测试断言不改；`agent_eval --suite regression --mode replay` 必须继续 6/6 `EVAL OK`。
- **不改公开契约**：只加 spec 列的端点/错误码/表/枚举/频道；工具数 84/83 不变；`mandate` 不可变（仓储不提供 update/delete/set 方法）。
- **迁移可逆**：`0026_tickets` 的 downgrade 回到 `0025_trace_spans`，三张已有表重建后列/索引/外键回原状，离线渲染可过。
- **并发**：收件箱领取必须两个实例并发 20 次都不相交且并集完整；「一个会话至多一个活动运行/一个活动工单」以数据库唯一约束为准。
- 不 push；提交 `<类型>(m69): <中文摘要>`；最后合并回 `dev` 打 `v3-m69`。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，命令行不加 `-q`；长输出重定向 `/tmp/m69/`。

## Review Focus

1. **迁移正确性**：五张新表 + 三张已有表的 batch 重建（`copy_from` 列清单、索引、外键 ondelete）；`test_initial_migration.py` 与 `tests/db/test_models.py` 的登记表全绿；降级到底再升级。
2. **工单状态机**：`TicketService` 的建单/委托/汇报校验/状态映射/结束回调；边界情况表逐行有用例；`TicketEnded` 监听、`cancel`/`await_human`/`human_resolved`。
3. **并发与不可变**：`MandateRepository` 无更新方法；`InboxService.claim` 并发用例连跑 20 次通过；工单结束后的写入返回 `TICKET_ENDED`。
4. **话题路由**：`parse_thread_key` 的两种前缀、`THREAD_KEY_PATTERNS`、`SOURCE_PREFIXES`、路由结果与订阅；未知话题的降级。
5. **装配与恢复**：lifespan 第 6 步拿到三个服务；重启恢复把被中断运行所属工单改 `blocked` 并补 `forwarded=false` 的汇报；`ticket_status` 频道前后端一致。

---

### Task 0: 分支、§0 前置检查与计划入库（已完成）

- [x] 建分支 `ticket-domain`，跑 §0 的 11 条检查；`mandate.source` 宽度按上面裁决。
- [x] 提交本计划：`docs(m69): M69 工单领域模型实施计划`。

### Task 1: 枚举与错误码（spec T1）

**Files:** `maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`

- [ ] 按 §4.1 加七个枚举并在 `test_enums.py` 的 `EXPECTED_VALUES` 同步、加 `test_ticket_status_is_terminal`；`ErrorCode` 加 `TICKET_NOT_FOUND`(404)/`TICKET_ENDED`(409)；`docs/v3/14` §4 加两行与总数 +2；`test_errors.py` 计数各 +2（`SECTION_COUNT` 不变）。
- [ ] 验证：`pytest tests/domain` → 0。
- [ ] 提交：`feat(m69): 工单领域枚举与错误码`

### Task 2: 模型与迁移 0026（spec T2）

**Files:** `maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0026_tickets.py`、`tests/db/test_models.py`、新建 `tests/db/test_tickets_migration.py`

- [ ] 按 §4.1 加五张表与三处 `ticket_id`/`inbox_id` 外键列（`mandate.source` 宽 16）；迁移照 0023 的 `batch_alter_table` + `copy_from` 写法；同步 `tests/db/test_models.py` 的全部登记表；写迁移用例（升级列/类型/可空/外键、级联删 ticket、降级）。
- [ ] 验证：`pytest tests/db` → 0（含 `test_initial_migration.py`）。
- [ ] 提交：`feat(m69): 工单五表与 0026 迁移`

### Task 3: 仓储与测试建库工具（spec T3）

**Files:** 新建 `maa_api/db/repositories/ticket.py`、`maa_api/db/repositories/agent.py`（加 `get_active_orchestrator`）、新建 `tests/fakes/v3_db.py`、新建 `tests/db/test_repositories_ticket.py`

- [ ] 五个仓储（`MandateRepository` 无 update/delete/set 方法）、`get_active_orchestrator`、`v3_db.py` 的三个工具；仓储测试。
- [ ] 验证：`pytest tests/db/test_repositories_ticket.py` → 0。
- [ ] 提交：`feat(m69): 工单仓储与 v3 测试建库工具`

### Task 4: 委托、汇报与话题键纯函数（spec T4）

**Files:** 新建 `maa_api/agent/tickets/{__init__,mandates,reports,threads}.py`、`tests/agent/tickets/{__init__,conftest}.py` 与三个测试文件

- [ ] `mandates.py`（`RISK_ORDER`/`strength_for`/`ceiling_for`/`lower_ceiling`/`ceiling_risk`/`exceeds_ceiling`/`policy_version`/`build_mandate`/`ORIGIN_SOURCES`）、`reports.py`（`HumanAction`/`ReportInput`/`parse_report`/`ticket_status_for`/`should_forward`/`report_inbox_payload` 与两张映射）、`threads.py`（`ThreadKey`/`parse_thread_key`/`THREAD_KEY_PATTERNS`/`SOURCE_PREFIXES`/`RouteResult`）。
- [ ] 验证：`pytest tests/agent/tickets` → 0；`policy_version()` 两次相同且 12 位十六进制。
- [ ] 提交：`feat(m69): 委托、汇报与话题键纯函数`

### Task 5: InboxService 与 ThreadRouter（spec T5）

**Files:** `maa_api/agent/tickets/inbox.py`、`maa_api/agent/tickets/threads.py`（补 `ThreadRouter`）、两个测试

- [ ] `InboxService`（`put`/`claim`/`drop_pending`/`pending_count`/`add_listener`，数据库为准 + 进程内锁）；`ThreadRouter`（`subscribe`/`unsubscribe`/`publish`/`to_orchestrator`/`orchestrator_session_id`）。
- [ ] 验证：`pytest tests/agent/tickets` → 0；并发领取用例连跑 20 次通过。
- [ ] 提交：`feat(m69): 收件箱服务与话题路由`

### Task 6: TicketService（spec T6）

**Files:** `maa_api/agent/tickets/service.py`、`tests/agent/tickets/test_ticket_service.py`

- [ ] `TicketService` 全部方法、`TicketEnded`、`block_tickets_after_restart`；状态机与边界情况逐行覆盖。
- [ ] 验证：`pytest tests/agent/tickets` → 0。
- [ ] 提交：`feat(m69): 工单服务与状态机`

### Task 7: REST 端点（spec T7）

**Files:** 新建 `maa_api/api/routers/tickets.py`、`maa_api/main.py`（import/include）、新建 `tests/api/test_tickets_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`

- [ ] 三个只读端点（列表/详情/汇报）与 Pydantic 响应模型；重生成快照与类型；`docs/v3/14` agent 小节 +3 行与两处计数。
- [ ] 验证：`pytest tests/api/test_tickets_router.py tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m69): 工单只读端点`

### Task 8: 实时通道与前端（spec T8）

**Files:** `maa_api/api/ws.py`、`tests/api/test_ws.py`、`web/src/api/keys.ts`、`web/src/realtime/events.ts`、`web/src/realtime/query-bridge.ts`、`web/src/realtime/query-bridge.test.ts`

- [ ] 后端 `CHANNELS` 加 `ticket_status` + 订阅用例；前端事件类型、query key 与失效规则（三个 key）+ 用例。
- [ ] 验证：`pytest tests/api/test_ws.py` → 0；`cd web && pnpm typecheck && pnpm test --run` → 0。
- [ ] 提交：`feat(m69): ticket_status 实时通道`

### Task 9: 保留策略、重启恢复与装配（spec T9）

**Files:** `maa_api/services/retention_service.py`、`maa_api/agent/runs/recovery.py`、`maa_api/main.py`（lifespan）、`tests/services/test_retention_service.py`、新建 `tests/agent/tickets/test_ticket_recovery.py`、`tests/api/test_agent_assembly_wiring.py`

- [ ] 收件箱保留（`INBOX_RETENTION_DAYS`、`agent_inbox_deleted`）；lifespan 第 6 步装配三个服务；恢复里把被中断运行所属工单改 `blocked` 并补 `forwarded=false` 汇报。
- [ ] 验证：`pytest tests/services/test_retention_service.py tests/agent/tickets tests/api/test_agent_assembly_wiring.py` → 0。
- [ ] 提交：`feat(m69): 工单保留、重启恢复与装配`

### Task 10: 全量验证、文档与合并（spec T10）

**Files:** `docs/v3/03-数据模型.md`（§6.2 `mandate.source` 改 str16）、`docs/v3/13-决策记录.md`（§8 补条）、新建 `docs/v3/ledgers/m69-ticket-domain.md`、`docs/v3/README.md`

- [ ] 跑 spec §7 全部命令；改 03 §6.2；13 §8 记偏离；写台账（每任务证据、Rulings）；README 状态行。
- [ ] 提交：`docs(m69): 契约同步、台账与状态行`；然后 `git merge --no-ff ticket-domain -m "merge: 工单领域模型（M69，见 docs/v3/13 §8.x）"` → `git tag v3-m69` → `git branch -d ticket-domain` → 合并后复验与汇报。
