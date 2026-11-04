# M73 既有场景迁移与 P1 收口（台账）

- 分支：`p1-migration`；基点：`dev` = `fb598eb`；合并：`dev` 上的 `merge: 既有场景迁移与 P1 收口（M73，见 docs/v3/13 §8.51–8.56）`；tag：`v3-m73`
- spec：[m73-p1-migration](../specs/m73-p1-migration.md)（352 行）
- 计划：[m73-p1-migration](../plans/m73-p1-migration.md)
- 决策记录：[docs/v3/13 §8.51–8.56](../13-决策记录.md)
- 本里程碑**无迁移**。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `f9c1a26` | 计划入库；§0 十条检查；6 处接缝裁决（诊断经 WorkerService 建单、去重用 ticket.result、IncidentService 延迟解析、`update_result`、`brief_header`、标签由 specTitles 带出） |
| G1（T1） | `9d4b8c1` + `f54606e` | 六份定义（daily_ops/copilot/planner/diagnose/analyst/ops）与 `test_builtin_workers.py`；九→十五份登记同步；daily_ops 去掉无权 recon 提示 |
| G2（T2） | `2a9d704` | 诊断改建 `diagnose` 工单：`_open_or_append`（开关/去重/追加/建单）、`TicketService.update_result`、`open_ticket(brief_header=)`、`run_incident` 生产调用删除、`IncidentResult.session_id` 填工单 id、test_incidents 重写 + §6 四条 |
| G2b | `84c0c4f` | `tests/services/test_pipeline_runner.py` 6 条旧断言改为 diagnose 工单语义（用例数不变） |
| G3（T3） | `192d683` | orchestrator.md 加「选哪个 Worker」段（version 2）；`test_orchestrator_routing.py` 两例（daily_ops / daily_ops+ops） |
| G4（T4） | `af32294` | `scripts/orchestrator_smoke.py`（orchestrator/ticket/mandate/diagnose/dedupe + `SMOKE OK`）；`agent_smoke.py` 定义 15 份与旧诊断断言改 diagnose 语义 |
| T5 | 本台账所在提交 | 01 §9 备注、13 §8.51–8.56、README、本台账（含 P1 验收记录）；全量验收与合并 |

## P1 验收记录（01 §9 四条）

| 标准 | 如何验证 | 结果 |
|---|---|---|
| 用户在指挥台与总控说话 | 前端 `ConsolePage.test.tsx` 30 例（流式、就地确认/提问、停止与恢复、排队、快捷开场）+ `/api/agent/orchestrator/*` 端点用例 + `orchestrator_smoke.py` 第 1 步 `[ok] orchestrator` | 通过 |
| 总控建工单、Worker 执行、确认冒泡、汇报回到总控 | `test_orchestrator_e2e.py` 7 例（建单→执行→终态唤醒→汇报转述、全双工、并行委派、上限拦截、取消）；M71 冒泡用例；`orchestrator_smoke.py` 的 `[ok] ticket`/`[ok] mandate`；`test_orchestrator_routing.py` 种类选择 | 通过 |
| 服务重启后能恢复 | `tests/agent/runs/test_run_recovery.py`（活动运行收尾）、`tests/agent/tickets/test_ticket_recovery.py`（工单 blocked 补汇报、租约清理）、M71 启动恢复用例 | 通过 |
| v2 的全部业务能力不回退 | `tests/agent` 1065 passed（v2 回归在 62 个里程碑中累积）、`agent_smoke.py` 末行 `SMOKE OK`、`agent_eval regression replay` 6/6、`LegacySessionsPage` 11 例只读查看器用例 | 通过 |

## 接缝裁决（详见 13 §8.51–8.56）

