# 移除消耗护栏与旧会话查看器（维护性改造规格）

> 非里程碑补丁：按维护性改造处理，不占用新里程碑编号，不调整 [12-实施路线](../12-实施路线与里程碑.md) 的顺序。
> 本文是本次改造的唯一规格；实施偏离与裁决记在 [13 §8.175](../13-决策记录.md)。
> 产品口径经用户 2026-11-09 确认：**两块都彻底删除**——前端入口 + 后端服务/端点/配置/账本表，以及旧会话的 4 个 v2 只读端点。消耗类与破坏类的逐次确认、确认冒泡、会话授权是 v2 红线，**不随本次删除**。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支与工作区 | `git status -sb` | `## remove-spend-legacy`，工作区干净 |
| 2 | 消耗护栏只有一处实现 | `grep -c "class SpendGuard" maa_api/agent/spend.py` | `1` |
| 3 | 账本只有一处写入 | `grep -rn "SpendLedger(" maa_api --include=*.py \| grep -v __pycache__` | 只命中 `maa_api/agent/spend.py` |
| 4 | 消耗端点与路由在 | `grep -n "agent/spend" maa_api/api/routers/spend.py` | 至少 1 行 |
| 5 | 旧会话页面在 | `ls web/src/features/agent/LegacySessionsPage.tsx` | 存在 |
| 6 | v2 只读端点四个 | `grep -n "async def list_sessions\|async def get_session_detail\|async def delete_session\|async def list_session_messages" maa_api/api/routers/agent.py` | 四个函数 |
| 7 | 迁移 head | `git ls-tree HEAD maa_api/db/migrations/versions/ \| tail -3` | 最新是 `0038_channels.py` |
| 8 | 生成物检查入口 | `grep -n '"gen:api' web/package.json` | 有 `gen:api` 与 `gen:api:check` |
| 9 | 旧会话没有第二消费者 | `grep -rn "api/agent/sessions" web/src --include=*.ts --include=*.tsx \| grep -v types/api.d.ts` | 只命中旧会话查看器与其测试、仓库/计划/压缩等 v3 子路径 |

## 1. 背景与目标

v3 已按 M84 与 M72 实现两块能力：`SpendGuard` 按日限制理智药、源石、加急许可三类消耗（超额即使确认也拦，V3-D26），前端有 `/more/spend` 页、指挥台「待你处理」汇总行与简报「消耗」小节；M72 把 v2 Agent 页改成 `/more/sessions` 旧会话只读查看器，配合 4 个 v2 只读端点。用户 2026-11-09 决定两块都不要了。

做完后：

1. `SpendGuard`、`spend_ledger`、三个每日上限配置键、`GET /api/agent/spend`、`SPEND_CAP_EXCEEDED` 全部消失；流水线入队/改参/预检/Worker 中间件不再有消耗上限拦截。
2. 每日简报与 `POST /api/agent/brief/run` 保留；简报里「消耗（按任务参数上限计）」小节与事实字段删除，其它小节与 `meta` 键不变。
3. `/more/sessions` 页面、`/agent` 深链重定向、导航项、指挥台标题栏「旧会话」入口，以及 4 个 v2 只读端点（会话列表、会话详情、删除会话、消息分页）全部消失；归档总控会话、v3 前旧 chat 与 v2 诊断会话的数据**留在库里**，不再有界面与接口入口。
4. 逐次确认、确认冒泡、`DELETE /api/agent/sessions/{id}/atomic-grant` 与 `DELETE .../copilot-grant`、成本账本与成本页、每日简报表身、诊断工单、压缩/回到过去/计划等 v3 会话端点一个不动。
5. 现行文档、接口清单、OpenAPI 快照、前端生成类型、错误码指南与冒烟脚本同步；历史 spec/计划/台账保留为记录。

## 2. 非目标

