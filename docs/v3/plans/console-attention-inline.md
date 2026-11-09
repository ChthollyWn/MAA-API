# 指挥台「待你处理」进主会话 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把指挥台「待你处理」整块从桌面右栏 / 手机底部面板搬到总控会话内容尾部，并让记忆与工单两类也能就地处理。

**Architecture:** 纯前端改动。新增 `AttentionFeed` 作为会话尾部区块（负责状态分支与标题），`AttentionList` 收敛为纯列表；记忆与工单两类卡片各换成新的就地操作组件（分别调记忆审核与工单发消息接口）。`ConsolePage` 的两套布局删掉面板入口，数据与实时刷新沿用现有 `useAttention`、WS 失效与 `useConversationScroll`。

**Tech Stack:** React 19 + TypeScript、TanStack Query、Vitest + Testing Library；后端零改动。

**Spec:** `docs/v3/specs/console-attention-inline.md`

## Global Constraints

- 分支 `console-attention-inline`（已从 `dev` 创建）；完成后 `git merge --no-ff` 回 `dev` 并删除分支；**不 push**。
- 后端零改动：不碰 `maa_api/`、`web/openapi.json`、`web/src/types/api.d.ts`、`scripts/dump_openapi.py`。
- `web/src/features/console/attention.ts`（`buildAttentionItems`）与 `use-attention.ts` 的合并/去重/排序、`excludeIds` 语义不改。
- `08 §1.1`「一次一问」、确认冒泡、`ConfirmationDock` 与 IM 审批口径不改。
- 文案与术语按 `docs/v3/11` §11；界面不出现代码名、里程碑号与文档坐标。
- 每个任务一个提交；提交信息风格 `feat(console): …（维护性补丁，见 docs/v3/13 §8.176）`。
- 前端命令都在 `web/` 下跑：`pnpm typecheck`、`pnpm test --run <文件>`、`pnpm build`。
- 进度写 `.superpowers/sdd/2026-11-09-console-attention-inline/progress.md`；完成后复制为 `docs/v3/ledgers/console-attention-inline.md` 并补「Rulings I made」。

## Review Focus

- **手机端没有面板入口后，待办是否真的可见可操作**：任何处理都要能在手机主会话里完成，不依赖 sheet/summary。T1 的「手机端直接在主会话处理」用例负责。
- **处理掉最后一张卡时 resolver 错误不能跟着消失**：计划审批失败等错误在列表清空后仍要可见。T1 改写 `plan-attention.test.tsx` 的「keeps the plan error visible after the last item disappears」。
- **实时副本挂起卡与区块不重复**：同一张确认卡在 live turn 与「待你处理」只能出现一次。T1 新增去重用例。
- **记忆审核的重复/过期提交**：在别处已处理（409 `MEMORY_NOT_PROPOSED`）不能卡死其它行。T2 用例。
- **工单回复失败可恢复**：409 `TICKET_ENDED` 或 500 后输入保留、错误可读、可重试。T3 用例。

---

### Task 1: 把「待你处理」搬进总控会话尾部

**Files:**
- Create: `web/src/features/console/AttentionFeed.tsx`
- Modify: `web/src/features/console/AttentionList.tsx`
- Modify: `web/src/features/console/ConsolePage.tsx`
- Delete: `web/src/features/console/AttentionPanel.tsx`、`AttentionSheet.tsx`、`AttentionSummaryBar.tsx`
- Test: `web/src/features/console/ConsolePage.test.tsx`、`web/src/features/console/plan-attention.test.tsx`
- Docs: `docs/v3/11-前端指挥台.md` §3.1、§3.2（位置）、§3.3、§5、§10

**Interfaces:**
- Consumes: `useAttention(options) → AttentionState`（现状不变）、`AttentionList` 现有卡片与四个 resolver。
- Produces:
  - `AttentionFeed({ attention, specTitles }: { attention: AttentionState; specTitles: ReadonlyMap<string, string> })`：有 items 时渲染 `Section title="待你处理" description={`${items.length} 件`}`；无 items 且无 resolver 错误时返回 `null`；`attention.error` 时渲染 `读取待你处理的事项失败：…` + 重试；resolver 错误在列表空时仍渲染。
  - `AttentionListProps = { attention: AttentionState; specTitles: ReadonlyMap<string, string> }`：只渲染 `<ol aria-label="待你处理">` 与条目，不再有加载/错误/空态。

