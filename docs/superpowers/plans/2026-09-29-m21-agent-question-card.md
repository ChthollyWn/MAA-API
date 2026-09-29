# M21 · Agent 主动提问与参数选择卡片实施计划

规格见 [specs/2026-09-29-m21-agent-question-card.md](../specs/2026-09-29-m21-agent-question-card.md)。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m21-agent-question-card`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- `python` 不在 PATH，一律 `.venv/bin/python`。
- 长输出重定向到文件，只读尾部和退出码；主 agent 不通读大输出。
- **实现者不要 `git commit` / `git add` / `git stash`**；提交由主 agent 统一做。
- 不引入新依赖。
- **不动确认链路**（`confirmation.py`、`confirmation` 表、`/api/confirmations`、全局面板、Web Push）与全局「待确认」计数。
- 测试、构建这类长输出重定向到文件，报告里给退出码与尾部统计。

任务依赖：Task 1 先行；Task 2 依赖 Task 1（要重新生成的 OpenAPI/TS 类型）；Task 3 的提示词部分依赖 Task 1；Task 4 依赖 1–3。

## Task 1 · 后端：数据模型、服务、REST、工具、runtime 接入

**产物**：

- `maa_api/db/models.py` 新增 `AgentQuestion`（docs/04 §5.17 的列与索引）+ `maa_api/db/migrations/versions/0015_agent_question.py`（`down_revision = "0014_agent_knowledge"`）；
- `maa_api/domain/enums.py` 新增 `QuestionStatus`；`maa_api/domain/errors.py` 新增 4 个错误码与 HTTP 绑定（`QUESTION_NOT_FOUND` 404、`QUESTION_ALREADY_ANSWERED` 409、`QUESTION_EXPIRED` 409、`QUESTION_SELECTION_INVALID` 400）；
- `maa_api/db/repositories/question.py`（或复用 `agent.py` 的仓储风格）；
- `maa_api/agent/questions.py`：`QuestionService`，方法签名与语义见规格 §3.3；
- `maa_api/agent/tools/interaction.py`：`ask_user` 工具定义 + REST handler（抛 `INVALID_PARAMETER`）；`tools/__init__.py` 注册新组；
- `maa_api/agent/runtime.py`：`question_service` 注入、`_execute_tool_batch` 拦截、SSE `question_required`、超时/跳过收尾（`finish_reason="question_unanswered"`）；
- `maa_api/api/routers/questions.py` + `maa_api/api/routers/__init__.py` 挂载；`maa_api/api/errors.py` 的 tag 分组如需要；
- `maa_api/settings.py` 新增 `agent.question_timeout_seconds`（默认 600，`ge=1`）与 env 映射；
- `maa_api/main.py`：构造 `QuestionService`、`app.state.question_service`、`start()`/`close()`、注入 `AgentRuntime`（含 `agent_runtime_factory` 兼容：现有工厂只接一个位置参数，保持向后兼容——新增参数走关键字且默认 `None`）；
- 测试：`tests/agent/test_question_service.py`、`tests/agent/test_runtime.py` 增补、`tests/api/` 四个端点的路由测试、`tests/agent/test_tool_catalog.py`（43 个工具）；
- 生成物：`web/openapi.json`（`.venv/bin/python scripts/dump_openapi.py`）。

**完成判据**：

- `.venv/bin/python -m pytest -q tests/agent tests/api tests/domain tests/db` 退出码 0；
- `.venv/bin/python scripts/dump_openapi.py --check` 退出码 0；
- 确认链路测试（`tests/agent/test_confirmation_service.py`、`test_confirmation_push.py`）未被改动或全部通过。

## Task 2 · 前端：提问卡与本会话待答卡

**产物**（`web/src/`）：

- `features/agent/QuestionCard.tsx` + `QuestionCard.test.tsx`：单选/多选、提交、跳过、本地倒计时与超时态；
- `features/agent/types.ts`、`AgentPage.tsx`、`MessageViews.tsx`：`question_required` 事件、本轮卡槽、收尾保留条件、输入栏状态行「请先回答上方的问题」；
- 本会话待答卡：`keys.agent.pendingQuestions(sessionId)` + 10 秒轮询查询，渲染在本轮之后，与已展示的本轮提问去重；
- `realtime/events.ts`、`realtime/query-bridge.ts`：`question_request` / `question_resolved` → 失效待答查询；
- `api/keys.ts`、`web/src/types/api.d.ts`（`pnpm gen:api`）；
- AgentPage 测试增补：`question_required` 后出卡、提交后清卡、超时文案。

**完成判据**：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 退出码 0。

## Task 3 · 提示词与文档同步（主 agent）

- `runtime.py` 的 `SYSTEM_PROMPT`：需要用户在若干选项间裁决时**必须**调用 `ask_user`（给出渠道等例子），禁止在正文里让用户回复；卡片返回的 `selected` 即用户裁决，拿到后直接执行；没有候选选项的开放式问题仍用正文提问。
- 知识库内置条目里若有「让用户在正文里回复」的措辞，一并改掉（`maa_api/agent/knowledge/builtin/*.md`）。
- docs/04 §5.17、docs/05 §6.18 + 错误码表（100 → 104）、docs/09 对话页、docs/11 §3.2/§5.3/§8.2/§8.4、docs/12 M21、docs/13 §8.26、docs/README 决策速查表。
- docs/14 由 `scripts/generate_api_guide.py` 重新生成（不带 `--check`）。

## Task 4 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`；
- `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
- `cd web && pnpm typecheck && pnpm test --run && pnpm build`；
- 行为实测：用假 LLM 驱动 `AgentRuntime`，跑通「`ask_user` → 选择 → 同一轮继续」与「超时 → `question_unanswered` 收尾」两条路径，把证据写进台账；
- 全仓 `grep` 确认没有提问逻辑漏进确认链路（`confirmation.py` 的 diff 应为空）。

## Task 5 · 台账与合并（主 agent）

- `.superpowers/sdd/2026-09-29-m21-agent-question-card/progress.md` → `docs/superpowers/ledgers/2026-09-29-m21-agent-question-card.md`；
- `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m21`。
