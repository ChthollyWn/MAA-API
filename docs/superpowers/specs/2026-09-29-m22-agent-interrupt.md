# M22 · 中断 Agent 正在执行的一轮

> 关联：[11-Agent模块设计](../../11-Agent模块设计.md) §5、§8，[09-前端重构方案](../../09-前端重构方案.md) §6.4，[13-决策记录](../../13-决策记录.md) §8.27。实施分支 `m22-agent-interrupt`，从 `refactor/v2` 新建。

## 1. 目标与用户裁决

用户裁决（2026-09-29）：「agent 执行过程中，现在完全不能中断，需要加上中断功能。」同轮确认的三条：

1. **只停 Agent 本轮**：游戏里已经跑起来的流水线继续（要停它仍用显式的「停止流水线」/`stop_pipeline`）。
2. **彻底撤回本轮在等、在跑的工具**：还在等确认的直接撤掉（不会在用户批准后突然执行）、`wait_pipeline` 与提问这类等待立刻结束，审计记为已中断；**已经提交进队列的任务保留**。
3. **刷新后仍能停**：后端记住「本会话有一轮在跑」，重开页面/换设备也能按停止。

现状（M16 起的刻意决定）：`docs/11 §8.5` 写着「不做停止生成」，理由是 SSE 断开后后端这一轮仍在 detached producer 里继续跑，纯前端的停止按钮只会断开显示、不会停止执行。于是：执行期间输入栏发不出消息（`AGENT_SESSION_BUSY` 409），用户只能干等 `wait_pipeline` 之类挂到超时，截图里的情况就是如此。本里程碑把「停止」做成**真的中断后端执行**，并改写那条决定。

## 2. 范围

### 2.1 本里程碑

1. runtime 会话级中断：`interrupt_turn(session_id)` + `turn_running(session_id)`（新增 `_active_turns: dict[str, Task]`，进程内真相源）。
2. 中断语义：停 LLM 流 → 撤回本轮在等/在跑的工具 → 落一条带 `finish_reason="interrupted"` 的助手消息 → 释放会话锁 → SSE 收尾（新增 `interrupted` 事件）。
3. `ConfirmationService.abort_session_calls(session_id)`：把该会话仍 pending 的确认置 `rejected`（等于「不批准」，永不执行）、取消在跑的工具 worker 并把审计置为已中断、唤醒等待方。
4. `QuestionService.cancel_pending_for_session(session_id)` + 新终态 `QuestionStatus.CANCELLED`。
5. 新 REST：`POST /api/agent/sessions/{session_id}/interrupt`（幂等）；`GET /api/agent/sessions/{session_id}` 增加 `turn_running`。
6. 新错误码 `AGENT_TURN_INTERRUPTED`（409，落在被取消调用的审计 `error_code` 上）。
7. 前端：执行期间发送按钮原地变「停止」（`CircleStop` + `destructive-soft`），刷新后由 `turn_running` 恢复停止入口；中断后本轮正常收尾、输入栏立刻可用；`AGENT_SESSION_BUSY` 文案写清「可以停止」。
8. 生成物与文档同步。

### 2.2 不做

- **不动流水线与队列**：中断不调 `stop_pipeline`、不取消任何已入队的任务（用户裁决 §1.1）。
- **不取消已批准并正在执行的确认**（无法回滚副作用）之外的东西——已批准的确认**要**取消它的 worker（裁决 §1.2），但工具可能已经产生副作用，这一点在文档里如实写明，不自称「可回滚」。
- **不做「暂停/恢复」**：中断是终止本轮，不是挂起后可继续。
- **不改被动断开的行为**：客户端断网/关页面仍不取消本轮（那是 M16 的既定语义，用户裁决只要求「主动中断」）。两者的区别在测试里必须显式区分。
- **不改自动诊断的中断语义**：`run_incident` 也登记进 `_active_turns`（同一个端点可中断），但它的 CancelledError 文案不能沿用「服务关闭」那套。