- [ ] **Step 1: 改写测试到新结构（先红）**

编辑 `ConsolePage.test.tsx`：

1. `lists confirmations, questions and waiting tickets with their origin`：删掉「右栏 `complementary`」断言；改为
   ```ts
   const session = await screen.findByRole('region', { name: '总控会话' })
   expect(within(session).getByRole('heading', { name: '待你处理' })).toBeInTheDocument()
   expect(within(session).getByText('等体力的日常')).toBeInTheDocument()
   expect(within(session).getByText('来自工单「确认来源」· 通用 Worker')).toBeInTheDocument()
   const aside = screen.getByRole('complementary', { name: '指挥台侧栏' })
   expect(within(aside).queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument()
   ```
   `data-attention-kind` / `data-attention-id` 断言保留。
2. 把 `opens the attention sheet from the summary bar on phones` 改名为 `renders attention cards in the main session on phones`：断言不再有「2 件事等你处理」按钮与 `dialog`；`待你处理` 标题、确认卡、提问卡直接在页面里；就地「批准并继续」与「提交」后断言 `confirmationPosts` / `questionPosts` 各 1 条；最后 `await waitFor(() => expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument())`。
3. 新增 `shows each pending card once when the live turn already renders it`：`createConsoleApi({ messages: [], confirmations: [confirmation], confirmationDetails: { 'confirm-1': confirmation }, stream: () => heldStream([... confirmation_required(confirm-1)]) })`，发送一条消息后断言 `screen.getAllByRole('heading', { name: '需要确认此操作' })` 长度为 1，且 `queryByRole('heading', { name: '待你处理' })` 不存在（唯一一项被 `excludeIds` 排除）。
4. 新增 `hides the attention section when nothing is pending`：只有 `messages: []` 时 `queryByRole('heading', { name: '待你处理' })` 不存在。
5. `lists pending memories as one attention entry (M79)` 先保留（T2 再改交互）。

编辑 `plan-attention.test.tsx`：

6. `opens the plan card in the phone attention sheet` 改为 `shows the plan card in the main session on phones`：去掉汇总行点击与 `dialog`，直接断言 `计划待批准` 可见并点「批准计划」。
7. `keeps the plan error visible after the last item disappears`：把 `findByText('还没有要你处理的事')` 换成 `await waitFor(() => expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument())`，错误文本断言保留。
8. `keeps the phone attention sheet open when the plan approval fails` 改为 `keeps the plan error visible on phones after the list empties`：去掉 sheet 点击，直接点「批准计划」，断言错误文本仍在、`待你处理` 标题消失。
9. `suppresses the ticket card after approving the plan` 里 `findByText('还没有要你处理的事')` 同样换成 `queryByRole('heading', { name: '待你处理' })` 不存在。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx src/features/console/plan-attention.test.tsx`
Expected: FAIL（`待你处理` 不在 `总控会话` 区域内、手机端仍要打开 sheet 等）。

- [ ] **Step 3: 新建 `AttentionFeed.tsx`**

```tsx
import { toApiError } from '@/api/errors'
import { Section } from '@/components/ui/section'
import { ErrorState } from '@/components/ui/states'
import { ErrorAlert } from '@/features/agent/StatusAlerts'
import { AttentionList } from './AttentionList'
import type { AttentionState } from './use-attention'

export interface AttentionFeedProps {
  attention: AttentionState
  specTitles: ReadonlyMap<string, string>
}

