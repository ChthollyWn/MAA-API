# 提问不再过期与工单卡选项优先（维护性补丁规格）

> 非里程碑补丁：按维护性改造处理，不占用新里程碑编号，不调整 [12-实施路线](../12-实施路线与里程碑.md) 的顺序。
> 本文是本次改造的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。
> 用户口径（2026-11-09）：**工单卡选项优先；提问取消有效期设置，不再自动失效**。其中「不再自动失效」经确认选 **B：跨重启保留，作答时把答案投递给来源会话**。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 最新提交上开分支 | `git status -sb` | `## questions-no-expiry` |
| 2 | 提问超时仍由配置控制 | `grep -rn "question_timeout_seconds" maa_api/settings.py maa_api/main.py` | 各有匹配 |
| 3 | 过期扫描与重启清理仍在 | `grep -n "expire_due\|reset_after_restart\|_expiry_loop" maa_api/agent/questions.py` | 有匹配 |
| 4 | 提问 wire 仍带 expires_at | `grep -n "expires_at" maa_api/agent/questions.py maa_api/agent/harness/builtin_middleware.py` | 各有匹配 |
| 5 | 工单卡仍是自由文本回复 | `grep -n "回复工单" web/src/features/console/AttentionTicketCard.tsx` | 有匹配 |
| 6 | 已过期提问仍可查询 | `curl -s "http://127.0.0.1:8002/api/agent/questions?status=expired&size=50"`（带 token） | 有 6 条历史过期提问 |
| 7 | 后端全量测试基线 | `.venv/bin/python -m pytest -o addopts= -q` 尾部 | 3 failed / 5243 passed（真资源基线） |

## 1. 背景与目标

`ask_user` 提问目前默认 600 秒超时（`agent.question_timeout_seconds`）：到点把提问置 `expired`、结束本轮（`finish_reason=question_unanswered`）。指挥台「待你处理」里提问卡随之消失，只剩工单那句 prose `human_action`，卡片退化成自由文本框——2026-11-09 的 14 条待审记忆 + 3 张 waiting/blocked 工单就是典型：EX 关与每日单抽都提过带选项的问题，但 10 分钟后过期，用户回来只能看到「回复」输入框。

做完后：

1. **提问不再按时间失效**：删除 `agent.question_timeout_seconds` 配置、过期扫描、本地倒计时与提问 wire 里的 `expires_at`；pending 提问一直保留到被回答、跳过或生命周期收尾。
2. **跨重启保留**：服务重启/关闭不再把 pending 提问置终态。重启后用户仍能看到卡片并作答；若原运行已不在（重启把运行标成中断），答案作为消息投给来源——工单提问发给该工单 Worker，总控提问回到总控会话——由新的一轮接着办。
3. **工单卡选项优先**：`waiting` / `blocked` 工单卡优先用与该工单关联的最近一条**已失效**提问（历史遗留）的选项渲染按钮；没有可回填的提问时按 `human_action.type` 给出按钮——`confirmation` →「批准 / 拒绝」，`decision` →「同意 / 不同意」；`question` 无选项时保留自由文本。所有答复仍写成工单消息发给 Worker（与当前回复同一条链路）。
4. **生命周期收尾**：会话中断仍撤回该会话 pending 提问（现状）；工单结束取消该工单的 pending 提问；总控「重新开始会话」取消旧会话的 pending 提问。这些是「不再需要回答」，不是按时间失效。
5. 后端只新增一小段投递回调与两个收尾钩子；确认链路、`ask_user` 工具参数、IM、前端其余交互不变。

## 2. 非目标

- 不改确认（confirmation）的超时与语义；`agent.confirmation_timeout_seconds` 保留。
- 不做历史已过期提问的自动「复活」成 pending：只在工单卡上回填其选项（展示层），不改数据库状态。
- 不新增 `human_action.options` 契约（Worker 在 waiting/blocked 汇报里仍只写 type/prompt/ref_id；未来若需要结构化选项另开任务）。
- 不把提问做成持久会话消息，也不改 SSE/WS 事件名与其余字段。
- 不改 IM 通道的提问转述口径；不改前端其它页面。

## 3. 相关文档与代码

