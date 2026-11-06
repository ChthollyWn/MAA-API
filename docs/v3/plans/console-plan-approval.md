# 指挥台计划批准 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Worker 的「计划待批准」做成指挥台「待你处理」里可就地批准/退回的卡片（桌面侧栏与手机面板都可操作），工单页 `PlanPanel` 保留为第二入口。

**Architecture:** 纯前端改动。复用既有的 `attentionTicketsQuery` → `ticketDetailQuery` → `planQueryOptions` 三层查询拿到待批准计划；`buildAttentionItems` 增加 `plan` 分支与「被抑制工单」集合；新增 `PlanApprovalCard` 纯展示组件与 `planResolver` 审批 mutation。不新增或修改后端契约。

**Tech Stack:** React 18 + TypeScript、TanStack Query、Vitest + Testing Library、Tailwind 设计系统组件。

**Spec:** [docs/v3/specs/console-plan-approval.md](../specs/console-plan-approval.md)

## Global Constraints

- 只改 spec §4.2 列出的文件；不新增依赖、端点、错误码、配置键、表。
- 界面文案照 spec §4.4：`计划待批准`、`批准计划`、`退回计划`、`提交退回`、`取消`、`打开工单`、`已批准计划`、`已退回计划`、`处理计划失败：…`。
- 既有用例不改仍通过；`pnpm gen:api:check` 不变（无后端改动）。
- 计划审批端点：`POST /api/agent/sessions/{worker_session_id}/plan/approval`，body `{approved: boolean, reason?: string}`（`reason` ≤ 200 字）。
- 工单页 `PlanPanel` 行为不变，只把中文表抽到 `plan-format.ts`。

## Review Focus

- **计划读取失败时不能把工单从「待你处理」里弄丢**：详情/计划报错、`worker_session_id` 为空、plan 为 `null` 时必须回落现有工单卡；对应测试在 Task 3。
- **批准后旧卡不能以「死卡」形态滞留**：计划缓存已变 `active`/`draft` 时抑制该工单卡（`suppressedTicketIds`），等 Worker 汇报刷新；对应测试在 Task 1 与 Task 3。
- **手机同一张卡可操作**：手机走 `AttentionSheet` 渲染同一份 `AttentionList`，必须有点击用例；对应测试在 Task 3。
- **退回必须带非空原因**：后端 `reason` 是可选字段但退回语义要求写原因；前端 trim 后非空才可提交；对应测试在 Task 2。
- **请求失败要显式提示且可重试**：409 时失效计划与工单查询，500 时顶部显示 `处理计划失败：…`；对应测试在 Task 2 与 Task 3。

---

### Task 1: 计划数据模型与「待你处理」合并

**Files:**
- Create: `web/src/features/tickets/plan-format.ts`
- Create: `web/src/features/console/use-attention-plans.ts`（本任务只写类型与纯判断辅助，不写查询）
- Modify: `web/src/features/console/attention.ts`
- Modify: `web/src/features/tickets/PlanPanel.tsx`（改为从 `plan-format.ts` 导入常量）
- Test: `web/src/features/console/attention.test.ts`

**Interfaces:**
- Consumes: `web/src/features/tickets/workspace-api.ts` 的 `PlanView`；`web/src/features/console/api.ts` 的 `TicketView`。
- Produces:
  - `plan-format.ts` 的 `PlanTone`、`PLAN_STATUS_LABELS`、`PLAN_ITEM_STATUS_LABELS`、`PLAN_STATUS_TONES`、`ITEM_STATUS_TONES`（与 `PlanPanel.tsx` 现有常量逐字相同）。
  - `use-attention-plans.ts` 的 `PlanAttentionView`、`PlanAttentionSource`、`PlanResolver`（类型定义）。
  - `attention.ts` 的 `AttentionItem` 新分支 `{ kind: 'plan'; id: string; ticket: TicketView; plan: PlanView; workerSessionId: string }`；`buildAttentionItems` 新入参 `plans?: readonly PlanAttentionView[]`、`suppressedTicketIds?: ReadonlySet<string>`。

- [ ] **Step 1: 写失败测试（attention.test.ts 追加三例）**