export function AttentionFeed({ attention, specTitles }: AttentionFeedProps) {
  const resolverErrors = (
    <>
      {attention.resolver.error ? <ErrorAlert error={attention.resolver.error} onDismiss={attention.resolver.clearError} /> : null}
      {attention.planResolver.error ? (
        <ErrorAlert
          error={{ ...attention.planResolver.error, message: `处理计划失败：${attention.planResolver.error.message}` }}
          onDismiss={attention.planResolver.clearError}
        />
      ) : null}
      {attention.alertResolver.error ? <ErrorAlert error={attention.alertResolver.error} onDismiss={attention.alertResolver.clearError} /> : null}
    </>
  )
  const hasResolverErrors = Boolean(attention.resolver.error || attention.planResolver.error || attention.alertResolver.error)
  if (attention.error) {
    return (
      <div className="mt-4 border-t pt-4">
        <ErrorState message={`读取待你处理的事项失败：${toApiError(attention.error).message}`} onRetry={attention.refetch} />
      </div>
    )
  }
  if (attention.isPending && attention.items.length === 0) return null
  if (attention.items.length === 0) {
    if (!hasResolverErrors) return null
    return <div className="mt-4 space-y-3 border-t pt-4">{resolverErrors}</div>
  }
  return (
    <Section title="待你处理" description={`${attention.items.length} 件`} className="mt-4 border-t pt-4">
      {resolverErrors}
      <AttentionList attention={attention} specTitles={specTitles} />
    </Section>
  )
}
```

- [ ] **Step 4: 收敛 `AttentionList.tsx`**

删除加载/错误/空态分支与 `resolverErrors`（挪到 `AttentionFeed`），只保留 `<ol aria-label="待你处理">` 与各 `item.kind` 分支；确认/提问/计划/告警卡片与 `data-attention-*` 断言属性不变；记忆、工单分支本任务先保持现状（整卡 `Link`），T2/T3 再换。移除不再使用的 import（`ErrorState`、`LoadingState`、`toApiError`、`ErrorAlert`）。

- [ ] **Step 5: 改 `ConsolePage.tsx` 两套布局**

1. import 换成 `AttentionFeed`，删除 `AttentionPanel` / `AttentionSheet` / `AttentionSummaryBar` 三个 import。
2. 删除 `sheetOpen` 状态与「处理完最后一张自动收起」的 `useEffect`。
3. `tailKey` 数组里加入待办签名：
   ```ts
   const attentionKey = attention.items.map((item) => item.id).join(',')
   // tailKey 数组末尾追加 attentionKey
   ```
4. `conversation` 改为 fragment：`<ConversationView … />` 之后放 `<AttentionFeed attention={attention} specTitles={specTitles} />`。
5. 桌面 `<aside aria-label="指挥台侧栏">` 只留 `ActiveTicketsPanel`。
6. 手机 `toolbar` 只保留进行中工单胶囊条：`tickets.length > 0` 时渲染 `<ActiveTicketsStrip … />`，否则 `undefined`；删除 `<AttentionSheet … />`。
7. `git rm` 三个组件文件。

- [ ] **Step 6: 跑测试与类型检查**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx src/features/console/plan-attention.test.tsx`
Expected: PASS（全部）。
Run: `cd web && pnpm typecheck`
Expected: PASS（0）。

- [ ] **Step 7: 同步文档**

`docs/v3/11-前端指挥台.md`：

- §3.1 表格：`待你处理` 一行的桌面改为「总控会话内容尾部（`ConversationView` 之后），随消息滚动」、手机改为「会话内容尾部、输入栏上方，随整页滚动」；`总控会话` 一行补「末尾是『待你处理』块」；`进行中的工单` 手机列去掉「『待你处理』那一行下面」。
- §3.2 开头补位置说明（会话尾部、空态不渲染）；删除「手机面板自动收起」相关句子。
- §3.3 末尾补一句：会话内容最后是 §3.2 的「待你处理」块。
- §5 把「冒泡来的确认、提问……真正的确认卡、提问卡在『待你处理』里」改为「……在总控会话末尾的『待你处理』块里」。
- §10 删除「『待你处理』面板：`ResponsiveSheet` ……」条目，并把第一条里「『待你处理』汇总行与」删掉。

- [ ] **Step 8: 提交**

```bash
git add -A web/src/features/console docs/v3/11-前端指挥台.md
git commit -m "refactor(console): 待你处理搬进总控会话尾部（维护性补丁，见 docs/v3/13 §8.176）"
```

---