- 诊断工单经 `WorkerService.open_ticket`（否则不会起 Worker）；去重按 `ticket.result.incident_key` + 活动状态，在 `_key_lock` 内查-建；`agent_session.incident_key` 唯一索引不用于诊断（避免旧已结束会话撞键）。
- `TicketService.update_result` 内部方法；`open_ticket.brief_header`（诊断用「自动诊断：」）；`AgentIncidentService` 延迟解析 `app.state` 服务；`IncidentResult.session_id` 填工单 id；`_refresh_existing_pipeline_context` 改写工单收件箱。
- daily_ops 正文删掉无权 recon 委派（05 §3.2 delegates 为准）；工单种类中文标签由 `specTitles` 自动带出，前端零改动。
- `agent_smoke.py` 的旧诊断会话断言与 9→15 定义名单同步更新（只改脚本）。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | `test_builtin_workers.py` 2 passed；`tests/agent/specs + tool_catalog` 63 passed；`tests/agent` 1064 passed |
| G2 | `test_incidents + test_runtime` 99 passed；`tests/agent` 1065 passed；`run_incident` 无生产调用 |
| G2b | pipeline_runner 42 passed（用例数不变）；`test_incidents + tests/core` 212 passed |
| G3 | routing 2 passed；specs+workers 48 passed；orchestrator 相关 23 passed |
| G4 | `orchestrator_smoke.py` → 五条 `[ok]` + `SMOKE OK`；`agent_smoke.py` → `SMOKE OK`；仓库 DB/config 指纹不变 |
| T5 收尾 | 全量首跑多出两条：`test_agent_specs_router` 的 orchestrator 版本 2 漏同步、`test_crash_recovery` 退避满载 flake；修正后（13 §8.57）复跑见下 |
| T5 | 见下 |

### 最终验收（T5，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | 见「合并后复验」；与 dev 起点（3 failed / 4470 passed）相比不新增失败 |
| `scripts/dump_openapi.py --check` | 0 |
| `scripts/generate_api_guide.py --check` | 0 |
| `pnpm gen:api:check` | 0（本里程碑未改前端/接口） |
| `scripts/orchestrator_smoke.py` | `SMOKE OK`（五条 `[ok]`） |
| `scripts/agent_smoke.py` | `SMOKE OK` |
| `agent_eval regression replay` | `EVAL OK`（6/6） |
| `agent_eval capability replay` | `EVAL OK`（6/6，100.0%；orchestrator.md 改了提示词，通过率不低于基线） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 0 |
| 手动验收（六种 kind 中文标签、`kind=diagnose` 筛选） | 标签由 `/api/agent/specs` 的 title 带出、工单端点本就支持 kind 参数；未起浏览器手点（等价断言在 specs 路由测试与工单列表用例） |

### 计数

- 代理定义 9 → **15**；工具 93/92、HTTP 端点 144、错误码 141/140、业务表 38 **均不变**；无迁移（head 仍 `0027_device_lease`）。
- 新增脚本：`scripts/orchestrator_smoke.py`；新增测试文件：`tests/agent/test_builtin_workers.py`、`tests/agent/test_orchestrator_routing.py`。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 诊断经 `WorkerService.open_ticket` 建单 | 裸 create 不起 Worker（13 §8.51） |
| R2 | 去重用 `ticket.result.incident_key`，不用会话唯一键 | spec §4.1；避免旧会话撞唯一索引（13 §8.52） |
| R3 | `IncidentService` 延迟解析服务、`IncidentResult.session_id` 填工单 id | 装配顺序约束；保持调用方签名（13 §8.53） |
| R4 | `brief_header` + daily_ops 删 recon 句 | 文案正确性与授权一致性（13 §8.54） |
| R5 | 前端零改动（标签来自 specTitles） | 实现事实（13 §8.55） |
| R6 | `agent_smoke.py` 旧诊断断言与名单同步改 | 验收要求两冒烟通过；只改脚本（13 §8.56） |
| R7 | 浏览器手动验收记「未做」 | 等价断言在路由/列表用例；本会话无浏览器手点 |

## 偏离（已记 13 §8.51–8.56）

- §8.51 诊断建单链路；§8.52 去重与 `update_result`；§8.53 装配延迟与返回值；§8.54 文案前缀与 daily_ops recon；§8.55 前端标签；§8.56 冒烟断言联动。

## 遗留

- 真机/浏览器手验未做（六种 kind 中文标签、`kind=diagnose` 筛选在真实数据上的展示）；specs 路由与工单列表用例覆盖等价断言。
- `daily_ops` 等六份定义的提示词质量尚未经真实 LLM 行为评估；后续里程碑（M87 验证闸门、M81 事件规则）再收敛。
- P2 起从 M74（上下文压缩与长会话）开始；`legacy_*` 定义保留供回放，M73 后不再起新运行。
