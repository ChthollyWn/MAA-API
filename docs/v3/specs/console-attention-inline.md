# 指挥台「待你处理」进主会话（维护性补丁规格）

> 非里程碑补丁：按维护性改造处理，不占用新里程碑编号，不调整 [12-实施路线](../12-实施路线与里程碑.md) 的顺序。
> 本文是本次改造的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。
> 产品口径经用户 2026-11-09 确认：**所有「待你处理」都要在主会话（总控会话）里体现并处理**。三种位置方案（会话流尾部一段 / 会话头部常驻折叠区 / 挂到来源事件卡）中，用户选择**会话流尾部一段**。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 最新提交上开分支 | `git status -sb` | `## console-attention-inline`，工作区干净 |
| 2 | 「待你处理」当前在桌面右栏 | `grep -n "AttentionPanel" web/src/features/console/ConsolePage.tsx` | 有匹配 |
| 3 | 手机当前用底部面板 + 汇总行 | `grep -n "AttentionSheet\|AttentionSummaryBar" web/src/features/console/ConsolePage.tsx` | 各至少 1 行 |
| 4 | 记忆与工单条目当前只是链接 | `grep -n "to=\"/more/memories\"\|to={\`/tickets/\${ticket.id}\`}" web/src/features/console/AttentionList.tsx` | 各 1 行 |
| 5 | 就地操作的四类 resolver 已存在 | `grep -n "ConfirmationCard\|QuestionCard\|PlanApprovalCard" web/src/features/console/AttentionList.tsx` | 各 1 行 |
| 6 | 记忆审核与工单发消息接口已存在 | `.venv/bin/python -c "import json;d=json.load(open('web/openapi.json'));print('/api/agent/memories/{memory_id}/review' in d['paths'], '/api/agent/tickets/{ticket_id}/messages' in d['paths'])"` | `True True` |
| 7 | 后端契约本次不改 | `grep -n "send_ticket_message\|review_memory" web/openapi.json` | 有匹配（只读确认，不改） |

## 1. 背景与目标

M72 把确认、提问、计划、严重告警、待审核记忆与 waiting/blocked 工单合并成指挥台「待你处理」，桌面放在右栏 `AttentionPanel`，手机放在工具条汇总行 + 底部 `AttentionSheet`。其中确认、提问、计划、告警四类卡片可以就地处理，但**记忆和工单两类只是链接**：用户点「14 条记忆待审核」会跳到记忆页，点工单卡会跳到工单页，处理路径都离开了主会话。

2026-11-09 的实际情况：指挥台「待你处理」同时挂着 14 条待审核记忆与 3 张 waiting/blocked 工单卡（更新活动资源、通关活动 EX 关、每日免费单抽没领到）。其中 3 张工单卡在主会话里只有汇报正文，用户必须离开主会话才能答复；这与「所有待你处理都在主会话中体现并处理」的要求不符。

做完后：

1. 指挥台「待你处理」整块移到总控会话内容尾部（桌面与手机同一位置），随消息滚动；桌面右栏只剩「进行中的工单」，手机工具条只剩进行中工单胶囊。
2. 全部六类来源都在主会话里就地处理：确认（批准/拒绝）、提问（作答/跳过）、计划（批准/退回）、严重告警（标记已处理）、记忆（逐条批准/拒绝）、工单（回复 Worker / 打开工单）。
3. 记忆条目从「链接到记忆页」升级为可展开的就地审核；工单条目从「整卡链接」升级为卡片 + 就地回复。
4. 没有待办时整块不渲染，不占会话空间；读取失败时显示可重试的错误块。
5. 不新增或修改后端端点、SSE/WS 事件、错误码、配置键、表与迁移；`web/openapi.json` 与生成类型不变。
6. 实时副本里的挂起确认/提问仍在原来的位置渲染，且仍按 id 从「待你处理」里排除，不重复出现；滚动贴底与「回到底部」的行为沿用现有 `useConversationScroll`。

## 2. 非目标