- 不改消耗类/破坏类的判定口径（`RISK_FIELDS`）、`CONFIRMATION_REQUIRED`/`CONFIRMATION_EXPIRED`、会话级授权与确认冒泡。
- 不删 `cost_ledger`、`/more/costs`、简报的「模型花费」行、预算中间件。
- 不删数据库里的 `agent_session`/`agent_message` 历史行、`agent_audit` 历史行（含历史 `SPEND_CAP_EXCEEDED` 拦截审计）；不新增清理数据的迁移。
- 不删历史文档：`docs/v3/specs/m84-brief-spend.md`、`docs/v3/plans/m84-brief-spend.md`、`docs/v3/ledgers/m84-brief-spend.md`、M72 的 spec/plan/台账一行不动，只在新决策记录里指向它们。
- 不动 v2 事件协议与 `legacy_turn`/`legacy_incident` 回放（evals 与测试仍用）；只删查看器与前端只服务查看器的缓存失效。
- 不顺手做别的简化（不改 `/api/agent/sessions/{id}/compact|rewind|plan` 等 v3 端点，不动 M73 诊断工单链路）。

## 3. 相关文档与代码

- 口径文档：[09 §6 消耗护栏](../09-安全与治理.md)、[11 §8 旧会话只读查看器](../11-前端指挥台.md)、[12](../12-实施路线与里程碑.md) 的 M72/M84 行、[14 现行接口清单](../14-现行接口清单.md)、[16 部署指南](../16-部署指南.md) 的消耗上限段。
- 历史规格：[m84-brief-spend](../specs/m84-brief-spend.md)、[m72-console-frontend](../specs/m72-console-frontend.md)、[13 §8.153–8.162](../13-决策记录.md) 与 V3-D26。
- 既有代码入口（详见 §4）：
  - 后端消耗护栏：`maa_api/agent/spend.py`、`maa_api/api/routers/spend.py`、`maa_api/db/models.py`（`SpendLedger`）、`maa_api/db/migrations/versions/0037_spend_ledger.py`、`maa_api/domain/enums.py`（`SpendKind`）、`maa_api/domain/errors.py`（`SPEND_CAP_EXCEEDED`）、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/main.py`、`maa_api/services/queue_service.py`、`maa_api/services/pipeline_runner.py`、`maa_api/agent/tools/pipeline.py`、`maa_api/agent/workers/middleware.py`、`maa_api/agent/brief/{facts,render}.py`。
  - 前端消耗护栏：`web/src/features/spend/`、`web/src/features/console/{attention.ts,use-attention.ts,AttentionList.tsx,test-utils.tsx}`、`web/src/features/settings/{BriefControl.tsx,sections.ts}`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/api/keys.ts`。
  - 旧会话：`web/src/features/agent/LegacySessionsPage.tsx` 及其测试、`SessionList.tsx`、`use-message-window.ts`、`use-conversation-scroll.ts`、`types.ts`、`format.ts`、`messages.ts`、`stream.ts`、`StatusAlerts.tsx`、`MessageViews.tsx`（逐个确认是否被指挥台/工单共用）；`web/src/routes/router.tsx`、`web/src/routes/more/*`、`web/src/components/layout/{nav.ts,layout.test.tsx}`、`web/src/features/console/ConsolePage.tsx` 标题栏菜单、`web/src/realtime/query-bridge.ts`、`web/src/api/keys.ts`；后端 `maa_api/api/routers/agent.py` 的四个 v2 只读端点与 `AgentSessionRepository.list_page(legacy_only=True)`。
- 契约与生成物：`web/openapi.json`、`web/src/types/api.d.ts`、`scripts/dump_openapi.py`、`scripts/generate_api_guide.py`、`tests/domain/test_route_inventory.py`。
- 冒烟：`scripts/brief_spend_smoke.py`、`scripts/agent_smoke.py`、`scripts/frontend_smoke.py`。

## 4. 设计

### 4.1 消耗护栏的拆除

生产代码里三类调用点，全部**删除而不是留 `guard=None` 的可选分支**；删完后调用方回到没有守卫时的形状：

