# M72 指挥台前端（台账）

- 分支：`console-frontend`；基点：`dev` = `86b33c8`；合并：`dev` 上的 `merge: 指挥台前端（M72，见 docs/v3/13 §8.45–8.50）`；tag：`v3-m72`
- spec：[m72-console-frontend](../specs/m72-console-frontend.md)
- 计划：[m72-console-frontend](../plans/m72-console-frontend.md)
- 决策记录：[docs/v3/13 §8.45–8.50](../13-决策记录.md)
- 本里程碑**无迁移**。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `2c7ddc4` | 计划入库；§0 的 16 条检查（A1 `MISSING 0`、A2 七 schema；#11 evals 调用方裁决；#12 能力检查仍需迁移；#13 runs/costs/specs 落点已在） |
| G1（T1） | `ee8f311` | 删除四个 v2 写端点、请求模型与 `_LockedSessionStreamingResponse`；`stream_message` 抽成 `agent/legacy_turn.py` 的模块级驱动（evals 与 `tests/fakes/legacy_turn.py` 复用）；删 6 条端点用例、新增 `test_removed_v2_session_write_routes`；14 清单 −4（148→144） |
| G2（T2） | `5ff82d6` | `list_page(legacy_only=)`、`load_inbox_meta`、`session_wire` 的 `kind`/`archived_at`、`message_wire` 的 `run_id`/`inbox_id`/`inbox_kind`/`inbox_thread_key`、v3 消息两字段 |
| G3（T3） | `2ebc04b` | `agent/mentions.py` 五类展开与【引用】段（去重/上限 8/截断/读取失败）；接入总控与工单写入点；worker passive 摘要用原文（`notify_content`）；common.md 加一句；capability 6/6 |
| G4（T4） | `dd703ce` | 冒烟切指挥台：`_SmokeLLM` 分派（含 M67 四类探查兼容）、总控 SSE v3 + 消息 + 运行用量、`/__smoke/legacy-sessions`、相对计数；frontend_smoke 深链/404/405/构建产物 |
| 修复 | `7764beb` | v3 `text_delta` 补 `run_id`（M70 缺陷；04 §3），v2 形状不变 + 回归用例 |
| G5（T5） | `b14cc91` | query keys、WS 失效（agent_run/ticket_status/lease_changed/subscribed）、`streamSse`（detach）、Composer 三 props、messages 泛型、TOOL_LABELS +9 |
| G6（T6） | `b6a8f93` | 会话核心：SSE 归一 15 变体、实时副本 reducer、`filterLiveHistory`/`userMessageArrived`、`transcript-v3`、`useConversation`（发送/排队/停止）、会话组件（27 例） |
| G7（T7） | `4749842` | 斜杠命令、@ 提及候选源与面板、`ConversationComposer`（状态行优先级、busy/onStop、seed）、HelpCard（48 例） |
| G8（T8） | `6e204f1` | 指挥台：test-utils 端点模拟、`attention` 合并排序与来源、桌面两列/手机 Sheet、快捷开场/菜单/重新开始；移植 AG2/A G4/AG5/M21/M22/M23/M59 共 30 例 |
| G9（T9） | `82e2ff1` | 工单列表与详情：format 适配/8 张映射/费用与时长、汇报时间线、活动树、委托卡、租约指示与释放、取消与结束态（15 例） |
| G10（T10） | `0346c93` | 代理定义列表（四组）与详情（工具/委派/实际模型/提示词折叠/404）（3 例） |
| G11（T11） | `eaf1cf7` | 五项 Tab 与三组侧栏/更多页、六个新路由 + `/agent*` 重定向、旧会话只读查看器（`git mv` + 重写，11 例）、`/more/status`、能力检查改 probe 端点 |
| T12 | 本台账所在提交 | 02/03/04/05/14 契约同步；13 §8.45–8.50；README、本台账；全量验收与合并 |

## 核心决策记录

| 主题 | 结论 |
|---|---|
| `stream_message` 的评估驱动调用方 | 抽成生产模块级 `stream_legacy_turn`，evals 与测试共用（13 §8.45） |
| v3 `text_delta` 缺 `run_id` | 修 `collect_completion` + pump，v2 形状不变（13 §8.46） |
| 提及断言与摘要 | 按契约改一条既有用例的两处断言；`notify_content` 保摘要不含引用段（13 §8.47） |
| 冒烟探查兼容 | 补 M67 四类探查固定应答（13 §8.48） |
| query key 替换与临时占位 | v2 三个 ticket 键被新对象取代；G5 的 4 处 `@ts-expect-error` 在 G11 清零（13 §8.49） |
| 工单页补充文案/版式 | 404 与读取失败文案按风格补；leftover 桌面/手机各一处（13 §8.50） |

## §0 #4 抄下的响应模型属性名

