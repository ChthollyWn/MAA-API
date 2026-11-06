# 指挥台计划批准（维护性补丁规格）

> 非里程碑补丁：按维护性改造处理，不占用新里程碑编号，不调整 [12-实施路线](../12-实施路线与里程碑.md) 的顺序。
> 本文是本次改造的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。
> 产品口径经用户 2026-11-06 确认：只把「计划待批准」搬上指挥台；「设备/权限批准」就是既有确认卡（已在指挥台可处置）；工单页保留为第二入口。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 最新提交上开分支 | `git status -sb` | `## console-plan-approval`，工作区干净 |
| 2 | 计划面板仍只在工单页 | `grep -n "PlanPanel" web/src/features/tickets/TicketDetailPage.tsx` | 至少 2 行 |
| 3 | 指挥台「待你处理」还没有 plan 来源 | `grep -n "kind: 'plan'" web/src/features/console/attention.ts` | 无输出 |
| 4 | 计划查询与审批函数已存在 | `grep -n "export function planQueryOptions\|export async function submitPlanApproval" web/src/features/tickets/workspace-api.ts` | 两行都有 |
| 5 | 测试假后端已模拟计划端点 | `grep -n "plan/approval" web/src/features/console/test-utils.tsx` | 有匹配 |
| 6 | 确认/提问已在指挥台可处置 | `grep -n "ConfirmationCard" web/src/features/console/AttentionList.tsx` | 有匹配 |

## 1. 背景与目标

M71/M72 把 Worker 与子代理发起的确认、提问冒泡到指挥台「待你处理」，可直接批准/拒绝/作答（V3-D23）。M75 的「计划待批准」是另一条链路：Worker 提交计划后工单进入 `waiting`，`human_action={type: decision, ref_id: 计划 id}`；目前只能进工单详情页、在桌面侧栏 `PlanPanel` 批准/退回，手机工单页根本不渲染 `PlanPanel`，计划推送落到 `/tickets/:id` 后无处置入口。本次改造把它提升到指挥台，完成「授权/批准在指挥台完成，工单页不是必经路径」的产品要求。

做完后：

1. 工单 `waiting`、`human_action.type=decision`、`ref_id` 指向一份 `awaiting_approval` 计划时，指挥台「待你处理」出现一张计划卡（标题、步骤、来源工单），可「批准计划」「退回计划」。
2. 桌面右侧栏与手机「待你处理」面板都能操作；手机不再需要进工单页批准计划。
3. 工单页 `PlanPanel` 保留，作为状态展示与第二入口，行为不变。
4. 不新增或修改后端端点、SSE/WS 事件、错误码、配置键、表与迁移；`web/openapi.json` 与生成类型不变。
5. 读不到计划、计划与 `human_action.ref_id` 不匹配、或请求失败时，回落到现有工单卡行为，不回退既有能力。

## 2. 非目标

- 不处理非计划的 `decision`（重启打断、Watchdog、失败补 `blocked`、Worker 自报）：仍只出工单卡，进工单页处理。
- 不改确认/提问卡片、全局面板 `ConfirmationDock`、`?confirm=` 深链与 IM 审批（V3-D24、V3-D25 口径不变）。
- 不改工单页 `PlanPanel` 的行为与版式，也不在手机工单页新增计划面板。
- 不改后端计划端点；批准计划后 `ticket.human_action` 的清理时机仍由 Worker 下一轮汇报负责。
- 不做计划深链（`?plan=`）、不做计划推送改向。

## 3. 相关文档与代码

- [11-前端指挥台](../11-前端指挥台.md)：§3.2「待你处理」来源表与排序、§4.2 计划面板、§10 移动端、§11 文案。
- [02-系统架构](../02-系统架构.md) §6.2、[13-决策记录](../13-决策记录.md) V3-D23；M75 spec §4.3、§4.5、§4.6。
- 既有代码入口：
  - `web/src/features/console/attention.ts` 的 `buildAttentionItems`、`AttentionItem`；
  - `web/src/features/console/use-attention.ts` 的 `useAttention`；
  - `web/src/features/console/AttentionList.tsx`、`AttentionPanel.tsx`、`AttentionSheet.tsx`；
  - `web/src/features/console/api.ts` 的 `attentionTicketsQuery`、`ticketDetailQuery`；
  - `web/src/features/tickets/workspace-api.ts` 的 `planQueryOptions`、`submitPlanApproval`、`PlanView`；
  - `web/src/features/tickets/PlanPanel.tsx` 的计划状态/步骤中文表与批准/退回交互；
  - `web/src/features/console/test-utils.tsx` 的假后端（已模拟 plan GET/POST 与 `holdPlanApproval`）。