1. **入队预留**（`QueueService`）：准入事务里判定并写 `spend_ledger` 的代码块删除；`_admission_lock` 若只服务预留则一并删除，否则保留原锁序。删后入队不再因消耗上限返回 `SPEND_CAP_EXCEEDED`。
2. **运行中改参**（`PipelineRunner`）：`set_task_params` 路径的差额预留删除，`operation_lock` 与其它行为不变。
3. **确认前预检**（`submit_pipeline`/`set_task_params` 工具）：只读预检调用删除；工具描述与提示词里「消耗上限」相关表述若存在则同步删。
4. **Worker 中间件**：`SpendCapMiddleware`（或同名类）从 `build_agent_chain` 链上摘除并删除类定义；链序号断言与注释同步。

数据与配置：

- 新增迁移 `0039_drop_spend_ledger`：`upgrade` 删索引 `ix_spend_ledger_kind_created_at` 与表 `spend_ledger`；`downgrade` 按 0037 的定义重建表与索引（保留可回滚）。`0037_spend_ledger.py` 与迁移链不动。
- 模型 `SpendLedger`、枚举 `SpendKind`、错误码 `SPEND_CAP_EXCEEDED`（含 `ERROR_HTTP_STATUS` 行）、`SpendSettings` 与 `spend.daily_*` 三个配置键、`settings_schema` 三行、`config.template.yaml` 的 `spend:` 段、`main.py` 的装配与 `app.state.spend_guard`、`bind_spend_guard` 都删除。
- 审计：`record_spend_rejection` 删除；历史 `agent_audit` 行保留。前端若有错误码→中文映射只删该键。

简报：

- `brief/facts.py` 删 `spend_today`/`spend_yesterday` 字段、`_spend_block` 与对 `spend_day_window` 的依赖；「今天/昨天」的日界仍需要 04:00 分日，改用简报自己的常量或迁到 `brief/` 内的纯函数（不 import 待删模块）。
- `brief/render.py` 删 `_spend_section`、`_today_spend_text`、`_SPEND_ORDER` 与 `_SECTION_BUILDERS` 中的一行；其余小节不变。
- `POST /api/agent/brief/run` 保留：`maa_api/api/routers/spend.py` `git mv` 为 `maa_api/api/routers/brief.py`，只留简报端点与 `BriefRunResult`；`main.py` 的 include 与 [02 §4.1](../02-系统架构.md) 的路由清单同步。OpenAPI tag 仍是 `agent`。

接口与契约：

- `GET /api/agent/spend` 删除；`SPEND_CAP_EXCEEDED` 从 14 §4 错误码表删除，[15](../15-开放API接入指南.md) 的错误码表由脚本重生成。
- 前端 `keys.agent.spend`、`features/spend/` 整目录删除；**简报控件保留**：`runBrief`/`useRunBrief` 迁到 `web/src/features/brief/api.ts`，`features/settings/BriefControl.tsx` 改 import；`settings/sections.ts` 的 agent 分区 summary 去掉「与消耗上限」。
- 指挥台「待你处理」删消耗来源：`AttentionItem` 的消耗分支、`use-attention.ts` 的消耗查询、`AttentionList.tsx` 的汇总行、`test-utils.tsx` 的假端点与类型，排序句与 [11 §3.2](../11-前端指挥台.md) 同步。

### 4.2 旧会话查看器的拆除

后端：

- 删除四个只读端点：`GET /api/agent/sessions`（`list_sessions`）、`GET /api/agent/sessions/{id}`（`get_session_detail`）、`DELETE /api/agent/sessions/{id}`（`delete_session`）、`GET /api/agent/sessions/{id}/messages`（`list_session_messages`）。
- 只服务它们的辅助一并删除或以确认无引用后再删：`_v2_session_or_404`、`turn_running` 的读取、`active_copilot_grant_wire`（若只被详情使用）、`session_wire`（若只被列表/详情使用）。`message_wire`、`load_inbox_meta`、`AgentMessageRepository` 若被 v3 消息路由复用则保留，只删 v2 专用参数/调用。
- `AgentSessionRepository.list_page(legacy_only=True)` 的 `legacy_only` 若只服务查看器则删除该参数；有其它调用就保留并改默认口径。
- 保留：`DELETE /api/agent/sessions/{id}/atomic-grant`、`DELETE .../copilot-grant`、`/sessions/{id}/compact|checkpoints|plan|plan/approval|rewind`、`/orchestrator/*`、`/tickets/{id}/messages`。
- `reset_orchestrator` 的归档写入不变（归档会话仍写库），只是没有读取入口。