- `TicketSummary`：id、title、kind、status、origin、summary、last_report_status、human_action、priority、created_at、updated_at、ended_at
- `TicketDetail`：ticket、mandate、recent_reports、result、subscriptions、worker_session_id、turn_running、lease
- `RunSummary`/`RunDetail`：run_id、agent_name、kind、status、finish_reason、steps、prompt_tokens、cached_tokens、completion_tokens、cost_micros、currency、started_at、finished_at、parent_run_id、root_run_id、ticket_id（RunDetail 另含 children、meta、error_message）
- `SpecSummary`/`SpecDetail`：name、title、description、kind、model_role、tools、delegates、version、spec_version（Detail 另含 body、device、risk_ceiling、max_steps、deferred_groups、skills、budget、output_schema）
- `Mention`：type、id、label
- 适配函数无差异；只做缺省回退（id←ticket_id、reports←recent_reports、run_id←id、agent_name←agent）。

## §0 分支选择记录

- #12：`AgentCapabilityCheck.tsx`/`settings.test.tsx` 仍在调 `capabilities/check` → T11 第 7 步执行（改 `POST /api/agent/model-profiles/default/probe`，三徽章 + `响应 {latency_ms} ms`）。
- #13：`runs`/`modelRoles`/`specs` key 已存在，复用；`more/runs`、`more/costs` 路由保留。
- #11：额外调用方为评估驱动（见 13 §8.45）。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | 目标 pytest 0（`99 passed`）；parity 17；openapi 0；全量 4457 passed |
| G2 | `agent_router + orchestrator_router` = `14 passed`；openapi/genapi 0 |
| G3 | 定向 16 passed；capability `6/6`（与 M71 基线一致） |
| G4 | `agent_smoke.py` → `SMOKE OK` |
| G5 | typecheck 0；realtime+messages = 34 passed |
| G6 | typecheck 0；conversation = 27 passed；features/agent 180 passed |
| G7 | typecheck 0；conversation = 48 passed |
| G8 | typecheck 0；features/console = 82 passed |
| G9 | typecheck 0；features/tickets = 15 passed；全量前端 901 passed |
| G10 | typecheck 0；features/agents = 3 passed |
| G11 | typecheck 0；全量 vitest 96 文件 **877 passed**；`streamMessage`/`capabilities/check`/`AgentPage` 引用清零 |
| 修复 | `text_delta` 回归用例 6 passed；parity 17 |

### 最终验收（T12，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 4470 passed**；3 条与 dev 起点同一组（真资源数据），无新增 |
| `scripts/dump_openapi.py --check` | 0 |
| `scripts/generate_api_guide.py --check` | 0 |
| 前端五步（install / gen:api:check / typecheck / test / build） | 0；vitest 96 文件 **877** 用例；build 正常 |
| `scripts/frontend_smoke.py` / `scripts/agent_smoke.py` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，不低于 M71 基线） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 0 |
| 手动验收（真机手机宽度走总控/工单） | **未验证**（本会话无常驻服务与真机）；等价链路由 877 条前端用例与后端 e2e 覆盖 |

### 计数

- HTTP 端点 148 → **144**（−4）；错误码、工具（93/92）、业务表（38）不变；无迁移（head 仍 `0027_device_lease`）；OpenAPI tag 19 不变。
- 前端：测试文件 78 → **96**，用例 801 → **877**。
- 新增后端模块：`maa_api/agent/legacy_turn.py`、`maa_api/agent/mentions.py`；新增前端目录：`features/console/conversation/`、`features/console/`、`features/tickets/`、`features/agents/`。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `stream_legacy_turn` 放生产模块而不是只放 tests/fakes | evals runner 也需要驱动；一处实现两处复用（13 §8.45） |
| R2 | 修 v3 `text_delta` 缺 `run_id`（含 v2 形状不变 + 回归用例） | 04 §3 契约；否则前端直播文字全丢（13 §8.46） |
| R3 | 改 `test_mentions_are_stored_in_inbox_payload` 两处断言 + `notify_content` | 契约要求的必然联动（13 §8.47） |
| R4 | 冒烟补 M67 四类探查兼容 | 否则冒烟不可通过（13 §8.48） |
| R5 | 渐进迁移期允许 `@ts-expect-error`，G11 清零 | 顺序约束；最终无残留（13 §8.49） |
| R6 | 工单页 404/读取失败文案按风格补；leftover 版式两处 | spec 文案表不穷尽，交互口径不变（13 §8.50） |
| R7 | 手动真机验收记「未验证」 | 本会话无常驻服务与真机；等价链路由前端用例与后端 e2e 覆盖 |

## 偏离（已记 13 §8.45–8.50）

- §8.45 评估驱动与 `stream_legacy_turn`；§8.46 `text_delta` 补 `run_id`；§8.47 提及断言与 passive 摘要；§8.48 冒烟探查兼容；§8.49 query key 替换与临时占位；§8.50 工单页补充文案/版式。

## 遗留

- 真机手验未做（手机宽度下的 Tab、流式、建单胶囊、旧会话跳转），需要起服务配真实 LLM 时补。
- v2 会话面已在后端只读（写端点删除），但 `legacy_*` 定义与旧会话数据仍保留到 M73（诊断改建工单、`legacy_chat` 退场）。
- `agents/*` 重定向保留 query/hash；`/agent/:sessionId` 直接落到 `/more/sessions` 后由页面读 `?session_id=`。
- `orchestrator_smoke.py` 属 M73，本里程碑未加。