## 4. 设计

### 4.1 数据来源与判定

新 hook `useAttentionPlans(tickets)` 接收「待你处理」里的 waiting/blocked 工单，产出计划项与被抑制的工单 id：

1. 候选：`ticket.status === 'waiting'` 且 `ticket.humanAction?.type === 'decision'` 且 `ticket.humanAction.ref_id` 非空。
2. 对每个候选读 `ticketDetailQuery(ticket.id)`（指挥台已有查询，10 秒轮询、WS 失效）取 `workerSessionId`；没有会话就停在这一步。
3. 对每个 `workerSessionId` 读 `planQueryOptions(workerSessionId)`（M75，只随 `plan_updated` 失效）。
4. 计划项：`plan !== null` 且 `plan.status === 'awaiting_approval'` 且 `plan.id === ticket.humanAction.ref_id`。
5. 抑制：`plan !== null` 且 `plan.id === ticket.humanAction.ref_id` 但状态不是 `awaiting_approval`（刚批准/退回，Worker 还没汇报清 `human_action`）时，把该工单 id 放进 `suppressedTicketIds`，避免出现一张点了就 409 的死卡。
6. 读取失败（详情或计划报错、`workerSessionId` 为空、计划为 `null`）：不产出、不抑制，工单卡按现状显示。

边界情况：

| 输入 | 期望 |
|---|---|
| waiting + decision + 计划待批准且 ref 匹配 | 计划卡；同一工单不出工单卡 |
| waiting + decision + 计划已批准/退回（ref 匹配） | 不出计划卡，也不出该工单卡（等 Worker 汇报刷新） |
| waiting + decision + plan 为 null / ref 不匹配 | 工单卡（现状） |
| waiting + decision + 详情或计划读取失败 | 工单卡（现状） |
| blocked + decision | 工单卡（现状） |
| waiting + 确认/提问 | 现有确认/提问卡与覆盖逻辑不变 |
| 实时副本里挂着确认/提问 | `excludeIds` 逻辑不变；计划不走 SSE 实时副本 |
| 计划批准成功 | 计划缓存写入响应；计划卡消失；失效工单详情与列表；Toast「已批准计划」 |
| 计划退回成功 | 同上，Toast「已退回计划」 |
| 409 `PLAN_NOT_AWAITING_APPROVAL` | 失效计划与工单查询；提示一次；计划卡消失、回落工单卡或空 |
| 手机 | 「待你处理」面板（`AttentionSheet`）里同一张卡，可操作 |
| 计划步骤很多 | 全部列出，不做折叠（计划通常 ≤ 8 步） |

### 4.2 模块与文件

新增：

- `web/src/features/tickets/plan-format.ts`：从 `PlanPanel.tsx` 原样抽出 `PlanTone`、`PLAN_STATUS_LABELS`、`PLAN_ITEM_STATUS_LABELS`、`PLAN_STATUS_TONES`、`ITEM_STATUS_TONES`。`PlanPanel.tsx` 改为从这里导入，行为与文案不变。
- `web/src/features/console/use-attention-plans.ts`：`useAttentionPlans` 与 `usePlanApprovalResolver`。
- `web/src/features/console/PlanApprovalCard.tsx`：纯展示卡片，决定通过 props 传入。
- 测试：`web/src/features/console/plan-attention.test.tsx`、`web/src/features/console/PlanApprovalCard.test.tsx`。

修改：