前端：

- 删 `/more/sessions` 路由与 `LegacySessionsPage`；`/agent` 深链重定向改指 `/`（指挥台）并保留 `?session_id=` 的丢弃行为，避免旧书签 404——这是本规格的默认裁决，实施者可按路由现状调整。
- 删导航项「旧会话」、指挥台标题栏操作菜单的「旧会话」项、「重新开始」确认文案里「只能在旧会话里查看」改为「会归档」；不再有「会话列表」手机面板。
- `features/agent/` 下只服务查看器的文件删除；被指挥台/工单/对话组件共用的（如 `Composer.tsx`、`Markdown.tsx`、`MessageViews.tsx` 等）保留，注释里的「旧会话查看器」字样同步；删除后 `pnpm typecheck` 不得有未使用导出报错。
- 实时层：删 `keys.agent.sessions`/`keys.agent.session` 与 `query-bridge.ts` 中对应失效分支（若这些事件在 v3 已无消费者）；`realtime/client.test.ts` 同步。v2 事件协议本身不动。
- 指挥台「待你处理」的确认/提问来源行：`features/console/attention.ts` 的 `originLabel` 在「无 origin 且调用方不是 REST」时返回「来自旧会话」。v2 会话在 M72 已无写入口、M73 后诊断也不再起 v2 运行，这条只能来自已不可产生的旧数据；改成「来自后台运行」并同步 [11 §3.2](../11-前端指挥台.md)、`attention.test.ts` 与 `ConsolePage` 相关断言。`features/agent/origin.ts` 的另一份 `originLabel` 只认 origin，不动。
- 冒烟：`scripts/frontend_smoke.py` 删 `/more/sessions`、`/agent` 深链检查；`scripts/agent_smoke.py` 删旧会话列表/消息检查与 `POST /__smoke/legacy-sessions` 路由。

### 4.3 前端「消费上限」相关页面

- `/more/spend` 页、导航项、路由、`features/spend/` 测试全部删除；`styles` 无相关全局类。
- [11](../11-前端指挥台.md) 的路由表、管理页清单、移动端布局、文案表删「旧会话」「消耗护栏」两行；导航分组 `agent` 的说明同步。

### 4.4 契约与生成物

1. 后端改完后：`.venv/bin/python scripts/dump_openapi.py`（重新生成快照）；`cd web && pnpm gen:api`（重新生成 `types/api.d.ts`）。
2. `.venv/bin/python scripts/generate_api_guide.py` 重新生成 15 的错误码与 tag 表。
3. `docs/v3/14-现行接口清单.md`：删 `GET /api/agent/spend` 与四个 v2 会话端点行、`SPEND_CAP_EXCEEDED` 行；错误码总数 169 → 168，HTTP 端点总数 187 → 182，agent 小节「（84 条）」→「（79 条）」；「v2 会话只读（M72）」说明段改写。
4. `tests/domain/test_route_inventory.py`、`tests/api/test_app_skeleton.py`（tag 数断言，若涉及）等清单断言同步。
5. `web/openapi.json` 与 `web/src/types/api.d.ts` 纳入提交；三处 `--check` 全 0。

### 4.5 文档同步口径

