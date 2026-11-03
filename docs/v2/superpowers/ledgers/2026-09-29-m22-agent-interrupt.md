# SDD ledger — 中断 Agent 正在执行的一轮（M22）

## Setup

- Base：`1afab10`（M21 合并回 `refactor/v2` 的合并提交），分支 `m22-agent-interrupt`（从 `refactor/v2` 新建）。
- 规格：`docs/superpowers/specs/2026-09-29-m22-agent-interrupt.md`；计划：`docs/superpowers/plans/2026-09-29-m22-agent-interrupt.md`。
- 用户裁决（2026-09-29）：「agent 执行过程中，现在完全不能中断，需要加上中断功能。」同轮确认：①只停 Agent 本轮，游戏里跑着的流水线继续；②彻底撤回本轮在等/在跑的工具（未批准的确认撤掉、等待中的工具结束、审计记已中断），已入队的任务保留；③刷新页面后仍能停。

## 事实基础（动手前实际核对）

- `docs/11 §8.5` 从 M16 起明确写着「不做停止生成」，理由是 SSE 断开后 detached producer 仍继续跑、纯前端停止只断显示 —— 本次用户裁决取代它。
- 一轮的所有权链：`routers/agent.py` `acquire_session`（失败即 409 `AGENT_SESSION_BUSY`，这就是输入栏发不出消息的位置）→ `_LockedSessionStreamingResponse` → `runtime.stream_message` → detached `_produce_turn` → finally 放锁 + `_SSE_END`。全仓没有任何代码取消过 `_turn_tasks`。
- 确认的执行不依赖本轮是否还在等（`_wait_and_execute` 只认状态），所以**必须先撤确认再取消本轮**，否则存在「中断后用户一批准，工具照样执行」的窗口。
- `_execute_and_record` 不捕 `CancelledError`：取消 worker 会留下永久 pending 的审计。
- assistant 的 `tool_calls` 消息先落库、tool 消息整批完成后才落库 → 批次中途中断必须补桩，否则下一轮请求结构不完整（M20 起历史不裁剪，上游会 400）。

## Rulings I made

- Ruling: 中断是**主动动作**，只由新端点触发；断网/关页面的被动断开仍旧不取消本轮。理由：用户要的是「我能叫停」，不是「关掉页面就杀任务」；后者会让移动端切后台误杀长任务。代价：两条路径要在测试里显式区分。
- Ruling: 撤确认用**拒绝**语义（`rejected` + 审计 `REJECTED`）而不是删除记录。理由：审计要能回答「这条确认后来怎么了」；删除会让「批准过但没执行」无法追溯。
- Ruling: 在跑的工具 worker 用 `task.cancel()` 并让审计落 `FAILED` + 新错误码 `AGENT_TURN_INTERRUPTED`，而不是新增 `AuditStatus.cancelled`。理由：`AuditStatus` 是对外契约（docs/04），加枚举值要动迁移期语义与前端映射；`failed` + 明确的错误码已经能表达「没跑完，且原因是中断」。
- Ruling: 中断时把已产出的部分助手文本落库并附「本轮已被你中断。」，`finish_reason="interrupted"`。理由：否则用户在界面上读过的内容会在刷新后消失，且历史里看不出中断点。
- Ruling: 不连带停止流水线、不取消队列任务（用户裁决 §1）。
- Ruling: 已批准并已在执行的工具**无法回滚**，文档如实写明，不自称可回滚。
- Ruling: `turn_running` 走会话详情 + 前端 10 秒轮询，不新增 WS 事件。理由：Agent 页已有两条 10 秒轮询查询，加第三条的成本可接受；WS 只是让多标签页更快看到状态，收益不抵新增一个事件类型的契约面。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 后端 | runtime 中断、确认撤回、提问取消、REST、测试、OpenAPI | 完成（`backend-report.md`） |
| 2 · 前端 | Composer 停止按钮、AgentPage 收尾、turn_running、测试 | 完成（`frontend-report.md`） |
| 3 · 文档 | docs/04·05·09·11·12·13·README、docs/14 | 完成（`docs-report.md`，docs/13 §8.27 由主 agent 写） |
| 4 · 验收（主 agent） | 见下 | 完成 |
| 5 · 台账与合并 | ledger + `git merge --no-ff` + `v2-m22` | 完成 |

