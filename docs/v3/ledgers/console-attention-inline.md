# 指挥台「待你处理」进主会话（维护性补丁台账）

- 分支：`console-attention-inline`（合并后删除）；基点：`dev` = `121a3d6`；合并：`7e433ec`（`dev` 上的 `merge: 指挥台待你处理进主会话（维护性补丁，见 docs/v3/13 §8.176）`）；tag：无（维护性补丁，不新增里程碑）
- spec：[console-attention-inline](../specs/console-attention-inline.md)
- 计划：[console-attention-inline](../plans/console-attention-inline.md)
- 决策记录：[docs/v3/13 §8.176](../13-决策记录.md)
- 用户决定（2026-11-09）：所有「待你处理」都要在主会话中体现并处理；三种位置方案（会话流尾部一段 / 会话头部常驻折叠区 / 挂到来源事件卡）中选**会话流尾部一段**。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `4457757` | 规格与实施计划入库（非里程碑补丁，不改 12 的路线与顺序） |
| T1 | `621f18f` | 「待你处理」搬进总控会话内容尾部：新增 `AttentionFeed`；`AttentionList` 收敛为纯列表；`ConsolePage` 两套布局删掉右栏面板 / 手机汇总行与底部面板；删除 `AttentionPanel`、`AttentionSheet`、`AttentionSummaryBar`；`tailKey` 加入待办签名；测试改写到新结构（含手机端、空态、实时副本去重）；11 §3.1/§3.2/§3.3/§5/§10 同步 |
| T2 | `201c980` | 记忆就地审核：新增 `AttentionMemoryCard`（展开逐条批准/拒绝，改后批准去记忆页）；假后端补记忆审核端点与 `memoryReviews()`；新增「审核成功」「409 不卡死其它行」用例；11 §3.2/§11 同步 |
| T3 | `118b434` | 工单就地回复：新增 `AttentionTicketCard`（标题可点、`human_action.prompt`、就地回复、打开工单）；发送走 `streamSse` detach，成功 Toast「已发给工单」，失败与 409 就地提示；假后端补 `ticketMessagePosts()`；4 条用例；11 §3.2/§11 同步 |
| T4 | 本台账所在提交 | 02 §4.2、13 §8.176、台账、全量验证与合并 |

## Rulings I made

| # | 裁决 | 理由 | 代价 |
|---|---|---|---|
| R1 | 状态分支上移到 `AttentionFeed`（空态不渲染、读取失败可重试、resolver 错误在列表清空后仍渲染） | 原 `AttentionList` 同时负责列表与页面状态；搬进会话尾部后空态文案会像一条消息 | 纯列表组件不再自带加载/错误态，调用方必须传完整 `AttentionState` |
| R2 | 确认/提问/计划/告警四类卡片与 resolver 一行未改；`excludeIds` 去重口径不变 | 已有能力与实时副本去重经过测试覆盖，改动风险大于收益 | 实时副本里的挂起卡仍只在实时副本处出现 |
| R3 | 记忆审核不做「改后批准」/编辑，行内只有批准/拒绝，第二入口去记忆页 | 严格对齐用户「就地处理」诉求，同时避免把记忆编辑表单搬进会话尾部 | 想改后批准仍要离开主会话 |
| R4 | 工单回复按钮视觉文案「发送」、读屏名「发送回复」 | 页面同时有总控输入栏的「发送」，避免同名按钮 | 文案要点里「发送」按钮实际可读名不同 |
| R5 | 工单卡无 `human_action.prompt` 时只给「打开工单」，不显示「回复」 | 没有提问可答时回复没有语义；blocked 工单的处置入口仍是工单页 | 这类卡片不能就地处理（与规格 §2 一致） |
| R6 | 手机工具条只保留进行中工单胶囊；不再有「N 件事等你处理」入口 | 待办已在会话尾部可见，保留双重入口违背单一位置 | 上滑看历史时要靠「回到底部」回到待办 |

## 验收

写者全部停止后，主 agent 独占复跑（长输出在 `/tmp/lead-*.log`）：

| 检查 | 结果 |
|---|---|
| 前端 `pnpm typecheck` | 0 |
| 前端全量 `pnpm test --run` | 112 文件 / 984 用例全绿 |
| 前端 `pnpm build` | 0（末段 `[sw_harness] SERVICE WORKER OK`） |
| `scripts/frontend_smoke.py`（先 build） | 0，末行 `SMOKE OK` |
| `scripts/dump_openapi.py --check` | 0（契约未变） |
| `cd web && pnpm gen:api:check` | 0 |
| 后端全量 `pytest -o addopts= -q` | **3 failed / 5243 passed / 5 skipped / 2 xfailed**（514.48s）；3 条失败是既有真资源用例基线（见下） |
| `scripts/check_doc_links.py` | 末行 `BROKEN 0` |
| 残留 grep | `AttentionPanel\|AttentionSheet\|AttentionSummaryBar` 在 `web/src` 零命中；`件事等你处理` 仅剩 ConsolePage.test 里一条「不存在」断言 |
| 合并 | `dev` 上 `git merge --no-ff` = `7e433ec`；合并后工作区干净、分支已删除 |

已知失败（非本补丁引入）：`tests/services/test_activity_index.py` 2 条 + `tests/services/test_stage_layout.py` 1 条，依赖 `resource/` 真资源数据，m63 起的既有基线。

## 备注

- 本补丁只动前端与文档；后端端点、SSE/WS 事件、错误码、配置、表与迁移零改动。
- `docs/copy-audit/` 文案基线按 13 §8.175 口径不重设（既有基线差异非本补丁引入，`copy_inventory.py --check` 不作为本补丁门禁）。
- 运行中控制台上的 4 件待办（14 条记忆、3 张工单）改完后即可在指挥台直接处理；其中「更新活动资源」缺 Worker 工具、「每日免费单抽」验证窗口已过期属于运行侧问题，不在本补丁范围。
