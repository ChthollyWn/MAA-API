# 提问不再过期与工单卡选项优先（维护性补丁台账）

- 分支：`questions-no-expiry`（合并后删除）；基点：`dev` = `26d61b7`；合并：待填；tag：无（维护性补丁，不新增里程碑）
- spec：[questions-no-expiry](../specs/questions-no-expiry.md)
- 计划：[questions-no-expiry](../plans/questions-no-expiry.md)
- 决策记录：[docs/v3/13 §8.177](../13-决策记录.md)
- 用户口径（2026-11-09）：「工单卡选项优先，提问取消有效期设置，不再自动失效」；「不再自动失效」经选择为 **B：跨重启保留 pending，作答时把答案投递给来源会话**。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `1b7c725` | 规格与实施计划入库（非里程碑补丁，不改 12 的路线与顺序） |
| T1 | `37bbab1` | 后端：删 `agent.question_timeout_seconds`、过期扫描、重启/关闭清理；`wait` 无超时；`answer` 后在「无等待者且来源运行已结束」时调用投递回调；新增 `cancel_pending_for_ticket` 与工单结束监听、总控重置取消旧会话提问；迁移 `0040_question_expiry_historical`（`expires_at` 改历史列、删过期扫描索引）；提问 wire 去掉 `expires_at`；`QUESTION_EXPIRED` 只服务历史行；测试与评测同步（删 `legacy-question-timeout` 场景与 cassette） |
| T2+T3 | `194f0e9` | 前端：`QuestionCard` 去倒计时与本地过期；提问排序改 `created_at`；`AttentionTicketCard` 选项优先（关联历史失效提问选项 → 类型固定按钮 → 自由文本，「其他回复」保留文本入口）；新增历史失效提问查询；测试同步 |
| T4 | 本台账所在提交 | 文档 03/04/05/09/10/11/13/14 同步、全量验证与合并 |

## Rulings I made

| # | 裁决 | 理由 | 代价 |
|---|---|---|---|
| R1 | 投递判定两层：本进程等待者 → 不投递；等待者不在且来源运行仍 `running`/`waiting_human` → 不投递；运行已终态/不存在/无 run_id → 投递 | 等待轮会自己拿答案；运行仍活跃时投递会与抢在 `wait` 注册前的竞态重复触发 | 依赖运行状态表的终态口径 |
| R2 | 服务重启/关闭不改 pending；工单结束、总控重置、会话中断仍撤回 | 重启后等待轮已不存在但提问仍可作答；后三者是「不再需要回答」而不是按时间失效 | 收尾路径多两处装配钩子 |
| R3 | `skip` 不投递；投递内容是一句用户消息，不是结构化载荷 | 跳过表示不回答，没有要交给新运行的裁决 | 新运行只能从消息文本理解答案 |
| R4 | 迁移不删 `expires_at` 列，只改可空并删 `ix_agent_question_status_expires_at`；降级用 `created_at` 回填 | 历史提问可回放；删列没有业务收益且降级无法恢复原时间 | 表中留一列历史数据 |
| R5 | 0040 被本补丁占用，M86 起建议迁移号顺延为 0041–0047（03 §2） | 迁移编号按实际顺序取下一个可用值 | 03 §2 表与顺延说明同步 |
| R6 | 删除评测场景 `legacy-question-timeout` 与 cassette；假世界默认设置去掉该键；harness 黄金记录去掉 `question_required.expires_at` | 「提问超时」行为整体删除，旧场景无法再通过 | 评测覆盖少一条 v2 复盘 |
| R7 | 工单卡历史提问回填只用于 waiting/blocked、只取最新一条「单选、有选项」的提问；多选或无选项回落文本 | 卡片上不做多选提交，也不猜开放式问题的选项 | 部分历史工单仍只能自由文本 |
| R8 | 固定按钮文案「批准/拒绝」（confirmation）与「同意/不同意」（decision），答复内容就是按钮文案 | 选项优先、少打字；现有发消息链路不变 | 语义靠 Worker 理解短文本 |

## 验收

写者全部停止后，主 agent 独占复跑（长输出在 `/tmp/lead-*.log`）：

| 检查 | 结果 |
|---|---|
| 前端 `pnpm typecheck` | 0（`pnpm build` 内含，单独跑也 0） |
| 前端全量 `pnpm test --run` | 112 文件 / 986 用例全绿 |
| 前端 `pnpm build` | 0（末段 `[sw_harness] SERVICE WORKER OK`） |
| `scripts/frontend_smoke.py`（先 build） | 0，末行 `SMOKE OK` |
| 后端全量 `pytest -o addopts= -q` | 待填 |
| `scripts/dump_openapi.py --check` | 0（契约未变） |
| `cd web && pnpm gen:api:check` | 0 |
| `scripts/check_doc_links.py` | 末行 `BROKEN 0` |
| 残留 grep（`question_timeout_seconds`、提问相关 `expires_at`） | 代码零残留；`docs/v3/09`、`14` 各一处是「已删除」说明；`types.ts` 剩下的 `expires_at` 属于确认卡与原子授权 |
| 合并 | 待填 |

已知失败（非本补丁引入）：`tests/services/test_activity_index.py` 2 条 + `tests/services/test_stage_layout.py` 1 条，依赖 `resource/` 真资源数据，m63 起的既有基线。

## 备注

- 本补丁只动提问链路、指挥台工单卡与相关文档；确认链路、`ask_user` 参数、IM 通道不动。
- 历史 6 条 expired 提问保留可查询；工单卡按 `origin.ticket_id` 回填其选项，不复活成 pending。
- `human_action.options` 契约不在本补丁范围（未来若要 Worker 直接给候选，另开任务）。