| 文档 | 改动 |
|---|---|
| [01-总览与范围](../01-总览与范围.md) | §6 里程碑能力表「每日简报、消耗预算护栏」改成「每日简报」（消耗护栏删除说明见 13 §8.175）；§9 红线一句若提消耗上限则删 |
| [02-系统架构](../02-系统架构.md) | §2.2 服务表删 `SpendGuard` 行；§4.1 文件树删 `spend.py`、路由清单 `spend.py`（M84）改 `brief.py`（M84 简报）；§4.2 目录说明「agent/ v2；M72 改成只读的旧会话查看器」删；§8 装配顺序删 `spend_guard` |
| [03-数据模型](../03-数据模型.md) | §8 迁移历史表 `0037_spend_ledger` 行标注「0039 已删表」并加 0039 行；§11.6 `spend_ledger` 整节删；表清单/索引清单同步 |
| [04-接口与事件协议](../04-接口与事件协议.md) | §2.2 删 M72 的 `GET /sessions`、`GET /sessions/{id}/messages` 两行与说明；§2.4 删 M84 `GET /api/agent/spend` 行；§5 错误码删 `SPEND_CAP_EXCEEDED` 行；§6 配置表删三个 `spend.daily_*` 行；§2 开头「旧会话改为只读查看器」句改写 |
| [05-运行时与代理定义](../05-运行时与代理定义.md) | 全文搜「旧会话查看器」与「消耗护栏」，只改提到查看器/上限的句子；legacy 定义回放口径不动 |
| [09-安全与治理](../09-安全与治理.md) | 删 §6 整节；§1.2 风险清单与 §12 威胁模型表删「消耗护栏/每日上限」行；§5 若引用 §6 改成一句话「消耗上限已按用户要求删除，逐次确认不变」 |
| [10-可观测与评测](../10-可观测与评测.md) | 只改提到「消耗护栏」的句子；第 307 行「旧会话」指 v2 会话回放，**保留** |
| [11-前端指挥台](../11-前端指挥台.md) | §1 两处（开篇与「一个连续的总控会话」）、§3 分组说明、§3.2 消耗汇总行与排序句、§4 标题栏三项改两项、路由表 `/more/sessions` 与 `/more/spend` 两行、§8 整节、§9 管理页两行、§10 移动端「旧会话」句、§11 文案表相关行 |
| [12-实施路线与里程碑](../12-实施路线与里程碑.md) | M72 行补「旧会话查看器已删除（13 §8.175）」；M84 行改成「每日简报（消耗护栏部分已删除，见 13 §8.175）」；§4 验收命令里 `brief_spend_smoke.py` 改名同步 |
| [14-现行接口清单](../14-现行接口清单.md) | 见 §4.4；另删 R1 文档里对 `SPEND_CAP_EXCEEDED` 的说明 |
| [15-开放API接入指南](../15-开放API接入指南.md) | 由 `scripts/generate_api_guide.py` 重生成错误码与 tag 表（删错误码后必须重跑） |
| [16-部署指南](../16-部署指南.md) | §20 标题改「每日简报（M84）」并删「消耗上限」整段；§5 配置说明如提 `spend.*` 同步 |
| [README](../README.md) | 决策速查表「主动性、通道与安全」小节删「消耗护栏」行；其它表若提旧会话则改 |
| [13-决策记录](../13-决策记录.md) | 新增 §8.175；§9 待拍板 Q4（消耗上限默认值）标注「已作废：功能删除」 |

- 历史 spec/plan/台账不动；`docs/v3/ledgers/` 新增本次台账。
- 断链检查：`.venv/bin/python scripts/check_doc_links.py` 末行 `BROKEN 0`。

### 4.6 工作拆分与顺序

一次补丁、单分支、单次合并回 dev；内部按写范围分三个互不重叠的工作流并行：

1. **后端消耗护栏**：`maa_api|tests|scripts` 里消耗护栏相关文件（含迁移）。
2. **后端旧会话**：`maa_api/api/routers/agent.py`、仓储与对应测试、`scripts/agent_smoke.py`。
3. **前端**：`web/src` 里两块前端与测试、`scripts/frontend_smoke.py`。

顺序约束：后端完成 → 重生成 OpenAPI 与前端类型 → 前端 typecheck 收口；文档在代码稳定后由主 agent 统一改并跑断链。三个工作流的文件清单在实施计划里逐任务列全。

## 5. 实施任务