### Task 2: 记忆就地审核

**Files:**
- Create: `web/src/features/console/AttentionMemoryCard.tsx`
- Modify: `web/src/features/console/AttentionList.tsx`（memory 分支）
- Modify: `web/src/features/console/test-utils.tsx`（假后端记忆审核 + `memoryReviews()`）
- Test: `web/src/features/console/ConsolePage.test.tsx`
- Docs: `docs/v3/11-前端指挥台.md` §3.2、§11

**Interfaces:**
- Consumes: `attentionMemoriesQuery`、`reviewMemory(id, { approved })`、`invalidateMemories(queryClient)`、`describeMemoryError`、`KIND_LABELS`（均来自 `@/features/memory/api`）。
- Produces: `AttentionMemoryCard({ count, previews }: { count: number; previews: readonly string[] })`；`AttentionList` 的 memory 分支渲染 `<AttentionMemoryCard count={item.count} previews={item.previews} />`；假后端新增 `memoryReviews: () => RecordedRequest[]`。

- [ ] **Step 1: 改测试（先红）**

`ConsolePage.test.tsx` 的 `lists pending memories as one attention entry (M79)` 改为 `reviews pending memories in place`：

```ts
stubMedia((query) => query === '(min-width: 1024px)')
const api = createConsoleApi({
  messages: [],
  memories: [
    makeMemory({ id: 'm1', content: '刷图不要用源石恢复理智' }),
    makeMemory({ id: 'm2', content: 'ADB 掉线先重连一次' }),
    makeMemory({ id: 'm3', status: 'active', content: '已生效的记忆' }),
  ],
})
renderConsole(api)

const card = await screen.findByRole('region', { name: '总控会话' })
expect(within(card).getByText('2 条记忆待审核')).toBeInTheDocument()
expect(within(card).getByRole('link', { name: '去记忆页' })).toHaveAttribute('href', '/more/memories')
fireEvent.click(within(card).getByRole('button', { name: '展开审核' }))
const first = document.querySelector('[data-memory-id="m1"]') as HTMLElement
fireEvent.click(within(first).getByRole('button', { name: '批准' }))
await waitFor(() => expect(api.memoryReviews()).toHaveLength(1))
expect(api.memoryReviews()[0]?.body).toEqual({ approved: true })
await waitFor(() => expect(screen.queryByText('刷图不要用源石恢复理智')).not.toBeInTheDocument())
const second = document.querySelector('[data-memory-id="m2"]') as HTMLElement
fireEvent.click(within(second).getByRole('button', { name: '拒绝' }))
await waitFor(() => expect(api.memoryReviews()[1]?.body).toEqual({ approved: false }))
await waitFor(() => expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument())
```

