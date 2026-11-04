# M70 总控与 Worker 运行（台账）

- 分支：`orchestrator-workers`；基点：`dev` = `edc31f5`；合并：`dev` 上的 `merge: 总控与 Worker 运行（M70，见 docs/v3/13 §8.30–8.36）`；tag：`v3-m70`
- spec：[m70-orchestrator-workers](../specs/m70-orchestrator-workers.md)
- 计划：[m70-orchestrator-workers](../plans/m70-orchestrator-workers.md)
- 决策记录：[docs/v3/13 §8.30–8.36](../13-决策记录.md)
- 本里程碑**无迁移**。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `5c95a50` | 计划入库；§0 的 19 条检查（#12 命中均既有名字、#16 无匹配 → T13 完整补 v2 路由过滤） |
| G1（T1+T2） | `0d4b3c0` | 三个配置类/键/`_FIELDS`/模板；三个错误码（403/403/409）；`FinishHookFanout` 与 `main.py` 接线 |
| G2（T3） | `829b3b0` | 运行时接缝：`CURRENT_RUN`/`require_run`、`RunEventBus.subscribe`、`run_start.consumed_inbox_ids`、`cancelled`、`message_persisted`、`AgentLoop.run` 接外部运行行/state/inbox_items/cancel_event；收件箱 `put_user_message`/`has_active_pending`/`count_pending`/`render`；`deliver` 不回落；`TicketService.bind_orchestrator_resolver` |
| 接缝补缺 | `a376fda` | `RunService.start` 加 `ticket_id`（13 §8.30） |
| G3（T4） | `8479385` | `common.md`、注册表 `common_prompt`、`workers/cards.py`（角色卡/工单卡/任务卡/收件箱渲染/persist）、提示分层插段 |
| G4（T5+T6） | `50f5e87` | 六份新定义（orchestrator/general/researcher/navigator/verifier/titler，recon 未动）、三个 schema、七个新工具（`rest_visible=False`）、v2 工具隔离；工具 84→91/83→90 |
| G5（T7） | `9472063` | `DeviceGate`、`MandateMiddleware`/`DeviceGateMiddleware`/`ReportMiddleware`/`ensure_report`、v3 链装配 |
| G6（T8） | `95e2598` | `SessionPump`/`WorkerSlots`/`WakeDispatcher`/`run_agent` |
| G7（T9） | `349b159` | `WorkerService`（建单/唤醒/汇报/取消/结束/订阅）、`TicketTitler` |
| G8（T10） | `ea660c7` | `DelegationService`（名单/深度/并行/独占串行/降级/随父中断） |
| G9（T11） | `287ab2f` | `OrchestratorService`（唯一会话/修复归档/批窗口/提问让位/重置/扫描） |
| G10（T12） | `a0b4c6d` | `RunEventStream`/`AgentRunBroadcaster`、WS `agent_run`、前端事件类型与 query-bridge |
| G11（T13） | `34851a0` | 九条端点与模型；lifespan 第 8 步装配六项+hub+监听者+广播器；关闭序列；v2 路由按 kind 过滤；run_id 归属方案（hub + per-run 转发）；清单 146 条；快照/类型/指南重生成 |
| G12（T14） | `155b7ca` | 假 lifespan/`ScriptedLLM` 共享设施；七个 e2e 用例（连跑 3 次稳定）；冒烟改用共享设施；修复 e2e 暴露的两处实现缺陷 |
| T15 | 本台账所在提交 | 02/04/05 契约同步；13 §8.30–8.36；README 状态行；全量验收与合并 |

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | settings+vapid+errors = `91 passed`；fanout+upgrade_plans+assembly = `16 passed` |
| G2 | `tests/agent` = `961 passed, 1 skipped, 2 xfailed`；parity 17；agent_eval regression `EVAL OK` |
| G3 | cards+harness = `76 passed`；parity 17；regression `EVAL OK`；`legacy_chat` 提示 sha256 不变 |
| G4 | 工具/定义/schema/runtime = `111 passed`；parity 17；计数 `91 90` |
| G5 | workers = `14 passed`；parity 17；regression `EVAL OK` |
| G6 | pump 5 条 + workers `19 passed` |
| G7 | worker/titler 定向 `11 passed`；workers `30 passed`；`tests/agent` 1002 passed |
| G8 | delegation 6 条；workers `36 passed` |
| G9 | orchestrator `5 passed`；`tests/agent` 1013 passed |
| G10 | stream `5 passed`；`test_ws.py` 12 passed；前端 typecheck + vitest 78 文件 793 用例 |
| G11 | `tests/api tests/domain` = `971 passed`；parity 17；`dump_openapi --check` 0；`pnpm gen:api:check` 0 |
| G12 | e2e 连跑 3 次各 `7 passed`；`agent_smoke.py` = `SMOKE OK`；parity 17；`tests/agent tests/api` = `1545 passed` |