| # | 任务 | 写范围 | 完成判据 |
|---|---|---|---|
| T1 | 后端消耗护栏生产代码拆除 | `maa_api/`（除旧会话文件） | `grep -rn "SpendGuard\|spend_guard\|SpendLedger\|SpendKind\|SPEND_CAP_EXCEEDED\|spend.daily_" maa_api --include=*.py` 只剩历史迁移 0037 |
| T2 | 迁移 + 模型 + 配置 | `maa_api/db/`、`settings.py`、`settings_schema.py`、`config.template.yaml`、`tests/db/`、`tests/test_settings.py` | `0039_drop_spend_ledger` 升降级测试通过；配置 schema 无 spend 键 |
| T3 | 后端消耗测试清理与研究 | `tests/`、`scripts/brief_spend_smoke.py` | 相关测试整删/改写；简报冒烟通过 |
| T4 | 后端旧会话端点拆除 | `maa_api/api/routers/agent.py`、`db/repositories/agent.py`、`tests/api/`、`scripts/agent_smoke.py` | 四个路径不在 `set(app.openapi()['paths'])`；保留端点仍测 |
| T5 | 前端两块删除 + 简报控件迁移 | `web/src/`（见 §4.6）、`scripts/frontend_smoke.py` | typecheck / test / build 通过；`grep` 无残留 |
| T6 | 契约生成物与接口清单 | `web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`、`scripts/` 生成脚本 | 三处 `--check` 为 0 |
| T7 | 文档同步与台账 | `docs/v3/`（除历史 spec/plan/ledger） | 断链 `BROKEN 0`；13 §8.175 与台账入库 |
| T8 | 全量验证与合并 | 全仓 | §7 全绿；`git merge --no-ff` 回 dev |

## 6. 测试清单

后端（整删或改写，具体文件以实施计划为准）：

| 文件 | 处理 |
|---|---|
| `tests/agent/test_spend.py`、`tests/api/test_spend_router.py`、`tests/api/test_agent_invoke_spend.py`、`tests/services/test_queue_spend.py`、`tests/services/test_schedule_spend.py`、`tests/agent/harness/test_spend_middleware.py`、`tests/db/test_spend_ledger_migration.py` | 整删；新增 `tests/db/test_drop_spend_ledger_migration.py` 验证升降级 |
| `tests/agent/brief/test_facts_brief.py`、`test_render_facts.py` | 删消耗小节用例，其余不变 |
| `tests/agent/workers/test_worker_middleware.py`、`tests/agent/test_pipeline_tools.py`、`tests/agent/test_pipeline_runner_set_params.py` | 删上限拦截用例；保留确认/改参行为 |
| `tests/api/test_agent_router.py` | 删四个只读端点用例；保留授权撤销与 v3 消息用例 |
| `tests/domain/test_route_inventory.py`、`tests/api/test_app_skeleton.py`、`tests/test_settings.py`、`tests/db/test_models.py`、`tests/api/test_agent_assembly_wiring.py`、`tests/core/test_supervisor.py` | 同步计数/清单/装配断言 |
| `scripts/brief_spend_smoke.py` | `git mv` 为 `brief_smoke.py`，只验简报与 `POST /api/agent/brief/run` |
| `scripts/agent_smoke.py`、`scripts/frontend_smoke.py` | 删两块检查 |

前端：

| 文件 | 处理 |
|---|---|
| `web/src/features/spend/`（3 文件 + 测试） | 整删；`useRunBrief` 迁到 `features/brief/api.ts` |
| `web/src/features/console/attention.test.ts`、`use-attention.ts`、`AttentionList.tsx`、`test-utils.tsx` | 删消耗来源用例/查询/渲染/假端点 |
| `web/src/features/agent/LegacySessionsPage.*` 与只服务它的小文件 | 整删；共享组件保留 |
| `web/src/routes/{router.tsx,more/more.test.tsx}`、`components/layout/{nav.ts,layout.test.tsx}`、`features/console/ConsolePage.test.tsx` | 删路由/导航/标题栏/文案断言 |
| `web/src/realtime/{query-bridge.ts,query-bridge.test.ts,client.test.ts}`、`api/keys.ts` | 删 v2 会话失效分支与 keys |
| `web/src/features/settings/{BriefControl.tsx,sections.ts}` 与设置测试 | 改 import、删「消耗上限」文字 |

