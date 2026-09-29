# M22 · 中断 Agent 正在执行的一轮实施计划

规格见 [specs/2026-09-29-m22-agent-interrupt.md](../specs/2026-09-29-m22-agent-interrupt.md)。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m22-agent-interrupt`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- `python` 不在 PATH，一律 `.venv/bin/python`；前端 node/pnpm 见 Task 2。
- **不要 `git commit` / `git add` / `git stash`**；提交由主 agent 统一做。
- 长输出重定向到文件，只读尾部与退出码。
- 不引入新依赖。
- **被动断开不取消本轮**这条既有语义不变（已有测试必须继续通过，不许为了让新用例好写而放宽）。
- 中断**不碰**流水线与队列：不调 `stop_pipeline`、不取消任何已入队任务。

任务依赖：Task 1 → Task 2（前端要 `pnpm gen:api` 生成的新端点类型）；Task 3 依赖 1、2；Task 4 主 agent。

## Task 1 · 后端：中断链路

**产物**：

- `maa_api/agent/runtime.py`：`_active_turns`、`turn_running()`、`interrupt_turn()`、`_interrupted` 标记、取消分支的收尾（部分文本 + 中断标记落库、批次内未落库调用的 tool 桩消息、SSE `interrupted`）、`run_incident` 的取消文案区分；
- `maa_api/agent/confirmation.py`：`abort_session_calls(session_id)` + `_execute_and_record` 的 `CancelledError` 处理（审计置 `FAILED` + `AGENT_TURN_INTERRUPTED` 后 re-raise）；
- `maa_api/agent/questions.py`：`QuestionStatus.CANCELLED` + `cancel_pending_for_session(session_id)`；
- `maa_api/domain/enums.py`（`QuestionStatus`）、`maa_api/domain/errors.py`（`AGENT_TURN_INTERRUPTED` 409，104 → 105）；
- `maa_api/api/routers/agent.py`：`POST /api/agent/sessions/{id}/interrupt`、`GET /api/agent/sessions/{id}` 增 `turn_running`；
- `tests/agent/test_runtime.py`、`tests/agent/test_confirmation_service.py`、`tests/agent/test_question_service.py`、`tests/api/test_agent_router.py`、`tests/domain/test_errors.py`（计数）、必要时 `tests/db/*` 清单；
- 生成物 `web/openapi.json`（`.venv/bin/python scripts/dump_openapi.py`）。

**完成判据**：`.venv/bin/python -m pytest -q tests/agent tests/api tests/domain tests/db` 退出码 0；`.venv/bin/python scripts/dump_openapi.py --check` 退出码 0；规格 §5 列的后端用例全部存在且真的断言到（尤其「批次中途中断后历史结构完整」与「被动断开仍不取消」）。

## Task 2 · 前端：停止入口

**产物**（`web/src/`）：

- `features/agent/Composer.tsx`：busy 时发送按钮变「停止」（`CircleStop` + `destructive-soft`，`aria-label="停止本轮"`，stopping 态），新增 `onStop`/`stopping` props；
- `features/agent/AgentPage.tsx`：stop mutation（`POST /api/agent/sessions/{id}/interrupt`）→ abort 流 + 显式 `running:false` + 失效五组查询；`AGENT_SESSION_BUSY` 文案；SSE `interrupted` 事件的收尾；会话详情 10 秒轮询 + `turn_running` 时的「本轮仍在后台执行」状态行与停止入口；
- `features/agent/types.ts`：`AgentSession.turn_running`、`finish_reason` 相关类型；
- `types/api.d.ts`（`pnpm gen:api`）、必要时 `api/keys.ts`；
- 测试：`AgentPage.test.tsx`（busy 时按钮是停止、点击后请求发出 + 本轮收尾 + 发送恢复、`turn_running` 时刷新后仍能停、409 文案）、`Composer` 相关断言。

**完成判据**：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（test/build 用 `/opt/homebrew/bin/node`，见 M21 台账的环境陷阱）。

## Task 3 · 文档同步（子 agent + 主 agent）

- 子 agent：docs/04 §5.8/§5.17、docs/05 §4.11/§6.15、docs/09 §6.4、docs/11 §5/§8.2/§8.5、docs/12 M22、docs/README、docs/14（脚本生成）。
- 主 agent：docs/13 §8.27（三条裁决 + 取代「不做停止生成」的记录 + 不可撤回的边界 + 实现裁决）、SDD 台账。

## Task 4 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`；
- `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
- `cd web && pnpm typecheck && pnpm test --run && pnpm build`（homebrew node）；
- `scripts/agent_smoke.py`、`scripts/frontend_smoke.py`；
- 行为实测：独立探针驱动一轮 → 工具挂起时调中断端点 → 断言审计状态、会话锁释放、`turn_running` 翻转、历史结构完整；
- `git diff` 确认没有动流水线/队列的中断语义。

## Task 5 · 台账与合并（主 agent）

- `.superpowers/sdd/2026-09-29-m22-agent-interrupt/progress.md` → `docs/superpowers/ledgers/2026-09-29-m22-agent-interrupt.md`；
- `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m22`。
