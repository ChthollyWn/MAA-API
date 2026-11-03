# SDD ledger — Agent 主动提问与参数选择卡片（M21）

## Setup

- Base：`b943125`（M20 合并回 `refactor/v2` 的合并提交），分支 `m21-agent-question-card`（从 `refactor/v2` 新建）。
- 规格：`docs/superpowers/specs/2026-09-29-m21-agent-question-card.md`；计划：`docs/superpowers/plans/2026-09-29-m21-agent-question-card.md`。
- 用户裁决（2026-09-29）：
  1. 「让 agent 自己创建任务时，关于参数的选择，应该主动发送选择卡片，等待用户的选择后开始任务，不是让用户自己发消息回复。」
  2. 卡片可见范围：对话页内 + 本会话待答卡（刷新/切页回来仍能看到），**不动**全局「待确认」面板与标题栏胶囊。
  3. 用户没回答时：10 分钟后过期，**直接结束本轮**。
  4. 选项能力：单选 + 多选。

## 事实基础（动手前实际核对）

- 现状：Agent 需要用户裁决参数时只能写在正文里让用户回复；`SYSTEM_PROMPT` 的「不确定的参数要向用户询问，不要猜测」没有任何机制支撑。全仓 grep 无 ask_user / question 概念。
- 现成的「阻塞式卡片」只有人工确认链路：`ConfirmationService` → `confirmation` 表 → WS `confirm_request` → SSE `confirmation_required` → 前端本轮确认卡 → `POST /api/confirmations/{id}`；runtime 每 0.25s 轮询确认状态（`runtime.py:1061-1084`），确认终态会 `_finish_with_text(..., "confirmation_resolved")` 结束本轮。
- 工具循环的关键位置：`runtime.py:925` `_execute_tool_batch`（`tool_start` → `confirmation_service.invoke` → 结果回填 tool 消息 → `_run_turn` 继续/收尾）；`read_only=True` 的批次并发、否则串行；连续三轮相同调用判 `stuck_loop`。
- 工具 schema 只把 name/description/parameters 发给模型（`runtime.py:1304-1325`）；`INCIDENT_ALLOWED_TOOLS`（`runtime.py:72`）是自动诊断会话的白名单，天然不含新工具。

## Rulings I made

- Ruling: **提问不复用确认链路**，另起 `agent_question` 表 + `QuestionService` + `/api/agent/questions`。理由：用户裁决 §2 明确「不动全局待确认面板」，而复用 confirmation 会让提问自动进入待确认列表与标题栏胶囊；确认的语义（风险等级、批准即执行、拒绝后跳过后续调用）也与提问（拿一个答案继续）不同。代价：多一份与 ConfirmationService 同构的服务与一组测试。
- Ruling: **加一个「跳过」动作**（用户裁决只说了超时）。理由：提问挂起期间本轮 SSE 未结束、输入栏发不出消息，没有跳过就要被卡满 10 分钟。跳过与超时共用同一套收尾路径（`finish_reason="question_unanswered"`），代价是一个状态与一个端点。若用户不要，删除端点与按钮即可，超时路径不受影响。
- Ruling: 超时/跳过**以正常工具结果 + 结束本轮**收尾，不用 `AppError` 错误体。理由：那不是错误，是「用户没给裁决」；写进 tool 消息里也更好读。
- Ruling: `ask_user` 的 `read_only=False`。理由：提问必须与写操作串行，「问完再做」是这次需求的语义；代价是含 `ask_user` 的批次不再并发。
- Ruling: 自动诊断会话（`INCIDENT_ALLOWED_TOOLS`）不放开 `ask_user`。理由：无人值守的诊断挂起 10 分钟毫无意义。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 后端 | 模型/迁移/服务/REST/工具/runtime/测试/OpenAPI | 完成（`backend-report.md`） |
| 2 · 前端 | QuestionCard、AgentPage、待答查询、WS 失效、测试 | 完成（`frontend-report.md`） |
| 3 · 提示词与文档 | SYSTEM_PROMPT、内置知识条目、docs/04·05·09·11·12·13·README、docs/14 | 进行中（提示词与知识条目由主 agent 完成，文档由子 agent 同步） |
| 4 · 验收（主 agent） | 见计划 | 完成 |
| 5 · 台账与合并 | ledger + `git merge --no-ff` + `v2-m21` | 进行中 |

## Task progress