```ts
it('turns a waiting ticket with a pending plan into a plan item', () => {
  const items = buildAttentionItems({
    confirmations: [],
    questions: [],
    tickets: [
      ticket({ id: 't-plan', status: 'waiting', human_action: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' } }),
      ticket({ id: 't-waiting', status: 'waiting', updated_at: '2026-09-27T10:02:00Z' }),
    ],
    plans: [{
      ticket: toTicketView(makeTicket({
        id: 't-plan',
        status: 'waiting',
        human_action: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' },
      })),
      plan: { id: 'p1', session_id: 'w1', ticket_id: 't-plan', title: '清日常', items: [], status: 'awaiting_approval', approved_by: null, approved_at: null },
      workerSessionId: 'w1',
    }],
  })
  expect(items.map((item) => item.kind)).toEqual(['plan', 'ticket'])
  expect(items[0]).toMatchObject({ kind: 'plan', id: expect.stringContaining('p1') })
})

it('hides a ticket whose plan already left awaiting approval', () => {
  const items = buildAttentionItems({
    confirmations: [], questions: [],
    tickets: [ticket({ id: 't-plan', status: 'waiting' })],
    suppressedTicketIds: new Set(['t-plan']),
  })
  expect(items).toEqual([])
})

it('keeps the ticket card when no plan data is available', () => {
  const items = buildAttentionItems({
    confirmations: [], questions: [],
    tickets: [ticket({ id: 't-plan', status: 'waiting', human_action: { type: 'decision', prompt: '需要你决定', ref_id: 'watchdog:1' } })],
  })
  expect(items.map((item) => item.kind)).toEqual(['ticket'])
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm vitest run src/features/console/attention.test.ts`
Expected: FAIL（`plans`/`suppressedTicketIds` 不是 `buildAttentionItems` 的属性；plan 分支不存在）

- [ ] **Step 3: 实现**

1. 新建 `web/src/features/tickets/plan-format.ts`，把 `PlanPanel.tsx` 顶部四个常量与 `PlanTone` 类型原样搬过来并导出；`PlanPanel.tsx` 删除本地定义、改为 `import { ... } from './plan-format'`。
2. 新建 `web/src/features/console/use-attention-plans.ts`，先只放三个类型与 docstring（`PlanAttentionView`、`PlanAttentionSource`、`PlanResolver`），签名照 spec §4.3。
3. 改 `attention.ts`：
   - 类型联合加 plan 分支；
   - `buildAttentionItems` 入参加 `plans = []` 与 `suppressedTicketIds`；
   - `coveredTickets` 之外再建 `plannedTickets = new Set(plans.map((entry) => entry.ticket.id))`；
   - 工单循环先跳过 `suppressedTicketIds?.has(ticket.id)` 与 `plannedTickets.has(ticket.id)`；
   - 在 blocked 工单（group 4）之后、waiting 工单（group 6）之前插入 plan 项（group 5，`expires: Infinity`，`updated: updatedAt(ticket.updatedAt)`，`id: \`plan-${plan.id}\``）；waiting 组 5→6、消耗组 6→7；
   - 注释更新排序说明。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && pnpm vitest run src/features/console/attention.test.ts src/features/tickets/PlanPanel.test.tsx`
Expected: PASS（含既有用例）

- [ ] **Step 5: 提交**

```bash
git add web/src/features/tickets/plan-format.ts web/src/features/console/use-attention-plans.ts web/src/features/console/attention.ts web/src/features/tickets/PlanPanel.tsx web/src/features/console/attention.test.ts
git commit -m "feat(console): 待你处理支持计划待批准数据模型"
```

---

### Task 2: 计划卡组件

**Files:**
- Create: `web/src/features/console/PlanApprovalCard.tsx`
- Test: `web/src/features/console/PlanApprovalCard.test.tsx`

**Interfaces:**
- Consumes: `plan-format.ts` 的常量；`workspace-api.ts` 的 `PlanApprovalInput`、`PlanView`；`console/api.ts` 的 `TicketView`。
- Produces: `PlanApprovalCard({ ticket, plan, workerSessionId, busy, onResolve }: PlanApprovalCardProps)`，props 见 spec §4.3。

- [ ] **Step 1: 写失败测试**

```tsx
// PlanApprovalCard.test.tsx
import { describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { PlanApprovalCard } from './PlanApprovalCard'
import type { PlanView } from '@/features/tickets/workspace-api'
import type { TicketView } from './api'

const ticket: TicketView = { id: 't1', title: '清日常', kind: 'general', status: 'waiting', origin: 'user', summary: null, lastReportStatus: null, humanAction: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' }, createdAt: null, updatedAt: null, endedAt: null }
const plan: PlanView = { id: 'p1', session_id: 'w1', ticket_id: 't1', title: '清日常', items: [{ id: 'a1', title: '提交剿灭', status: 'in_progress', note: '先看体力' }], status: 'awaiting_approval', approved_by: null, approved_at: null }

it('renders the plan title, steps and source ticket', () => {
  render(<PlanApprovalCard ticket={ticket} plan={plan} workerSessionId="w1" busy={null} onResolve={vi.fn()} />)
  expect(screen.getByRole('heading', { name: '计划待批准' })).toBeInTheDocument()
  expect(screen.getByText('清日常')).toBeInTheDocument()
  expect(screen.getByText('提交剿灭')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '打开工单' })).toHaveAttribute('href', '/tickets/t1')
})