- [03-数据模型](../03-数据模型.md)（`agent_question`）、[04-接口与事件协议](../04-接口与事件协议.md)（`question_required`、提问 wire）、[05-运行时与代理定义](../05-运行时与代理定义.md)（`ask_user`/等待）、[08-主动性与通道](../08-主动性与通道.md)（一次一问）、[09-安全与治理](../09-安全与治理.md)（提问超时行）、[10-可观测与评测](../10-可观测与评测.md)（假世界默认设置）、[11-前端指挥台](../11-前端指挥台.md)（待你处理与提问卡）、[14-现行接口清单](../14-现行接口清单.md)（§4.11 提问）。
- 代码入口：`maa_api/agent/questions.py`、`maa_api/db/repositories/question.py`、`maa_api/db/models.py`（`AgentQuestion`）、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/main.py`、`maa_api/agent/harness/builtin_middleware.py`（`ClarificationMiddleware`）、`maa_api/agent/tickets/service.py`（结束监听）、`maa_api/agent/orchestrator/service.py`（`reset`）。
- 前端入口：`web/src/features/agent/QuestionCard.tsx`、`format.ts`、`types.ts`；`web/src/features/console/attention.ts`、`AttentionTicketCard.tsx`、`api.ts`；`web/src/api/keys.ts`；`web/src/features/console/test-utils.tsx`。
- 测试：`tests/agent/test_question_service.py`、`tests/api/test_questions_router.py`、`tests/agent/test_runtime.py`、`tests/evals/`、`web/src/features/agent/QuestionCard.test.tsx`、`web/src/features/console/*.test.*`。

## 4. 设计

### 4.1 提问生命周期（无时间失效）

- `QuestionService.create`：不再计算 `expires_at`；wire 去掉 `expires_at` 字段；其余（options 归一、审计同事务、广播）不变。
- `QuestionService.wait`：只等「终态事件 + 每 0.25 秒重读库」，没有 deadline；被回答、跳过、取消（会话中断 / 工单结束）都会返回终态 wire。
- `answer` / `skip`：去掉「已过时间就就地置过期」的分支；其余 404 → 终态 409 → 选择校验的顺序不变。历史 `expired` 行仍在 `_terminal_error` 里返回 `QUESTION_EXPIRED`（只读兼容）。
- 删除：`expire_due`、`_expire_now`、`_expire_all`、`_expiry_loop`、`start()` 的扫描、`reset_after_restart`、`close()` 的清理、`_is_overdue`、`DEFAULT_QUESTION_TIMEOUT_SECONDS`、`EXPIRED_SUMMARY`/`RESTART_SUMMARY`/`SHUTDOWN_SUMMARY`。
- 服务重启/关闭：**不动 pending 提问**（`main.py` 不再调用 `reset_after_restart` / `start` / `close`）。
- 审计：`expired` 仍是历史审计终态（不新增产生路径）。

### 4.2 跨重启作答投递

新增可选回调 `QuestionService.set_answer_delivery(callback)`。`answer()` 在提交、审计、广播之后判断：

| 条件 | 行为 |
|---|---|
| 本进程有该提问的等待者（`_resolution_events` 里有它） | 不投递：等待轮照旧被唤醒并拿到答案 |
| 等待者不在，但 `origin.run_id` 的运行仍是 `running` / `waiting_human` | 不投递：运行随后会读到已答状态（覆盖「用户抢在 `wait` 注册前作答」的竞态） |
| 等待者不在，且来源运行已终态 / 不存在 / 没有 run_id | 调 `callback(wire)`：把答案作为消息投给来源会话 |

投递内容与路由（装配层实现，`main.py`）：

```text
用户回答了之前的提问「<question>」：<selection 用、连接>
```

- `wire.origin.ticket_id` 存在 → `WorkerService.post_user_message(ticket_id, text, [], notify_content=text)`（工单消息，唤醒 Worker）。
- 否则 → `OrchestratorService.post_user_message(text, [])`（写进总控会话，唤醒总控新一轮）。
- 投递失败（工单已结束、服务未就绪等）只记 warning，不影响回答已经落库的事实。

`skip` 不投递：用户选择不回答，没有要传给新运行的裁决。

### 4.3 生命周期收尾

- 会话中断：`cancel_pending_for_session` 保持不变（中断撤回该会话 pending 提问）。
- 工单结束：`TicketService.add_end_listener` 注册一个监听器，调用新增的 `QuestionService.cancel_pending_for_ticket(ticket_id)`（仓储新增按 `origin_ticket_id` 批量取消）。审计 `rejected`，摘要「工单已结束，提问已取消」。
- 总控「重新开始会话」：`OrchestratorService.reset` 在归档旧会话后调用 `cancel_pending_for_session(old_id)`（失败只记日志）。
- 这些收尾取消的提问不再可答，属于「不需要回答」，与「按时间失效」区分。

### 4.4 存储与契约

- `agent_question.expires_at` 保留为**历史列**：迁移 0040 把它改为可空（`downgrade` 恢复非空并按 `created_at` 回填），模型字段改可选；新提问不再写入。
- 提问 wire 去掉 `expires_at`（REST 列表/详情、SSE `question_required`、前端类型三处同步）；OpenAPI 里提问端点是 `dict` 形状，快照不变。
- `QUESTION_EXPIRED` 错误码保留：只服务历史 `expired` 行（现在不会产生新的）。
- 前端不再显示倒计时/本地过期：`QuestionCard` 删掉 deadline、计时器、剩余时间文案与 `expired` 本地终态（409 `QUESTION_EXPIRED` 归入「已结束」）。

### 4.5 指挥台排序

提问不再有 `expires_at` 排序键。`buildAttentionItems` 里提问组改为按 `created_at` 升序（最早的在最前面），读不出时间排最后；确认组仍按过期时间升序。其余分组与去重不变。

### 4.6 工单卡选项优先（`AttentionTicketCard`）

按优先级取选项：

1. **关联的历史失效提问**：`GET /api/agent/questions?status=expired&size=50` 里 `origin.ticket_id === ticket.id` 的最近一条（按 `created_at` 取最新），且有选项 → 逐条渲染选项按钮（label + description）。
2. **按 `human_action.type` 的固定按钮**：`confirmation` →「批准」「拒绝」；`decision` →「同意」「不同意」；`question` 不提供固定按钮。
3. 没有可用的按钮时：直接显示自由文本输入（现状）。
4. 有按钮时：按钮行 + 「其他回复」链接展开自由文本；「打开工单」始终保留。

点按钮 = 把选项 label / 按钮文案作为消息发给该工单（复用现有 `streamSse(..., {detach: true})` 发送与失效逻辑），Toast「已发给工单」，失败提示「发送消息失败：原因」。多选提问不渲染按钮（回落自由文本），避免在卡片上做多选提交。

### 4.7 配置删除

删除 `agent.question_timeout_seconds`：`settings.py` 的 `SETTING_KEYS` 与 `AgentSettings` 字段、`services/settings_schema.py` 条目、`main.py` 的构造参数；文档 [09 §5](../09-安全与治理.md)、[10 §280](../10-可观测与评测.md)、[14 §4.11](../14-现行接口清单.md) 同步。`config.template.yaml` 没有这个键（已核对）。

### 4.8 边界情况

| 输入 / 状态 | 期望 |
|---|---|
| 提问一直没人答 | 卡片一直挂；服务重启后仍在，可继续作答 |
| 重启后作答（来源运行已中断） | 答案投递给来源会话；提问置 answered；其它客户端经 WS 刷新 |
| 作答时等待轮还活着 | 不投递，等待轮拿到答案继续（现状） |
| 用户抢在 `wait` 注册前作答 | 运行仍在 running → 不投递；`wait` 读到终态直接返回 |
| 工单先结束、后再答该提问 | 工单结束时提问已取消（终态），答了返回 409 |
| 总控重新开始会话 | 旧会话 pending 提问被取消，不迁移到新会话 |
| 历史 expired 提问 | 仍可查询；作答返回 409 `QUESTION_EXPIRED`；工单卡可回填其选项 |
| 工单 `human_action` 无对应提问 | 按 type 给固定按钮；`question` 类型自由文本 |
| 提问没有选项 | 不渲染选项按钮（现状不可能，防御） |

## 5. 测试

后端：

- `tests/agent/test_question_service.py`：改写超时用例；新增「wait 无超时直到回答/跳过/取消」「重启不动 pending」「answer 在等待者存在时不投递」「answer 在运行中断后投递」「工单结束取消提问」「wire 不含 expires_at」。
- `tests/api/test_questions_router.py`：wire 字段、过期行 409、无超时语义。
- `tests/agent/test_runtime.py`：删除 `question_timeout_seconds` 注入与依赖超时的用例，改为 skip / 正常回答。
- `tests/evals/`：删掉 `legacy-question-timeout` 场景与 cassette（行为已不存在），假世界默认设置去掉该键。
- `tests/db/test_migrate.py`：覆盖 0040 升级/降级。
- 全量 `pytest -o addopts= -q`。

前端：

- `QuestionCard.test.tsx`：删倒计时/本地超时用例，补「没有 expires_at 也一直可答」。
- `ConsolePage.test.tsx`：工单卡固定按钮（confirmation / decision）、历史过期提问选项回填、其他回复仍可用。
- `attention.test.ts`：提问按 `created_at` 升序。
- 命令：`pnpm typecheck && pnpm test --run && pnpm build`；`scripts/frontend_smoke.py`。

## 6. 文档同步

- 03 数据模型：`agent_question.expires_at` 标为历史列、不再写入。
- 04 接口与事件：`question_required` 载荷去掉 `expires_at`。
- 05 运行时与代理：`ask_user` 说明里的等待/超时措辞。
- 08/09/10/11/14：提问不再超时、假世界默认设置、工单卡选项优先、§4.11 提问语义与 wire。
- 13 决策记录：新增 §8.177。
- 台账 `docs/v3/ledgers/questions-no-expiry.md`、spec、plan。

## 7. 验收

| 检查 | 期望 |
|---|---|
| 后端全量 pytest | 除既有 3 条真资源失败外无新增失败 |
| 前端 typecheck / test / build | 全绿 |
| `scripts/frontend_smoke.py` | `SMOKE OK` |
| `scripts/dump_openapi.py --check`、`pnpm gen:api:check` | 0 |
| 文档断链 | `BROKEN 0` |
| 残留 grep | `question_timeout_seconds`、`expires_at`（提问相关）在 `maa_api/` 与 `web/src/` 零残留（历史列迁移与文档说明除外） |
| 手工验收（用户） | 待你处理卡片在重启后仍存在；提问卡没有倒计时；工单卡直接给选项/按钮，不用打字 |

## 8. 后续（不在本补丁）

- `human_action.options` 契约：Worker 在 waiting/blocked 汇报里直接给候选，工单卡不再依赖历史提问。
- 历史 6 条 expired 提问的清理策略（当前保留可查询、可回填，不复活）。