## 3. 设计

### 3.1 runtime：中断与状态

- `_active_turns: dict[str, asyncio.Task]`，在 `_run_turn` 入口登记 `asyncio.current_task()`（覆盖 `run_incident`），在 finally 注销。
- `turn_running(session_id) -> bool`：进程内有活动 task 即为真。
- `async def interrupt_turn(session_id, *, confirmation_service, ...) -> bool`：
  1. 若该会话没有任何活跃 task：若锁仍在（`acquire_session` 之后、producer 登记之前的窗口），释放锁并返回 `False`；否则直接返回 `False`。
  2. 先 `confirmation_service.abort_session_calls(session_id)`（**必须先撤确认**：`_wait_and_execute` 只认状态，用户批准就会执行，与本轮是否还在等无关）。
  3. 取消 producer task（`task.cancel()`），`await asyncio.wait_for(task, timeout=5)` 等它收尾（吞掉 `CancelledError`）。
  4. 再撤一次（幂等）：取消窗口期内刚创建的确认/审计也要被撤掉。
  5. 返回 `True`。
- **中断收尾**（`_produce_turn` / `_run_turn` 的取消分支，`session_id` 在 `self._interrupted` 集合里才当作主动中断，否则原样 re-raise）：
  - 攒到的部分助手文本 + 「本轮已被你中断。」一起落一条 `role="assistant"`、`finish_reason="interrupted"` 的消息（没有部分文本时就只落标记），**不重复 emit 已经流出去的那段文本**；
  - 若中断发生在工具批次中：为这批里尚未落库的调用补写 tool 消息（内容 `{"error": {"code": "AGENT_TURN_INTERRUPTED", "message": "本轮已被中断，该调用未完成"}}`）。**这是硬要求**：assistant 的 `tool_calls` 缺对应 tool 消息会让下一轮请求被上游 400（M20 起历史不再裁剪）；
  - 发 SSE `interrupted`（`{"session_id", "finish_reason": "interrupted"}`），再走既有 `message_end`；
  - `_produce_turn` 的 finally 照旧释放锁并推 `_SSE_END`，所以 SSE 流自然收尾。
- 被取消的调用**不重试**：中断后模型没有发言机会。

### 3.2 `ConfirmationService.abort_session_calls(session_id)`

按 `AgentAudit.session_id` 找 `status=pending` 的审计行（`Confirmation` 表没有 session 列，`payload.session_id` 只在 payload 里；审计行是可靠索引），逐条：

- 关联确认仍 `pending` → 走 `resolve(approved=False, resolved_by="system", reason="本轮已被中断")` 的效果：CAS 置 `rejected`、审计 `REJECTED`、广播 `confirm_resolved`、`event.set()`（worker 醒来看到非 approved 直接返回，**不执行**）。
- 关联确认已 `approved` 或没有确认（普通工具）→ `worker.cancel()`；`_execute_and_record` 必须捕获 `CancelledError` 把审计置为 `FAILED` + `error_code=AGENT_TURN_INTERRUPTED`，然后 re-raise（不捕会让审计永久 pending）。
- 顺序：先 CAS/拒绝，再 cancel，避免「拒绝期间被批准」。

### 3.3 `QuestionService.cancel_pending_for_session(session_id)`

CAS `pending → cancelled`（新增 `QuestionStatus.CANCELLED`）、审计 `REJECTED` + `result_summary="本轮已被中断，提问已取消"`、广播 `question_resolved`（`reason="cancelled"`）、唤醒等待方。中断时对挂起提问调用一次，吞掉终态冲突。