- **Task 1（后端）** 实现者完成（`backend-report.md`）。主 agent 复核：读过 `git diff maa_api/agent/runtime.py`（`question_service` 注入、`_ask_user` 拦截、`question_required` 事件、`question_unanswered` 收尾、`call_runtime_factory` 签名兼容）、`maa_api/agent/questions.py` 的 `wait/answer/skip/reset_after_restart/expire_due`、`db/models.py` 的 `AgentQuestion`（收尾时挪到文件末尾保持节号单调）。主 agent 亲自复跑：`pytest -q` → **1932 passed, 4 skipped** 退出码 0；`dump_openapi.py --check` 退出码 0；`git diff --stat maa_api/agent/confirmation.py` 为空。收尾追加两处裁决：`AgentQuestion` 挪到 `agent_knowledge`（§5.16）之后、超时文案去掉硬编码「10 分钟」。
- **Task 2（前端）** 实现者完成（`frontend-report.md`）。主 agent 复核 diff（`web/src` 10 个文件 + 新增 `QuestionCard.tsx`/`QuestionCard.test.tsx`）：`question_required` → 本轮卡、`tool_result` 摘牌、待答查询去重与 10 秒轮询、输入栏状态行、WS 两事件只做失效，确认链路前端零改动。主 agent 亲自复跑：`pnpm gen:api:check`、`pnpm typecheck`、`pnpm test --run`（**54 文件 / 554 项**）、`pnpm build`（末行 `[sw_harness] SERVICE WORKER OK`）全部退出码 0。**环境发现**：DSH 运行时自带的 node v24.21.0 是 hardened runtime，rolldown 的 darwin-arm64 原生绑定是 adhoc 签名，`dlopen` 失败 → `vitest`/`vite build` 启动即报 `Cannot find native binding`（用未改动的用例可复现，与本改动无关）；test/build 改用 `/opt/homebrew/bin/node` v23.11.0 通过，`gen:api:check`/`typecheck` 两个 node 都可以。
- **Task 3（提示词与文档）** 主 agent 亲自改 `SYSTEM_PROMPT`（新增「需要用户裁决参数时」一段：候选之间选定必须发卡、要在正文里让用户回复的反面写法、selected 即裁决、跳过/超时不重发、开放式问题仍用正文）与 4 条内置知识条目（`currency-sources`、`playbook-infrast-shift`、`playbook-promotion-planning`、`playbook-sanity-planning`），并用脚本把对应 4 个块同步进 M19 规格 §4.6（逐字一致性测试 `tests/agent/test_knowledge_service.py` 全绿）。docs/04·05·09·11·12·README 与 docs/14 由文档子 agent 同步（`docs-report.md`）；docs/13 §8.26 由主 agent 亲自写。
- **Task 4（验收，主 agent 亲自跑）**：
  - 后端 `.venv/bin/python -m pytest` → **1932 passed, 4 skipped**，退出码 0；
  - `.venv/bin/python scripts/dump_openapi.py --check` → `OpenAPI snapshot is current`；
  - `scripts/agent_smoke.py` → `SMOKE OK`；`scripts/frontend_smoke.py` → `SMOKE OK`（先 `pnpm build`）；
  - 前端四条命令全部退出码 0（见 Task 2）；
  - 独立探针 `/tmp/m21_probe.py`（不经实现者测试夹具）：wire 12 字段、answer → `answered`、重复回答 → `QUESTION_ALREADY_ANSWERED`、单选提交两项 → `QUESTION_SELECTION_INVALID`、超时 → `expired`（`answered_by=system`）、`skip` → `skipped`、3 次 `question_request` / 3 次 `question_resolved` 广播、`ask_user` 工具元数据（interaction / SAFE / read_only=False / model_visible=True）、`SYSTEM_PROMPT` 含发卡规则 → 全部符合预期，末行 `PROBE OK`。

## 最终交付

- 分支 `m21-agent-question-card`（从 `refactor/v2` 的 `b943125` 新建），合并提交见 `git log`，tag `v2-m21`。
- 交付内容：`ask_user` 工具 + `agent_question` 表（迁移 0015）+ `QuestionService` + `/api/agent/questions` 四个端点 + SSE `question_required` / WS `question_request`·`question_resolved`；runtime 在提问挂起期间阻塞同一轮、用户选择作为工具结果回填后继续，超时（默认 600 秒）或跳过以 `finish_reason="question_unanswered"` 结束本轮；前端对话页提问卡（单选/多选、提交、跳过、倒计时、超时态）与本会话待答卡（刷新恢复）；提示词与 4 条内置知识改成「发卡提问」；文档 7 篇 + API 指南同步。
- 遗留：工具条等待期间仍显示「执行中」；无自由文本答案；无 Web Push / 全局面板计数（用户裁决范围外）；多标签页同步依赖 WS 在线。（另：`config.yaml` 是本地 gitignore 文件，实现者按 `confirmation_timeout_seconds` 同款加了 `question_timeout_seconds: 600`，不进版本库。）