## Task progress

- **Task 1（后端）** 实现者完成（`backend-report.md`）。主 agent 复核：读过 `interrupt_turn`/`_abort_in_flight_calls`/`_interrupted_now` 与 `abort_session_calls` 的实现，确认「先撤确认、再取消本轮、再撤一次」的顺序与 `_aborted_audits` 区分「服务关闭」语义都落地。实现者的关键裁决：①关闭路径仍写 `SERVICE_UNAVAILABLE`，只有主动中断写 `AGENT_TURN_INTERRUPTED`；②新增 `_interrupt_settling` 避免二次 cancel 打断收尾；③补桩前回库按 `tool_call_id` 去重、桩消息在标记消息之前；④无活动轮次时也撤一次在飞调用。主 agent 亲自复跑：`pytest` → **1946 passed, 4 skipped** 退出码 0；`dump_openapi.py --check` 退出码 0；`git diff --stat maa_api/scheduler maa_api/services/pipeline_runner.py` 为空。
- **Task 2（前端）** 实现者完成（`frontend-report.md`）。主 agent 复核 diff：`Composer` 停止按钮（`CircleStop` + `destructive-soft`）、`AgentPage` 的 `stopTurn`/`invalidateTurnQueries`/`interrupted`+`message_end` 处理、`turn_running` 恢复入口、409 文案，并确认修掉了「abort 后 `running` 永久为 true」的既有 bug。实现者做了变异检验（拆掉四处实现各自变红）。主 agent 亲自复跑：`pnpm gen:api:check`、`pnpm typecheck`、`pnpm test --run`（**54 文件 / 558 项**）、`pnpm build`（末行 `[sw_harness] SERVICE WORKER OK`）全部退出码 0（test/build 用 `/opt/homebrew/bin/node`）。
- **Task 3（文档）** 子 agent 改 docs/04（§4/§5.8/§5.9/§5.17）、docs/05（§6.15 新端点 + `turn_running` + SSE `interrupted` + 中断段、§4.11 正文）、docs/09 §6.4、docs/11（§5.4 新小节 + §8.2 + §8.5 改写）、docs/12 M22、docs/README、docs/14（脚本重生成）；顺带补了 M21 漏同步的两处（docs/04 §5.8 的 `question_unanswered`、docs/05 SSE 清单的 `question_required`/`session_title`）。主 agent 亲自写 docs/13 §8.27。
- **Task 4（验收，主 agent 亲自跑）**：
  - 后端 `.venv/bin/python -m pytest` → **1946 passed, 4 skipped**，退出码 0（比 M21 多 14 条）；
  - `.venv/bin/python scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check` → 退出码 0；
  - `scripts/agent_smoke.py`、`scripts/frontend_smoke.py` → 末行均 `SMOKE OK`；
  - 独立探针 `/tmp/m22_probe.py`（不经实现者测试夹具，注册一个睡 60 秒的工具）：工具执行中 `interrupt_turn` → SSE 尾部 `interrupted` + `message_end`；历史 `user / assistant(tool_calls) / tool(桩) / assistant(interrupted)` 且每个 `tool_calls` 都有对应 tool 消息；审计 `failed` + `AGENT_TURN_INTERRUPTED`；重复中断返回 false；锁已释放；紧接着的第二轮正常收尾 → 末行 `PROBE OK`。

## 最终交付