- 不改 `buildAttentionItems` 的合并、去重与排序规则（来源、顺序、`excludeIds` 语义全部不变）。
- 不把待办写进消息历史：它是会话尾部的一块实时内容，不是 `agent_message`；不做待办深链、不做推送改向。
- 不接入观察记录来源（M81 前端尚未接入，见 13 §8.150 之前的记录），保持现状。
- 不改全局待确认面板 `ConfirmationDock`、标题栏「N 项待确认」胶囊、`?confirm=` 深链与 IM 审批口径。
- 不改确认、提问、计划、告警四类卡片的行为与版式；不改工单页与记忆页（两者仍是第二入口）。
- 不改 `08 §1.1` 的「一次一问」纪律与总控提示词；只是展示位置变化，不改变 Agent 行为。
- 不做记忆的「改后批准」/编辑/删除（留在记忆页）；不做批量审核；不做工单卡上的「取消工单」「停止本轮」。

## 3. 相关文档与代码

- [11-前端指挥台](../11-前端指挥台.md)：§3.1 三块与布局、§3.2 待你处理、§3.3 总控会话、§5 收件箱消息与事件卡片、§10 移动端布局、§11 文案要点。
- [08-主动性与通道](../08-主动性与通道.md) §1.4（指挥台只列什么）；[02-系统架构](../02-系统架构.md) §4.2 前端目录。
- 既有代码入口：
  - `web/src/features/console/ConsolePage.tsx`：桌面/手机两套布局、`useAttention`、`excludeIds`、`useConversationScroll`；
  - `web/src/features/console/attention.ts` 的 `buildAttentionItems`、`originLabel`、`AttentionItem`；
  - `web/src/features/console/use-attention.ts` 的 `useAttention`、`AttentionState`（确认/提问/计划/告警四个 resolver 已就位）；
  - `web/src/features/console/AttentionList.tsx`、`AttentionPanel.tsx`、`AttentionSheet.tsx`、`AttentionSummaryBar.tsx`；
  - `web/src/features/memory/api.ts` 的 `reviewMemory`、`invalidateMemories`、`describeMemoryError`、`KIND_LABELS`、`formatMemoryTime`；
  - `web/src/features/agent/stream.ts` 的 `streamSse(path, body, signal, onEvent, { detach: true })`；
  - `web/src/api/keys.ts` 的 `keys.agent.tickets.lists()`、`keys.agent.tickets.messagesAll(ticketId)`、`keys.agent.memories()`；
  - `web/src/features/console/test-utils.tsx` 的假后端与 `makeMemory`、`makeTicket` 等工厂。

## 4. 设计

### 4.1 位置与版式

新增 `AttentionFeed`：`Section title="待你处理" description={`${count} 件`}` 包住 `AttentionList`，放在**总控会话内容末尾**：

| 屏幕 | 位置 | 说明 |
|---|---|---|
| 桌面（≥ 1024px） | 左列 `总控会话` 的滚动内容里，`ConversationView` 之后 | 随消息滚动，不吸顶；区块上方一条分隔线（`border-t`）与上间距，和消息流区分 |
| 手机 | 会话内容末尾（`ConversationView` 之后、`Composer` 占位块之前，仍在 `总控会话` 区域内） | 随整页滚动，输入栏仍贴底；不与工具条争位置 |

同时做删除：

- 桌面右栏去掉 `AttentionPanel`，只留 `ActiveTicketsPanel`（进行中的工单）。
- 手机去掉 `AttentionSummaryBar` 与 `AttentionSheet`；工具条只在有进行中工单时渲染胶囊条。
- 删除 `AttentionPanel.tsx`、`AttentionSheet.tsx`、`AttentionSummaryBar.tsx` 三个文件；`ConsolePage` 里删除 `sheetOpen` 状态与「最后一张卡处理完自动收起」的 effect。

`AttentionList` 改为纯列表组件：只渲染 `attention.items` 与四个 resolver 相关的卡片，不再负责加载态/错误态/空态；这些状态由 `AttentionFeed` 负责：

| 状态 | `AttentionFeed` 行为 |
|---|---|
| `attention.isPending` 且没有数据 | 渲染空（不闪「正在读取」） |
| `attention.error` | 渲染错误块：`读取待你处理的事项失败：原因` + 重试按钮（沿用 `ErrorState`） |
| `items.length === 0` 且没有 resolver 错误 | 返回 `null`（整块不出现） |
| 有 resolver 错误（确认/计划/告警） | 即使列表已空也保留错误提示（沿用 `AttentionList` 现在的显示口径） |
| 有 items | 标题行 + 列表 |