### 3.4 REST 与可观测性

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/sessions/{session_id}/interrupt` | 幂等；返回 `{"session_id", "interrupted": bool, "turn_running": bool}`；无活动轮次时 `interrupted=false`（200，不是错误） |
| GET | `/api/agent/sessions/{session_id}` | 响应新增 `turn_running: bool`（从 `app.state.agent_runtime` 读；没有 runtime 时为 `false`） |

`AGENT_TURN_INTERRUPTED`（409）只用于被取消调用的审计 `error_code`；错误码总数 104 → 105。

### 3.5 前端

- 执行期间发送按钮原地变「停止」（`CircleStop`，`aria-label="停止本轮"`，点击后显示 stopping 态）；点击 → `POST …/interrupt` → 本地 `abort()` 流 + **显式 `running:false`**（现在的 catch 分支会因为 `signal.aborted` 提前 return，把 `running` 永久留在 true，这个 bug 一并修）→ 失效 messages/session/sessions/待确认/待答查询。
- 刷新恢复：`AgentSession.turn_running` 为真且本地没有流式轮次时，输入栏状态行显示「本轮仍在后台执行」+ 停止按钮；会话详情查询在 Agent 页开着时按 10 秒轮询（与待确认/待答同节奏）。
- `AGENT_SESSION_BUSY`：发送被 409 挡下时提示「该会话仍有一轮在跑，可先停止它」。
- SSE `interrupted` 事件：把本轮标记为已中断（状态行/文案），不当作错误。

## 4. 影响与兼容

- `finish_reason` 新增 `interrupted`（列宽 24 够用，无枚举约束）；docs/04 §5.8 的取值清单同步。
- 审计可能出现 `status=failed` + `AGENT_TURN_INTERRUPTED`（工具被中断）与 `status=rejected`（确认被撤）。
- 中断是**主动**动作，只有走新端点才取消本轮；断网/关页面仍旧不取消（既有测试继续有效）。
- 已批准且已在执行的工具可能已经产生副作用：中断只保证「不再继续等它、不再有后续工具」，不保证回滚；文档如实写明。

## 5. 验收

- `.venv/bin/python -m pytest -q` 退出码 0；
- 新增测试：LLM 流式期间中断（关连接 + `finish_reason=interrupted` + 部分文本落库 + SSE `interrupted`/`message_end`）；等确认期间中断（确认被拒、审计 rejected、之后批准不再执行）；`wait_pipeline` 等待期间中断（worker 取消、审计 failed + `AGENT_TURN_INTERRUPTED`）；提问挂起期间中断（`cancelled` + 卡片查询不再返回）；无活动轮次中断 → `interrupted=false`；工具批次中途中断后**下一轮请求结构完整**（每个 `tool_calls` 都有对应 tool 消息）；中断后立刻能发下一条消息（锁已释放）；`turn_running` 在会话详情里的真假；**被动断开仍不取消**（既有用例保持不变）；
- 前端：停止按钮出现/点击/收尾、刷新后由 `turn_running` 恢复停止入口、409 文案，`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 退出码 0；
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 退出码 0；
- 行为实测（主 agent）：假 LLM 驱动一轮 → 在 `wait_pipeline` 工具挂在轮询时调中断端点 → 断言审计、会话锁、`turn_running`、历史结构四件事。

## 6. 文档同步

- docs/11：§8.2 事件表加 `interrupted`；§8.5 删掉「不做停止生成」并改写为中断语义（含「只停本轮、不动流水线」「已批准的工具可能已有副作用」）；§5 增补一句中断时确认被撤回。
- docs/09 §6.4：停止按钮、`turn_running` 恢复入口、409 文案。
- docs/05 §6.15：新端点 + `turn_running` 字段；§4.11 加 `AGENT_TURN_INTERRUPTED`、总数 105。
- docs/04 §5.8：`finish_reason` 取值加 `interrupted`；§5.17 提问状态加 `cancelled`。
- docs/12：M22 条目；docs/13 §8.27：三条用户裁决、「不做停止生成」被取代的理由、不可撤回的边界、实现裁决；docs/README 决策速查表对应行改写。
- docs/14 由脚本重新生成。