- `web/src/features/console/attention.ts`：`AttentionItem` 加 `plan` 分支；`buildAttentionItems` 入参加 `plans`、`suppressedTicketIds`；排序组调整为确认 0 → 提问 1 → 记忆 2 → 告警 3 → blocked 工单 4 → 计划待批准 5 → waiting 工单 6 → 消耗 7（现有类型相对顺序不变，计划插在 blocked 与 waiting 之间）。
- `web/src/features/console/use-attention.ts`：接入计划数据源与 `planResolver`；`AttentionState` 加 `planResolver`。
- `web/src/features/console/AttentionList.tsx`：渲染计划卡、来源行与 `planResolver.error`。
- `web/src/features/console/test-utils.tsx`：新增 `makePlan` 工厂（仅测试设施）。
- 文档：`docs/v3/11-前端指挥台.md`、`docs/v3/13-决策记录.md` §8、`docs/v3/ledgers/console-plan-approval.md`。

### 4.3 前端内部接口

```ts
// features/tickets/plan-format.ts
export type PlanTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
export const PLAN_STATUS_LABELS: Record<string, string>
export const PLAN_ITEM_STATUS_LABELS: Record<string, string>
export const PLAN_STATUS_TONES: Record<string, PlanTone>
export const ITEM_STATUS_TONES: Record<string, PlanTone>

// features/console/use-attention-plans.ts
export interface PlanAttentionView {
  ticket: TicketView
  plan: PlanView
  workerSessionId: string
}
export interface PlanAttentionSource {
  items: PlanAttentionView[]
  suppressedTicketIds: ReadonlySet<string>
  isPending: boolean
  refetch: () => void
}
export interface PlanResolver {
  resolve: (workerSessionId: string, ticketId: string, input: PlanApprovalInput) => void
  busyFor: (workerSessionId: string) => 'approve' | 'reject' | null
  error: ShownError | null
  clearError: () => void
}
export function useAttentionPlans(tickets: readonly TicketView[]): PlanAttentionSource
export function usePlanApprovalResolver(): PlanResolver

// features/console/attention.ts
export function buildAttentionItems(input: {
  confirmations: readonly Confirmation[]
  questions: readonly AgentQuestion[]
  tickets: readonly TicketView[]
  plans?: readonly PlanAttentionView[]
  suppressedTicketIds?: ReadonlySet<string>
  memories?: readonly MemoryItem[]
  alerts?: readonly AlertOut[]
  spend?: SpendSummary
  excludeIds?: ReadonlySet<string>
}): AttentionItem[]
```

`PlanApprovalCard` props：

```ts
export interface PlanApprovalCardProps {
  ticket: TicketView
  plan: PlanView
  workerSessionId: string
  busy: 'approve' | 'reject' | null
  onResolve: (workerSessionId: string, ticketId: string, input: PlanApprovalInput) => void
}
```

### 4.4 文案

遵守 [文案规范](../../copy-audit/规范-文案规范.md) 与 [11 §11](../11-前端指挥台.md)：

| 概念 | 写法 |
|---|---|
| 卡片标题 | `计划待批准` |
| 来源行 | `来自工单「{标题}」· {Worker 中文名}`（由 AttentionList 统一渲染，与确认/提问同做法） |
| 按钮 | `批准计划`、`退回计划`、`提交退回`、`取消`；卡片右上角 `打开工单` |
| 退回原因 | 标签 `退回原因`，占位 `写清要改哪一步、改成什么`，最多 200 字 |
| Toast | `已批准计划`、`已退回计划` |
| 错误 | `处理计划失败：{原因}` |
| 步骤状态 | 沿用 `待开始 / 进行中 / 已完成 / 已取消` |

界面上不出现代码名、里程碑号与「Worker 授权」「权限批准」等新名词。

### 4.5 交互细节

- 批准：点击即提交，无二次确认；请求中两个按钮都禁用并显示 loading。
- 退回：点击「退回计划」就地展开原因输入框；原因 `trim()` 后非空才能「提交退回」；「取消」收起并清空。
- 成功后计划卡消失（计划缓存已变为 `active`/`draft`），不等待工单状态刷新。
- 计划读取失败时不向用户报错（回落到工单卡）；只有审批请求失败显示 `处理计划失败：…`。
- 计划卡与工单卡互斥；同一工单在「待你处理」里只出现一项。

### 4.6 查询失效