新增 `shows the memory review error without breaking the other rows`：用 `overrides` 让 `POST /api/agent/memories/m1/review` 返回 409 `MEMORY_NOT_PROPOSED`（message「这条记忆已经不在待审核状态，刷新后再试。」），断言错误文本出现、`m2` 行仍在且可点「拒绝」。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx`
Expected: FAIL（没有「展开审核」按钮与 `memoryReviews`）。

- [ ] **Step 3: 假后端加记忆审核**

`test-utils.tsx`：在 `GET /api/agent/memories` 分支后加

```ts
const memoryReviewMatch = /^\/api\/agent\/memories\/([^/]+)\/review$/.exec(url.pathname)
if (memoryReviewMatch && request.method === 'POST') {
  const id = decodeURIComponent(memoryReviewMatch[1]!)
  const memory = state.memories.find((item) => item.id === id)
  if (!memory) return json({ error: { code: 'MEMORY_NOT_FOUND', message: '记忆不存在' } }, 404)
  if (memory.status !== 'proposed') {
    return json({ error: { code: 'MEMORY_NOT_PROPOSED', message: '这条记忆已经不在待审核状态，刷新后再试。' } }, 409)
  }
  memory.status = (body as Json | undefined)?.approved ? 'active' : 'rejected'
  memory.reviewed_at = '2026-11-09T05:00:00Z'
  return json(memory)
}
```

返回值加 `memoryReviews: () => requests.filter((item) => item.method === 'POST' && /^\/api\/agent\/memories\/[^/]+\/review$/.test(item.path))`。

- [ ] **Step 4: 写 `AttentionMemoryCard.tsx`**

按规格 §4.3：`useQuery(attentionMemoriesQuery)` 取 `status === 'proposed'` 的项；本地 `expanded` 状态；`useMutation` 调 `reviewMemory(id, { approved })`，成功 `invalidateMemories(queryClient)`；失败按 `mutation.variables.id` 在该行下渲染 `InlineAlert` + `describeMemoryError`。

结构（文案逐字）：折叠态 `{count} 条记忆待审核` + `Badge variant="primary">记忆</Badge>` + 最多 3 条 `previews` 单行截断 + 「展开审核」按钮 + 「去记忆页」链接；展开态逐条 `data-memory-id` 行（`KIND_LABELS` 徽章、完整正文、「批准」「拒绝」按钮，`review.isPending && review.variables?.id === memory.id` 时禁用），底部「收起」+「去记忆页」。

- [ ] **Step 5: 接到 `AttentionList` 的 memory 分支**

`item.kind === 'memory'` 时渲染 `<AttentionMemoryCard count={item.count} previews={item.previews} />`（`li` 的 `data-attention-kind`/`data-attention-id` 保留）；删掉旧的整卡 `Link`。

- [ ] **Step 6: 跑测试与类型检查**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx`
Expected: PASS。
Run: `cd web && pnpm typecheck`
Expected: PASS。

- [ ] **Step 7: 同步文档**

`docs/v3/11-前端指挥台.md` §3.2 的「记忆提议」行把呈现改成「会话尾部汇总卡，可展开逐条批准/拒绝（改后批准去记忆页）」；§11 文案表补「展开审核 / 收起 / 去记忆页 / 批准 / 拒绝」。

- [ ] **Step 8: 提交**

```bash
git add -A web/src/features/console docs/v3/11-前端指挥台.md
git commit -m "feat(console): 记忆在待你处理里就地审核（维护性补丁，见 docs/v3/13 §8.176）"
```

---

### Task 3: 工单就地回复

**Files:**
- Create: `web/src/features/console/AttentionTicketCard.tsx`
- Modify: `web/src/features/console/AttentionList.tsx`（ticket 分支）
- Modify: `web/src/features/console/test-utils.tsx`（`ticketMessagePosts()`）
- Test: `web/src/features/console/ConsolePage.test.tsx`
- Docs: `docs/v3/11-前端指挥台.md` §3.2、§11

**Interfaces:**
- Consumes: `streamSse(path, body, signal, onEvent, { detach: true })`（`@/features/agent/stream`）、`toast`、`toApiError`、`TICKET_STATUS_LABELS`、`TicketView`（`./api`）、`keys.agent.tickets.lists()`、`keys.agent.tickets.messagesAll(ticketId)`。
- Produces: `AttentionTicketCard({ ticket }: { ticket: TicketView })`；假后端 `ticketMessagePosts: () => RecordedRequest[]`。

- [ ] **Step 1: 改测试（先红）**

`ConsolePage.test.tsx` 新增 3 条：

1. `replies to a waiting ticket in place`：
   ```ts
   stubMedia((query) => query === '(min-width: 1024px)')
   const api = createConsoleApi({
     messages: [],
     attentionTickets: [makeTicket({ id: 't-waiting', title: '每日免费单抽没领到', status: 'waiting',
       human_action: { type: 'question', prompt: '请确认是否批准验证流水线？', ref_id: null } })],
   })
   renderConsole(api)
   expect(await screen.findByText('请确认是否批准验证流水线？')).toBeInTheDocument()
   fireEvent.click(screen.getByRole('button', { name: '回复' }))
   fireEvent.change(screen.getByLabelText('回复工单'), { target: { value: '批准，明早 04:00 跑' } })
   fireEvent.click(screen.getByRole('button', { name: '发送' }))
   await waitFor(() => expect(api.ticketMessagePosts()).toHaveLength(1))
   expect(api.ticketMessagePosts()[0]?.path).toBe('/api/agent/tickets/t-waiting/messages')
   expect(api.ticketMessagePosts()[0]?.body).toEqual({ content: '批准，明早 04:00 跑' })
   expect(await screen.findByText('已发给工单')).toBeInTheDocument()
   expect(screen.queryByLabelText('回复工单')).not.toBeInTheDocument()
   ```