### 4.2 卡片来源与就地操作

排序与合并沿用 `buildAttentionItems` 现状（确认 0 → 提问 1 → 记忆 2 → 告警 3 → blocked 工单 4 → 计划 5 → waiting 工单 6）。每类的呈现与操作：

| 来源 | 呈现 | 就地操作 | 接口 |
|---|---|---|---|
| 确认 | `ConfirmationCard`（不变） | 批准 / 拒绝 | `POST /api/confirmations/{id}`（现有 resolver） |
| 提问 | `QuestionCard`（不变） | 作答 / 跳过 | `POST /api/agent/questions/{id}`、`.../skip`（现有 resolver） |
| 计划 | `PlanApprovalCard`（不变） | 批准计划 / 退回计划 | 计划审批（现有 `planResolver`） |
| 严重告警 | 告警卡（不变） | 标记已处理 | `POST /api/agent/watchdog/alerts/{id}/resolve`（现有 resolver） |
| 记忆 | `AttentionMemoryCard`（新） | 展开后逐条批准 / 拒绝 | `POST /api/agent/memories/{id}/review` |
| 工单 | `AttentionTicketCard`（新） | 回复（发给 Worker）/ 打开工单 | `POST /api/agent/tickets/{id}/messages`（SSE，detach 不读流） |

### 4.3 记忆就地审核（`AttentionMemoryCard`）

入参 `{ memories: readonly MemoryItem[] }`（props 里只传 `status === 'proposed'` 的项，长度即文案里的 N）。

折叠态（默认）：

- 标题 `${count} 条记忆待审核` + 徽章「记忆」（沿用现有视觉）；
- 前 3 条 `content` 单行截断预览（沿用 `buildAttentionItems` 的 `previews` 规则，最多 3 条）；
- 操作行：「展开审核」按钮 + 「去记忆页」链接（`/more/memories`，第二入口）。

展开态：

- 逐条渲染：`KIND_LABELS[kind]` 徽章 + 正文（完整、可换行）+ 「批准」「拒绝」按钮；每条带 `data-memory-id`；
- 底部：「收起」按钮 + 「去记忆页」链接；
- 每条独立 busy：批准/拒绝请求未返回时两个按钮都禁用，只在该行显示 `Spinner`；同一时刻允许不同条目并行。

动作与失效：

- 批准：`reviewMemory(id, { approved: true })`；拒绝：`reviewMemory(id, { approved: false })`；
- 成功后 `invalidateMemories(queryClient)`：记忆页查询与 `attentionMemoriesQuery` 一起失效，该条从列表消失；全部审完时 `AttentionFeed` 因 items 为空而整块消失；
- 失败：该条下方 `InlineAlert` 显示 `describeMemoryError(error)`，其它条目不受影响；
- 不做 Toast（行消失即为反馈）；不就地支持「改后批准」。

### 4.4 工单就地回复（`AttentionTicketCard`）

入参 `{ ticket: TicketView; specTitles }`。卡片结构（不再整卡是链接）：

- 标题行：`Link` 到 `/tickets/{id}`（标题可点）+ 状态徽章（`TICKET_STATUS_LABELS`：waiting「等你处理」/ blocked「受阻」，danger/warning 口径不变）；
- 正文：`ticket.humanAction?.prompt`，没有时回落 `ticket.summary`（沿用现状）；
- 操作行：「回复」（仅当 `humanAction?.prompt` 非空时显示）、「打开工单」（`Link`）；
- 回复展开：`Textarea`（`aria-label="回复工单"`，`rows=2`，`maxLength=32000`，占位「回复工单…」）+「发送」「取消」；空内容禁用「发送」。

发送：

- `streamSse(`/api/agent/tickets/${ticketId}/messages`, { content }, new AbortController().signal, () => {}, { detach: true })`：服务端受理即可，本端不读流（与排队消息同口径）；
- 成功：Toast「已发给工单」；清空并收起输入；失效 `keys.agent.tickets.lists()` 与 `keys.agent.tickets.messagesAll(ticketId)`；
- 失败：卡片内 `InlineAlert` 显示 `发送消息失败：原因`，输入保留可重试；错误码 `TICKET_ENDED` 时同样失效工单列表，刷新后卡片按新状态消失/更新；
- 本地不写消息气泡：回复是否进入会话与工单状态由 Worker 下一轮汇报与 WS 失效驱动（`human_action` 的清除时机不变）。