## 7. 验收

在 dev 分支上、所有写者停止后由主 agent 独占复跑：

1. 后端：`.venv/bin/python -m pytest -o addopts= -q > /tmp/remove-spend-legacy-pytest.log 2>&1; echo $?; tail -n 3 /tmp/remove-spend-legacy-pytest.log` → 0（已知 flake 单独记录）。
2. 生成物：`.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` → 均 0。
3. 前端：`cd web && pnpm typecheck && pnpm test --run && pnpm build` → 0；`pnpm test --run` 的结果行记录文件数/用例数。
4. 冒烟：`.venv/bin/python scripts/agent_smoke.py`、`.venv/bin/python scripts/brief_smoke.py`、`cd web && pnpm build` 后 `.venv/bin/python scripts/frontend_smoke.py`、`.venv/bin/python scripts/orchestrator_smoke.py` → 末行均 `SMOKE OK`。
5. 文档：`.venv/bin/python scripts/check_doc_links.py` → 末行 `BROKEN 0`。
6. 残留检查：`grep -rn "SpendGuard\|spend_ledger\|SPEND_CAP_EXCEEDED\|daily_medicine_limit\|/more/spend\|/more/sessions\|LegacySessions" maa_api web/src scripts tests docs/v3 --include=*.py --include=*.ts --include=*.tsx --include=*.md | grep -v __pycache__` → 只允许历史迁移、历史 spec/plan/台账、13 §8.175 与本次台账命中。
7. 文案基线：`scripts/copy_inventory.py --check`；若报回潮按 v2 口径 `--write-baseline` 重设并提交 `docs/copy-audit/`。
8. `git status` 干净后合并回 dev：`git merge --no-ff remove-spend-legacy`。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 入队事务里删预留代码破坏锁序/事务边界 | 保留 `_admission_lock` 的获得与提交时序；测试覆盖并发入队与取消；review 时逐行看 `async with` 缩进 |
| Worker 中间件链序号断言遗漏 | `grep -rn "spend" tests/agent` 清零；链构造测试与运行时测试双向验证 |
| 迁移 downgrade 与 0037 不一致 | downgrade 按 0037 的 DDL 原样重建；迁移测试 upgrade→downgrade→upgrade 一轮 |
| 旧会话端点被 v3 复用（`session_wire`/`message_wire`/`load_inbox_meta`） | 删除前 `grep` 每个符号的调用点，只删只服务 v2 的；v3 消息测试不得改口径 |
| 前端删出未使用导出/共享组件误删 | `pnpm typecheck` + 全量 vitest；`features/agent/` 每个文件先 grep import 再删 |
| 生成物不同步 | 顺序固定：后端 → dump_openapi → gen:api → 前端收口；三处 `--check` 兜底 |
| 文案基线回潮 | 按 v2 口径重设并提交，不改既有文案口径 |

回滚：本补丁在独立分支，未合并前 `git switch dev` 即可；合并后回滚用 `git revert` 单个合并提交。迁移 `0039` 的 downgrade 可恢复 `spend_ledger` 表结构，但**不恢复已删数据**（迁移 upgrade 时表被删，历史账本不保留）。

## 9. 文档同步

见 §4.5。另：`docs/v3/ledgers/remove-spend-legacy-sessions.md` 记录分支、提交、裁决与验证证据；[13 §8.175](../13-决策记录.md) 与台账同提交入库。

## 10. 停下来问

出现以下情况停下来问用户，不自行裁决：

1. 发现四个 v2 只读端点或 `GET /api/agent/spend` 有查看器/页面之外的消费者（外部脚本、其它前端页、`docs/v3/15` 的示例）。
2. 删除 `spend_ledger` 之外还要删别的表或列；或迁移需要丢弃用户数据。
3. 简报去掉消耗小节后与 `m84` 历史规格的其它口径冲突，导致要改简报的对外行为。
4. 旧会话前端共享组件的归属无法靠 import 判断，删了会影响指挥台/工单/设置。