2. `keeps the ticket reply failure visible and retryable`：`overrides` 让 `POST /api/agent/tickets/t-waiting/messages` 返回 500 `SERVICE_UNAVAILABLE`（message「服务暂时不可用」）；断言 `发送消息失败：服务暂时不可用`、输入框仍有内容、可再次点「发送」。
3. `ends the ticket reply when the ticket has ended`：同一 requests 返回 409 `TICKET_ENDED`（message「工单已结束，不能再发消息」）；断言错误文本。
4. `hides reply for a ticket without human action`：`attentionTickets: [makeTicket({ id: 't-blocked', status: 'blocked', summary: '卡在缺少工具' })]`（`human_action: null`）→ 出现「打开工单」、不出现「回复」。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx`
Expected: FAIL（没有「回复」「发送」）。

- [ ] **Step 3: 假后端加记录器**

`test-utils.tsx` 返回值加 `ticketMessagePosts: () => requests.filter((item) => item.method === 'POST' && /^\/api\/agent\/tickets\/[^/]+\/messages$/.test(item.path))`（现有 POST 分支已返回 SSE，detach 发送可直接用；失败注入走既有 `overrides`）。

- [ ] **Step 4: 写 `AttentionTicketCard.tsx`**

按规格 §4.4：标题 `Link` 到 `/tickets/${ticket.id}` + `Badge`（`ticket.status === 'blocked' ? 'danger' : 'warning'`，文案 `TICKET_STATUS_LABELS`）；正文 `ticket.humanAction?.prompt ?? ticket.summary`；操作行「回复」（仅 `humanAction?.prompt` 非空）与「打开工单」；展开后 `Textarea`（`aria-label="回复工单"`、`rows={2}`、`maxLength={32000}`、占位「回复工单…」）+「发送」「取消」。

发送 mutation：

```ts
mutationFn: (content: string) =>
  streamSse(`/api/agent/tickets/${encodeURIComponent(ticket.id)}/messages`, { content },
    new AbortController().signal, () => {}, { detach: true })
```

成功：`toast.success('已发给工单')`、清空草稿并收起、`invalidateQueries(keys.agent.tickets.lists())`、`invalidateQueries(keys.agent.tickets.messagesAll(ticket.id))`。失败：错误按 `发送消息失败：${toApiError(error).message}` 渲染在卡片内、输入保留；`status === 409` 时同样失效工单列表。空草稿禁用「发送」。

- [ ] **Step 5: 接到 `AttentionList` 的 ticket 分支**

`item.kind === 'ticket'` 时渲染 `<AttentionTicketCard ticket={item.ticket} />`（`li` 的 `data-attention-kind`/`data-attention-id` 保留）；删掉旧的整卡 `Link`。

- [ ] **Step 6: 跑测试与类型检查**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx`
Expected: PASS。
Run: `cd web && pnpm typecheck`
Expected: PASS。

- [ ] **Step 7: 同步文档**

`docs/v3/11-前端指挥台.md` §3.2 的「工单」行把呈现改为「工单卡：标题可点、状态、`human_action.prompt`，就地回复（发给 Worker）与『打开工单』；无 `human_action` 时只给打开入口」；§11 文案表补「回复 / 发送 / 已发给工单 / 发送消息失败」。

- [ ] **Step 8: 提交**

```bash
git add -A web/src/features/console docs/v3/11-前端指挥台.md
git commit -m "feat(console): 工单在待你处理里就地回复（维护性补丁，见 docs/v3/13 §8.176）"
```

---

### Task 4: 文档收尾、全量验证与合并

**Files:**
- Modify: `docs/v3/02-系统架构.md` §4.2（`console/` 一行）
- Modify: `docs/v3/13-决策记录.md`（§8 末尾新增 §8.176）
- Create: `docs/v3/ledgers/console-attention-inline.md`
- Modify: `.superpowers/sdd/2026-11-09-console-attention-inline/progress.md`（执行中随任务更新）