- 审批成功：`queryClient.setQueryData(keys.agent.plan(workerSessionId), next)`；失效 `keys.agent.tickets.detail(ticket.id)`、`keys.agent.tickets.lists()`、`keys.agent.pendingConfirmations` 无关，不失效。
- 审批失败（含 409）：失效 `keys.agent.plan(workerSessionId)`、工单详情与列表；错误留在 `planResolver.error`，由 `AttentionList` 顶部显示。

## 5. 实施任务

见 [实施计划](../plans/console-plan-approval.md)。任务顺序：数据模型 → 计划卡 → hook 与页面接线 → 文档 → 全量验证。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `features/console/attention.test.ts` | `turns a waiting ticket with a pending plan into a plan item` | 计划项 kind/id、同一工单不出工单卡、排在 waiting 卡前 |
| 同上 | `hides a ticket whose plan already left awaiting approval` | `suppressedTicketIds` 命中时不出工单卡 |
| 同上 | `keeps the ticket card when no plan data is available` | 无 plans 入参时行为不变 |
| `features/console/PlanApprovalCard.test.tsx` | `renders the plan title, steps and source ticket` | 标题「计划待批准」、步骤、来源链接 |
| 同上 | `approves the plan` | `onResolve(sessionId, {approved:true})` |
| 同上 | `requires a reason before rejecting` | 空原因禁用「提交退回」；非空发 `{approved:false,reason}` |
| 同上 | `disables both actions while the request is in flight` | busy 时两个按钮禁用 |
| `features/console/plan-attention.test.tsx` | `shows a pending plan in the console and approves it` | 桌面侧栏出现计划卡；POST `/api/agent/sessions/w1/plan/approval` body `{approved:true}`；卡消失 |
| 同上 | `rejects a plan from the console with a reason` | body `{approved:false,reason}`；Toast |
| 同上 | `opens the plan card in the phone attention sheet` | 手机汇总行 → Sheet 内计划卡可操作 |
| 同上 | `falls back to the ticket card when the plan cannot be read` | 计划 404/详情无会话时仍出工单卡 |
| 同上 | `shows an error when the approval request fails` | 500 时顶部错误「处理计划失败：…」 |

既有用例不改仍通过（`ConsolePage.test.tsx`、`PlanPanel.test.tsx`、`attention.test.ts`）。

## 7. 验收

1. `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 全部通过。
2. `.venv/bin/python scripts/frontend_smoke.py` 末行 `SMOKE OK`（需先 build）。
3. 后端无改动，仍全量跑 `.venv/bin/python -m pytest -q` 确认无回归（起点 `dev` 已有失败记台账）。
4. `.venv/bin/python scripts/check_doc_links.py` 末行 `BROKEN 0`；`git diff --check` 干净。
5. 手动补充检查（不作硬性验证，结果记台账）：桌面与 375px 宽度下各走一遍「plan awaiting → 指挥台批准/退回」。

## 8. 风险与回滚

- 只为 `decision` 工单多做一次工单详情查询（数量很少，复用既有查询与缓存），不会给所有工单增加请求。
- 计划卡与工单卡互斥、失败回退都在纯函数与 hook 内，测试覆盖；单分支合并，回滚 `git revert -m 1 <merge>` 即可。
- 不改后端与契约，无迁移与状态兼容风险。

## 9. 文档同步

- [11-前端指挥台](../11-前端指挥台.md)：§1 原则、§3.2（来源表、排序、互斥边界）、§4.2（计划面板写明指挥台为主入口）、§10（手机面板含计划卡）、§11（文案表补计划相关按钮与 Toast）。
- [13-决策记录](../13-决策记录.md) §8 追加一条。
- 台账 `docs/v3/ledgers/console-plan-approval.md`。
- 02/04/08/09/14/15 不改（无契约、无通道、无安全语义变化）。

## 10. 停下来问

- 发现必须新增/修改后端端点、事件、错误码、表或配置键才能完成。
- 发现计划批准与 13 号决策记录冲突，或需要改变确认/提问、全局面板、IM 的既有口径。
- 发现需要改工单页 `PlanPanel` 的行为或移动端工单页版式。