- 分支 `m22-agent-interrupt`（从 `refactor/v2` 的 `1afab10` 新建），合并提交见 `git log`，tag `v2-m22`。
- 交付内容：`POST /api/agent/sessions/{id}/interrupt` + `GET /api/agent/sessions/{id}` 的 `turn_running`；runtime `interrupt_turn`（先撤确认 → 取消本轮 → 再撤一次）；`ConfirmationService.abort_session_calls`（pending 确认按拒绝终结、在跑 worker 取消并落 `failed` + `AGENT_TURN_INTERRUPTED`）；`QuestionService.cancel_pending_for_session` + `QuestionStatus.CANCELLED`；中断收尾（部分文本 + 中断标记、tool 桩补齐历史、SSE `interrupted` + `message_end`、释放会话锁）；前端流式期间「停止本轮」按钮、刷新后由 `turn_running` 恢复停止入口、修掉 abort 后 `running` 卡死的旧 bug；文档 8 篇同步。
- 遗留（交用户裁决）：
  - `scripts/generate_api_guide.py` 把所有 `CONFIRMATION_`/`AGENT_`/`TOOL_`/`LLM_` 前缀的错误码描述统一替换成「预留错误码；对应功能未交付（M11/M13）」（19 行，含 M21/M22 的新码），是 M10 时期的既有逻辑 + 一条断言它的测试（`tests/test_api_guide_generator.py::test_unreleased_error_families_have_neutral_descriptions`）。这些功能早已交付，描述已失真；本次未改（动它要删那条测试并重生成 docs/14，属 M10 的裁决面）。
  - `docs/05 §6` 的「共 93 条 API 端点」自 M11 起从未维护（真实 operation 101，小节和 117），与 M18/M21 一样未动。
  - 标题生成窗口内 `turn_running=false`；assistant 文本消息在「取消正好落在其落库中途」时理论上去重不覆盖（不影响请求结构合法性）。


## 收尾（tag `v2-m22` 之后，分支 `m22-doc-followups`）

用户裁决：「这两个遗留都修复。」第二条比记录里写的严重，另发现第三条同类问题。详见 docs/13 §8.28。

1. **`scripts/generate_api_guide.py`**：删掉 M10 的「未交付家族统一改写」（`CONFIRMATION_`/`AGENT_`/`TOOL_`/`LLM_` 四前缀一律写成「预留错误码；对应功能未交付（M11/M13）」，19 行），错误码说明逐字取自 docs/05；`tests/test_api_guide_generator.py` 里断言该改写的那条测试改成反向钉住「四族必须保留 docs/05 原文」；连带修 docs/14 两处过期陈述（「`confirm_request` 等 M10 不会发布」、「§7 交付范围停在 M10」）。
2. **`docs/05 §6`**：实测不是数字漂移而是清单失真——21 条行描述不存在的端点、6 个已实现端点没被记录、6 处计数错。已改写成真实清单（删 21 / 补 6 / 总数 93 → 101），新增门禁 `tests/domain/test_route_inventory.py`（8 条断言，纯文件读取 docs/05 §6 + `web/openapi.json`）。
3. **`docs/05 §12`**：旧端点「保留 410 存根」是假陈述（M3 起一律不注册旧 router），改成不保留存根 + 迁移对照表，并如实标注 `ENDPOINT_REMOVED` 没有路径会抛出。
4. **遗留（待用户裁决）**：`ENDPOINT_REMOVED` 按本仓标准应删除（连带 `ERROR_HTTP_STATUS`、docs/05 §4 一行、总数 105 → 104、docs/14 与两处计数门禁）；本次只在文档加注。

验证（主 agent 复跑）：`tests/domain/test_route_inventory.py` 8 passed；`tests/domain tests/api tests/test_api_guide_generator.py` 714 passed；全量 `1954 passed, 4 skipped` 退出码 0；`dump_openapi.py --check`、`generate_api_guide.py --check` 退出码 0；docs/14 的「预留错误码」从 19 行降到 0；`maa_api/**` 与 `web/**` diff 为空。