**Interfaces:**
- Consumes: 前三个任务的全部产物。
- Produces: 可合并的 `console-attention-inline` 分支与台账。

- [ ] **Step 1: 全量验证（先跑，失败先修）**

```bash
cd web && pnpm typecheck && pnpm test --run && pnpm build
cd .. && .venv/bin/python scripts/frontend_smoke.py
.venv/bin/python scripts/dump_openapi.py --check
cd web && pnpm gen:api:check && cd ..
.venv/bin/python -m pytest -o addopts= -q 2>&1 | tail -5
.venv/bin/python scripts/check_doc_links.py | tail -2
grep -rn "AttentionPanel\|AttentionSheet\|AttentionSummaryBar\|件事等你处理" web/src || echo "no residue"
git status --short
```

Expected：typecheck/test/build 0；`SMOKE OK`；`dump_openapi --check` 0；`gen:api:check` 0；后端 pytest 只有既有 3 条真资源失败；`BROKEN 0`；grep 无输出；工作区只有预期改动。

- [ ] **Step 2: 文档收尾**

- `docs/v3/02-系统架构.md` §4.2 的 `console/` 行改为「M72 指挥台（首页）：总控会话（含『待你处理』块）、进行中的工单」。
- `docs/v3/13-决策记录.md` 末尾新增：

```md
### 8.176 指挥台待你处理进主会话（维护性补丁，2026-11-09）

- 用户要求：所有「待你处理」都要在主会话中体现并处理。三选一（会话尾部一段 / 头部常驻折叠区 / 挂到来源事件卡）后选**会话流尾部一段**。
- 实际：`AttentionList` 从桌面右栏与手机底部面板搬进总控会话内容尾部；新增 `AttentionFeed`；删除 `AttentionPanel`/`AttentionSheet`/`AttentionSummaryBar`。记忆由汇总链接升级为就地逐条批准/拒绝；工单由整卡链接升级为卡片 + 就地回复（SSE detach）。后端端点/事件/错误码/配置/表零改动。
- 裁决：……（记录执行中的实现裁决）
- 规格与计划：specs/console-attention-inline.md、plans/console-attention-inline.md；台账见 ledgers/console-attention-inline.md。
- 验证：见台账「验收」一节。
```

- [ ] **Step 3: 台账**

把 `.superpowers/sdd/2026-11-09-console-attention-inline/progress.md` 复制为 `docs/v3/ledgers/console-attention-inline.md`，补「Rulings I made」与「验收」两节（命令 + 实际输出，长输出只贴尾部）。

- [ ] **Step 4: 提交并合并**

```bash
git add docs/v3/02-系统架构.md docs/v3/13-决策记录.md docs/v3/ledgers/console-attention-inline.md
git commit -m "docs: 记录待你处理进主会话的决策（13 §8.176）与台账"
git checkout dev
git merge --no-ff console-attention-inline -m "merge: 指挥台待你处理进主会话（维护性补丁，见 docs/v3/13 §8.176）"
git branch -d console-attention-inline
git status -sb && git log --oneline -6
```

Expected：`## dev` 干净，合并提交在顶部；不 push。

---

## Self-Review

- 覆盖规格：§4.1 位置/删除（T1）、§4.2 卡片来源（T1 四类 + T2 记忆 + T3 工单）、§4.3（T2）、§4.4（T3）、§4.5 文件（T1–T3）、§4.6 滚动（T1 的 `tailKey`）、§4.7 文案（T2/T3 文档步）、§4.8 边界（T1/T2/T3 用例）、§5 测试（各任务）、§6 文档（T1/T2/T3/T4）、§7 验收（T4）。
- 类型一致：`AttentionFeedProps`、`AttentionMemoryCard({ count, previews })`、`AttentionTicketCard({ ticket })` 与各任务用例中的调用一致。
- Review Focus 的五条各自有任务用例（T1 ×3、T2 ×1、T3 ×2，工单失败拆成功/失败两条）。