### 最终验收（T15，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量，两次） | 第一次 `4408 passed` / 5 failed（3 条真资源 + 两条既有 flake）；第二次 `4409 passed` / 4 failed（3 条真资源 + supervisor flake）。两条 flake 已按 13 §8.37 加固（测试层），加固后基线预期只剩 3 条真资源用例 |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0 |
| 前端五步（install / gen:api:check / typecheck / test / build） | 退出码 0；vitest 78 文件 **793** 用例；build 正常（`SERVICE WORKER OK`） |
| `scripts/frontend_smoke.py` / `scripts/agent_smoke.py` | 均 `SMOKE OK` |
| `scripts/agent_eval.py --suite regression --mode replay` | `EVAL OK`（6/6） |
| `scripts/agent_eval.py --suite capability --mode replay` | `EVAL OK`（6/6） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 退出码 0 |
| 手动验收（真实 LLM 起服务 curl 两条 SSE） | **未验证**（本会话没有常驻服务与真实 LLM key）；等价链路由七个 e2e 用例覆盖 |

### 计数

- 工具 84 → **91**（模型可见 83 → **90**，REST 可见 84 不变）；HTTP 端点 137 → **146**（+9）；错误码 135/134 → **138/137**（+3）；WebSocket 频道 15 → **16**（`agent_run`）；代理定义 3 → **9**；数据表与迁移不变（本里程碑无迁移，head 仍 `0026_tickets`）；OpenAPI tag 19 不变。
- 新增测试文件：`tests/agent/test_hooks_fanout.py`、`tests/agent/harness/test_m70_harness_seams.py`、`tests/agent/tickets/test_m70_inbox_seams.py`、`tests/agent/workers/{test_cards,test_worker_middleware,test_device_gate,test_session_pump,test_worker_service,test_ticket_titler,test_delegation_service}.py`、`tests/agent/orchestrator/{test_orchestrator_service,test_run_event_stream}.py`、`tests/api/{test_orchestrator_router,test_ticket_messages_router,test_orchestrator_e2e}.py`；`tests/fakes/{lifespan,scripted_llm}.py`。

## 已知 flake（与 M70 无关，验收时加固）

- `tests/services/test_webpush_vapid.py::test_signature_is_raw_jws_not_der`：原断言 raw ECDSA 签名不以 `0x30` 开头，首字节随机约 1/256 误报；已改为「长度 64 + 公钥验证往返」（13 §8.37）。
- `tests/core/test_supervisor.py::test_backoff_sequence_then_failed_after_budget`：满载下 5 秒等待与退避区间过紧；已放宽等待与区间（13 §8.37）。
- 加固后两条单独与组合跑均通过；后续全量验收基线为 3 条真资源作业的既有失败。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `RunService.start` 补 `ticket_id` 参数（13 §8.30） | M70 运行必须写该列；补缺不改既有语义 |
| R2 | M65–M69 接缝名字不同但语义相同的一律照代码用（13 §8.31） | spec §3.3 处理规则；无对外契约变化 |
| R3 | v2 工具隔离改为从内置非 legacy 定义推导 `V3_ONLY_TOOL_NAMES`（13 §8.32） | 照字面与 `legacy_chat.tools` 取交集会裁掉 v2 测试自定义注册表的假工具；生产行为等价 |
| R4 | WorkerService 三处实现裁决（13 §8.33） | `create` 不接 strength/上限 → open_ticket 先校验 origin/strength；空 brief 替换；cancel 补 `ticket_updated` |
| R5 | 事件 run_id 归属用 hub + per-run 转发（13 §8.34） | 保持 04 §3 契约；不重造事件对象 |
| R6 | e2e 暴露的两处实现缺陷直接修，不放宽断言（13 §8.35） | 缺陷属于 M70 实现，不是测试问题 |
| R7 | 测试设施抽取与冒烟定义数断言 3→9（13 §8.36） | 本里程碑定义扩展是事实；冒烟行为不变 |
| R8 | 手动验收（真实 LLM curl）记「未验证」 | 本会话无常驻服务与真实 key；七个 e2e 覆盖等价链路 |

## 偏离（已记 13 §8.30–8.36）

- §8.30 `RunService.start` 补 `ticket_id`；§8.31 接缝名称差异；§8.32 v2 工具隔离实现；§8.33 WorkerService 三处裁决；§8.34 事件 run_id 归属；§8.35 e2e 修复的两个缺陷；§8.36 测试设施与冒烟断言更新。

## 遗留

- 手动验收未做（见上）；M72 的指挥台前端会用本里程碑的 SSE/WS 契约，联调时再在真实服务上过一遍。
- `DeviceGateMiddleware` 是进程内临时方案，M71 用 `DeviceLeaseService` + `LeaseMiddleware` 替换（含 `DEVICE_LEASE_BUSY` 超时）。
- `common.md` 改动不参与 `spec_version` 哈希（spec §8 风险）：M70 起每个改提示词的里程碑必须跑 capability 评测；M98 再把 common 纳入哈希。
- 设备门在第二 Worker 上无反馈等待（spec §8 风险）：M71 解决。
- capability 套件仍是 legacy 场景；总控/Worker 的行为回归由本里程碑的七个 e2e 用例承接（后续里程碑的 spec 需要继续扩评测）。