it('approves the plan', () => {
  const onResolve = vi.fn()
  render(<PlanApprovalCard ... onResolve={onResolve} />)
  fireEvent.click(screen.getByRole('button', { name: '批准计划' }))
  expect(onResolve).toHaveBeenCalledWith('w1', { approved: true })
})

it('requires a reason before rejecting', () => {
  const onResolve = vi.fn()
  render(<PlanApprovalCard ... onResolve={onResolve} />)
  fireEvent.click(screen.getByRole('button', { name: '退回计划' }))
  const submit = screen.getByRole('button', { name: '提交退回' })
  expect(submit).toBeDisabled()
  fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '第二步改到下午' } })
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  expect(onResolve).toHaveBeenCalledWith('w1', { approved: false, reason: '第二步改到下午' })
})

it('disables both actions while the request is in flight', () => {
  render(<PlanApprovalCard ... busy="approve" />)
  expect(screen.getByRole('button', { name: /批准计划|正在处理/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: '退回计划' })).toBeDisabled()
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm vitest run src/features/console/PlanApprovalCard.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现组件**

按 `ConfirmationCard`/`PlanPanel` 的设计语言写 `article` 卡片：
- 顶部 `Badge`「待批准」+ `<h3>` 标题 `计划待批准`；下一行 `plan.title` 与链接「打开工单」。
- 步骤 `<ol aria-label="计划步骤">`：序号、标题、note、状态 Badge（`PLAN_ITEM_STATUS_LABELS`/`ITEM_STATUS_TONES`）。
- `busy` 时「批准计划」「退回计划」都 `disabled`，被点的那个 `loading`。
- 「退回计划」切换原因输入：`<Textarea>`（label `退回原因`、`maxLength={200}`、placeholder 照 spec），`提交退回` 在 `reason.trim().length === 0` 时禁用；`取消` 收起并清空。
- 不用自己发请求，全部通过 `onResolve`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && pnpm vitest run src/features/console/PlanApprovalCard.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web/src/features/console/PlanApprovalCard.tsx web/src/features/console/PlanApprovalCard.test.tsx
git commit -m "feat(console): 计划待批准卡片"
```

---

### Task 3: 接入指挥台（查询、resolver、列表、手机面板）

**Files:**
- Modify: `web/src/features/console/use-attention-plans.ts`
- Modify: `web/src/features/console/use-attention.ts`
- Modify: `web/src/features/console/AttentionList.tsx`
- Modify: `web/src/features/console/test-utils.tsx`（新增 `makePlan` 工厂）
- Test: `web/src/features/console/plan-attention.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 `PlanAttentionView`/`PlanAttentionSource`/`PlanResolver`；Task 2 的 `PlanApprovalCard`；`console/api.ts` 的 `ticketDetailQuery`、`attentionTicketsQuery`；`tickets/workspace-api.ts` 的 `planQueryOptions`、`submitPlanApproval`。
- Produces:
  - `useAttentionPlans(tickets: readonly TicketView[]): PlanAttentionSource`（真实查询与判定，边界照 spec §4.1）。
  - `usePlanApprovalResolver(): PlanResolver`：`useMutation` 调 `submitPlanApproval(workerSessionId, input)`；成功 `setQueryData(keys.agent.plan(sessionId), next)`、失效 `keys.agent.tickets.detail(ticketId)`（需把 ticketId 一起传入 mutation 变量）与 `keys.agent.tickets.lists()`、Toast；失败（含 409）失效 `keys.agent.plan(sessionId)`、工单详情与列表，错误写入 `mutation.error`。
  - `AttentionState` 新字段 `planResolver: PlanResolver`。

- [ ] **Step 1: 写失败测试（plan-attention.test.tsx）**

用 `createConsoleApi` + `renderAt`（照 `ConsolePage.test.tsx` 的 `renderConsole` 写法），桌面用 `stubMedia((query) => query === '(min-width: 1024px)')`：

```ts
function waitingTicketDecision() {
  return makeTicket({
    id: 't1', title: '清日常', status: 'waiting',
    human_action: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' },
  })
}

function planFixture(overrides: Json = {}): Json {
  return {
    id: 'p1', session_id: 'w1', ticket_id: 't1', title: '清日常',
    items: [{ id: 'a1', title: '提交剿灭', status: 'in_progress', note: '先看体力' }],
    status: 'awaiting_approval', approved_by: null, approved_at: null,
    ...overrides,
  }
}

const planApprovalPosts = (api: ReturnType<typeof createConsoleApi>) =>
  api.requests.filter((item) => item.method === 'POST' && item.path === '/api/agent/sessions/w1/plan/approval')

it('shows a pending plan in the console and approves it', async () => {
  stubMedia((query) => query === '(min-width: 1024px)')
  const api = createConsoleApi({
    messages: [],
    attentionTickets: [waitingTicketDecision()],
    ticketDetails: { t1: makeTicketDetail({ ticket: waitingTicketDecision(), worker_session_id: 'w1' }) },
    plans: { w1: planFixture() },
  })
  renderConsole(api)
  expect(await screen.findByRole('heading', { name: '计划待批准' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '批准计划' }))
  await waitFor(() => expect(planApprovalPosts(api)[0]?.body).toEqual({ approved: true }))
  await waitFor(() => expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument())
})

it('rejects a plan from the console with a reason', async () => { /* 同上，点退回、填原因、断言 body */ })
it('opens the plan card in the phone attention sheet', async () => { /* 不 stub 桌面；点「N 件事等你处理」，Sheet 内找到计划卡并批准 */ })
it('falls back to the ticket card when the plan cannot be read', async () => {
  // ticketDetails 缺 worker_session_id 或 plans 为 null：仍出现「清日常」工单卡，且无计划标题
})
it('shows an error when the approval request fails', async () => {
  // overrides 让 POST 返回 500；点批准后出现「处理计划失败：…」
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm vitest run src/features/console/plan-attention.test.tsx`
Expected: FAIL（计划卡不存在；`makePlan` 未定义）

- [ ] **Step 3: 实现查询与接线**

1. `use-attention-plans.ts` 完整实现：
   - `const candidates = useMemo(() => tickets.filter(...), [tickets])`；
   - `useQueries` 两段：`candidates.map((ticket) => ticketDetailQuery(ticket.id))` → `sessions` → `sessions.map((sessionId) => planQueryOptions(sessionId))`；
   - `items`/`suppressedTicketIds` 用 `useMemo` 按 spec §4.1 判定（`plan` 非空且 ref 匹配：awaiting → item，否则 suppress）；
   - `isPending`：有候选且详情查询还在 pending；`refetch` 重取两段查询；
   - `usePlanApprovalResolver`：mutation 变量 `{workerSessionId, ticketId, input}`；成功写计划缓存、失效工单详情/列表、Toast「已批准计划」/「已退回计划」；失败失效计划与工单数据；暴露 `resolve`/`busyFor`/`error`/`clearError`（`ShownError` 由 `toShownError` 生成）。
2. `use-attention.ts`：`const plans = useAttentionPlans(tickets.data?.items ?? [])`、`const planResolver = usePlanApprovalResolver()`；把 `plans.items`、`plans.suppressedTicketIds` 传入 `buildAttentionItems`；`isPending`/`refetch` 合并；返回值加 `planResolver`。
3. `AttentionList.tsx`：顶部错误区加 `attention.planResolver.error`；新增 `if (item.kind === 'plan')` 分支：来源行（`来自工单「{ticket.title}」· {specTitles.get(ticket.kind) ?? ticket.kind}`，标题用 `Link` 到 `/tickets/{id}`）+ `PlanApprovalCard`（`busy={attention.planResolver.busyFor(item.workerSessionId)}`、`onResolve={attention.planResolver.resolve}`）。
4. `test-utils.tsx` 新增 `makePlan` 工厂（字段照 `PlanView`）与 `makeTicketDetail` 已有，直接复用。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && pnpm vitest run src/features/console/plan-attention.test.tsx src/features/console/ConsolePage.test.tsx src/features/console/attention.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web/src/features/console/use-attention-plans.ts web/src/features/console/use-attention.ts web/src/features/console/AttentionList.tsx web/src/features/console/test-utils.tsx web/src/features/console/plan-attention.test.tsx
git commit -m "feat(console): 指挥台待你处理接入计划批准"
```

---

### Task 4: 文档同步

**Files:**
- Modify: `docs/v3/11-前端指挥台.md`
- Modify: `docs/v3/13-决策记录.md`
- Create: `docs/v3/ledgers/console-plan-approval.md`

**Interfaces:**
- Consumes: Task 1–3 的最终行为与文案。
- Produces: 文档与台账。

- [ ] **Step 1: 更新 11**

- §1 原则第 1 条列举里加「等你批准的计划」；
- §3.2 来源表：计划待批准行改为「计划卡：标题、步骤、来源工单，就地『批准计划』『退回计划』」；排序句加「受阻工单 → 计划待批准 → 等你处理的工单」；边界加两条（计划卡压掉同一工单的工单卡；计划已批准/退回时该工单暂不出卡，等 Worker 汇报刷新）；来源行说明补计划卡；
- §4.2 计划面板：写明「指挥台『待你处理』是主入口，这里保留为状态展示与第二入口」；
- §10 手机：「待你处理」面板含计划卡；
- §11 文案：按钮/Toast 补 `批准计划`、`退回计划`、`提交退回`、`已批准计划`、`已退回计划`。

- [ ] **Step 2: 13 §8 追加一条（8.173）**

写清：现象（计划待批准只能进工单页、手机无 PlanPanel）、实际（指挥台计划卡 + 失败回退 + settled 抑制 + 无后端契约改动）、理由（用户确认的范围）、影响（11 与前端文件）。

- [ ] **Step 3: 写台账**

`docs/v3/ledgers/console-plan-approval.md` 照仓库模板：分支、基点、合并 commit（合并后补）、spec/plan 链接、做了什么、「Rulings I made」（N+1 详情查询、排序位次、settled 抑制、退回原因 200 字、审批后不等待 Worker）、验证证据表、遗留。

- [ ] **Step 4: 断链检查**

Run: `.venv/bin/python scripts/check_doc_links.py`
Expected: 末行 `BROKEN 0`

- [ ] **Step 5: 提交**

```bash
git add docs/v3/11-前端指挥台.md docs/v3/13-决策记录.md docs/v3/ledgers/console-plan-approval.md
git commit -m "docs(console): 计划批准上指挥台的契约同步与台账"
```

---

### Task 5: 全量验证与收尾

**Files:** 无（只跑命令；失败则回到对应任务修）

- [ ] **Step 1: 前端全量**

Run: `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`
Expected: 全部退出码 0

- [ ] **Step 2: 前端集成冒烟**

Run: `.venv/bin/python scripts/frontend_smoke.py`
Expected: 末行 `SMOKE OK`

- [ ] **Step 3: 后端全量（无代码改动，仅回归确认）**

Run: `.venv/bin/python -m pytest -q`
Expected: 与 `dev` 起点相比不新增失败（起点已有失败记台账）

- [ ] **Step 4: 文档与 diff**

Run: `.venv/bin/python scripts/check_doc_links.py`；`git diff --check`
Expected: `BROKEN 0`；无输出

- [ ] **Step 5: 独立复审**

派一个 fresh subagent 按 superpowers 的 requesting-code-review 口径复审本分支 diff（重点：失败回退、计划卡与工单卡互斥、手机路径、测试是否真的会失败过），把意见处理完。

- [ ] **Step 6: 合并**

```bash
git checkout dev
git merge --no-ff console-plan-approval -m "merge: 计划批准上指挥台（维护性补丁，见 docs/v3/13 §8.173）"
```

不 push、不打 tag（维护性补丁，不新增里程碑）。