### 4.5 组件与文件

新增：

- `web/src/features/console/AttentionFeed.tsx`：区块外壳、状态分支、`Section` + `AttentionList`；
- `web/src/features/console/AttentionMemoryCard.tsx`：记忆折叠/展开/审核；
- `web/src/features/console/AttentionTicketCard.tsx`：工单卡 + 就地回复。

修改：

- `web/src/features/console/ConsolePage.tsx`：两套布局改为「会话内容 + `AttentionFeed`」；删桌面 `AttentionPanel`、手机 `AttentionSheet`/`AttentionSummaryBar` 与 `sheetOpen`；`tailKey` 拼上待办项签名（`attention.items.map((item) => item.id).join(',')`），在底部时新待办自动贴底；
- `web/src/features/console/AttentionList.tsx`：去掉加载/错误/空态与空文案；记忆、工单两类改渲染新组件；其余卡片与 resolver 错误渲染不变；
- `web/src/features/console/test-utils.tsx`：假后端补记忆审核与工单发消息的记录/状态更新（见 §5）。

删除：

- `web/src/features/console/AttentionPanel.tsx`、`AttentionSheet.tsx`、`AttentionSummaryBar.tsx`。

不改：`attention.ts`、`use-attention.ts`、`use-attention-plans.ts`、`api.ts`、`AttentionList` 的确认/提问/计划/告警卡片，以及 `web/src/features/agent/*`。

### 4.6 滚动与实时

- `AttentionFeed` 在滚动内容内部，高度变化被 `setContent` 上的 `ResizeObserver` 观察；用户停在底部时跟随，上滑看历史时不强行拉回（沿用 `useConversationScroll`）。
- `tailKey` 增加待办项签名：新增待办、处理掉待办都会触发一次贴底（仅在跟随态）；`attention.isPending`/错误文案变化不额外触发。
- 数据刷新沿用 `useAttention` 的查询与 WS 失效（确认/提问/计划/告警/记忆/工单列表），不新增轮询与事件。

### 4.7 文案（新增）

| 位置 | 文案 |
|---|---|
| 记忆卡片按钮 | 展开审核 / 收起 |
| 记忆卡片链接 | 去记忆页 |
| 记忆条目按钮 | 批准 / 拒绝 |
| 工单卡片按钮 | 回复 / 打开工单 |
| 工单回复表单 | 发送 / 取消；占位「回复工单…」；读屏标签「回复工单」 |
| Toast | 已发给工单 |
| 错误 | 发送消息失败：原因；记忆错误沿用 `describeMemoryError` |

### 4.8 边界情况

| 输入 / 状态 | 期望 |
|---|---|
| 没有任何待办 | 整块不渲染；会话照常 |
| 只有记忆 | 一行「N 条记忆待审核」，可展开审核 |
| 只有 waiting/blocked 工单、`human_action.prompt` 为空 | 工单卡只显示摘要 + 「打开工单」，不显示「回复」 |
| 工单卡回复成功 | Toast「已发给工单」；卡片保留到 Worker 汇报后由 WS 刷新（`human_action` 可能还在） |
| 工单已结束（发送 409 `TICKET_ENDED`） | 卡片内错误提示；失效工单列表后卡片按刷新结果消失 |
| 记忆批准/拒绝成功 | 该条即从展开列表消失；全部处理完卡片消失 |
| 记忆审核 404/409（已在别处处理） | `describeMemoryError` 文案；卡片按刷新结果更新 |
| 实时副本里挂着确认/提问 | `excludeIds` 逻辑不变；它们只在实时副本处出现，不在区块里重复 |
| 计划刚批准但 Worker 未清 `human_action` | 现有抑制逻辑（`suppressedTicketIds`）不变 |
| 桌面 | 右栏无「待你处理」；只有进行中工单 |
| 手机 | 工具条无「N 件事等你处理」；没有底部面板；区块在会话尾部、输入栏上方 |
| 处理掉最后一张卡 | 区块消失；页面不自动收起任何面板（已无面板） |
| 读取待你处理失败 | 区块内错误 + 重试；会话其余部分照常 |

## 5. 测试

前端（`cd web`）：

- `ConsolePage.test.tsx` 改写与新增：
  - 桌面布局：`待你处理` 标题与卡片渲染在 `总控会话` 区域内（`aria-label="总控会话"`），桌面右侧栏 `指挥台侧栏` 不再包含待办、只含进行中工单；
  - 手机布局：不再出现「N 件事等你处理」按钮与 `dialog`；待办卡直接在主会话 DOM 中可操作；
  - 确认/提问/计划/告警四类卡片仍在（既有用例改到新位置后保留能力断言）；
  - 记忆：展开后逐条「批准/拒绝」→ 断言 `POST /api/agent/memories/{id}/review` 的 body 与假后端状态更新，处理后卡片消失；
  - 工单：点「回复」→ 输入 → 「发送」→ 断言 `POST /api/agent/tickets/{id}/messages` 的 body；成功 Toast；失败时错误提示与输入保留；无 `human_action` 的工单不显示「回复」；
  - 空态：没有任何待办时 `待你处理` 不渲染；
  - 处理掉最后一张卡后区块消失。
- `attention.test.ts` 不改（纯函数与排序口径不变）。
- 假后端（`test-utils.tsx`）：
  - `POST /api/agent/memories/{id}/review`：更新 `state.memories` 的 `status`，记录请求；暴露 `memoryReviews()`；
  - 工单消息 `POST` 已有 SSE 假响应，补记录与可选失败注入（如 `ticketMessageStatus`），暴露 `ticketMessagePosts()`；断言 detach 发送只 POST 一次。
- 命令：`pnpm typecheck && pnpm test --run && pnpm build`；`scripts/frontend_smoke.py`（需先 `pnpm build`）。

后端与契约（本次不改后端，仍按项目约定跑）：

- `.venv/bin/python -m pytest -o addopts= -q`（全量，记录既有失败基线）；
- `.venv/bin/python scripts/dump_openapi.py --check`、`cd web && pnpm gen:api:check`（期望 0，确认未误动契约）；
- `.venv/bin/python scripts/check_doc_links.py`（末行 `BROKEN 0`）。

## 6. 文档同步

- `docs/v3/11-前端指挥台.md`：§3.1 三块与布局表（待你处理移到会话尾部、右栏只剩工单、手机无面板/汇总行）；§3.2 重写呈现位置与两类新交互；§3.3 补一句会话尾部有「待你处理」块；§5「真正的确认卡、提问卡在『待你处理』里」改为指向会话尾部；§10 移动端删掉底部面板条目、改工具条描述；§11 文案补新增按钮/Toast/错误。
- `docs/v3/02-系统架构.md` §4.2 前端目录里 `console/` 的说明改为「指挥台（首页）：总控会话（含『待你处理』块）、进行中的工单」。
- `docs/v3/13-决策记录.md`：新增 §8.176，记录用户要求、方案选择、删除范围、实施裁决与验证。
- `docs/v3/ledgers/console-attention-inline.md`：完成后从 SDD `progress.md` 复制并补「Rulings I made」。
- `docs/v3/specs/console-attention-inline.md`、`docs/v3/plans/console-attention-inline.md`：随任务提交。
- 历史 spec（M72 等）不回头改；`docs/copy-audit/` 文案基线按 13 §8.175 的口径不重设（既有基线差异非本补丁引入）。

## 7. 验收

| 检查 | 期望 |
|---|---|
| 前端 typecheck / test / build | 全绿 |
| `scripts/frontend_smoke.py` | 末行 `SMOKE OK` |
| 后端全量 pytest | 无新增失败（既有真资源 3 条失败基线除外） |
| OpenAPI 快照与生成类型 | `--check` 0；未产生 diff |
| 文档断链 | `BROKEN 0` |
| 残留 grep | `AttentionPanel\|AttentionSheet\|AttentionSummaryBar` 在 `web/src` 零命中；`N 件事等你处理` 零命中 |
| 手工验收（用户） | 手机上打开指挥台：不需要展开任何面板即可看到并处理全部待办；回复工单能发出；记忆能就地批准/拒绝 |

## 8. 后续（不在本补丁）

- M81 观察记录来源接入「待你处理」（前端未接入，单独任务）。
- 若后续要「待办常驻吸顶/未读提示」，在此块之上加跳转或计数，不改变本补丁的所在与操作口径。
