import { describe, expect, it, vi, afterEach } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from '@/components/ui/toaster'
import { ConversationView } from '@/features/console/conversation/ConversationView'
import type { V3Message } from '@/features/console/conversation/types'
import {
  createConsoleApi,
  json,
  type ConsoleApiOverride,
  type OverrideContext,
} from '@/features/console/test-utils'
import type { MessageKind } from './targets'

function message(overrides: Partial<V3Message> & Pick<V3Message, 'id' | 'role'>): V3Message {
  return { session_id: 'main', seq: overrides.id, content: '', ...overrides }
}

/** 一轮完整的回合：人类消息 → 建工单 → 最终回复。 */
function conversation(sessionId = 'main'): V3Message[] {
  return [
    message({ id: 1, role: 'user', session_id: sessionId, content: '把日常做了' }),
    message({
      id: 2,
      role: 'assistant',
      session_id: sessionId,
      content: null,
      tool_calls: [{ id: 'c1', function: { name: 'create_ticket', arguments: '{"title":"日常"}' } }],
    }),
    message({ id: 3, role: 'tool', session_id: sessionId, tool_call_id: 'c1', tool_name: 'create_ticket', content: '{"result":{"ticket_id":"t1"}}' }),
    message({ id: 4, role: 'assistant', session_id: sessionId, content: '已经建好工单。' }),
  ]
}

const DRY_WITH_EFFECT = {
  session_id: 'main',
  to_seq: 1,
  dry_run: true,
  superseded: 4,
  superseded_events: 0,
  checkpoints_invalidated: 0,
  checkpoint_reactivated: null,
  plan_abandoned: false,
  side_effects: [{ seq: 3, tool_name: 'create_ticket', audit_id: 9, detail: '工单「日常」' }],
  notice_message_id: null,
}

const REAL_RESULT = { ...DRY_WITH_EFFECT, dry_run: false, notice_message_id: 5 }

const DRY_CLEAN = { ...DRY_WITH_EFFECT, dry_run: true, side_effects: [] }

function rewindOverride(dry: unknown, real: unknown): ConsoleApiOverride {
  return ({ method, path, body }: OverrideContext) => {
    if (method !== 'POST' || path !== '/api/agent/sessions/main/rewind') return undefined
    const dryRun = (body as { dry_run?: boolean } | undefined)?.dry_run === true
    return json(dryRun ? dry : real)
  }
}

function renderConversation(
  messages: V3Message[],
  options: {
    kind: MessageKind
    running?: boolean
    sessionId?: string
    ticketId?: string
    onResend?: (text: string) => unknown
  },
) {
  const sessionId = options.sessionId ?? 'main'
  const onResend = vi.fn(options.onResend ?? (() => true))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const scope = options.ticketId
    ? { kind: 'ticket' as const, ticketId: options.ticketId }
    : { kind: 'orchestrator' as const }
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[options.ticketId ? `/tickets/${options.ticketId}` : '/']}>
        <ConversationView
          scope={scope}
          history={messages}
          mainSessionId={sessionId}
          specTitles={new Map()}
          actions={{
            kind: options.kind,
            running: options.running ?? false,
            sessionId,
            ticketId: options.ticketId,
            onResend,
          }}
        />
        <LocationProbe />
      </MemoryRouter>
      <Toaster />
    </QueryClientProvider>,
  )
  return { onResend }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function rewindRequests(path = '/api/agent/sessions/main/rewind') {
  return (requests: { method: string; path: string; body: unknown }[]) =>
    requests.filter((request) => request.method === 'POST' && request.path === path)
}
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('回溯（M80 §4.6）', () => {
  it('回到这里：先 dry_run，确认框逐字展示条数与副作用，再发正式请求', async () => {
    const api = createConsoleApi({
      messages: conversation() as unknown as Record<string, unknown>[],
      overrides: [rewindOverride(DRY_WITH_EFFECT, REAL_RESULT)],
    })
    renderConversation(conversation(), { kind: 'orchestrator' })

    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[0]!)
    fireEvent.click(await screen.findByRole('button', { name: '回到这里' }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('回到这条消息？')).toBeInTheDocument()
    expect(within(dialog).getByText('之后的 4 条消息会折叠，不再进上下文。')).toBeInTheDocument()
    expect(within(dialog).getByText('已经执行的操作不会撤销：')).toBeInTheDocument()
    expect(within(dialog).getByText('create_ticket（工单「日常」）')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '回到这里' }))

    await waitFor(() => {
      expect(rewindRequests()(api.requests).map((request) => request.body)).toEqual([
        { to_seq: 1, dry_run: true },
        { to_seq: 1, dry_run: false },
      ])
    })
    expect(await screen.findByText('已回溯')).toBeInTheDocument()
  })

  it('回溯失败：toast 用「回溯失败：{message}」', async () => {
    createConsoleApi({
      messages: conversation() as unknown as Record<string, unknown>[],
      overrides: [
        ({ method, path }: OverrideContext) => (method === 'POST' && path === '/api/agent/sessions/main/rewind'
          ? json({ error: { code: 'RUN_ALREADY_ACTIVE', message: '会话上有一轮正在运行', details: {} } }, 409)
          : undefined),
      ],
    })
    renderConversation(conversation(), { kind: 'orchestrator' })

    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[0]!)
    fireEvent.click(await screen.findByRole('button', { name: '回到这里' }))

    expect(await screen.findByText('回溯失败：会话上有一轮正在运行')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('重新生成：没有副作用时不弹框，回溯后重发那条人类消息', async () => {
    const api = createConsoleApi({
      messages: conversation() as unknown as Record<string, unknown>[],
      overrides: [rewindOverride(DRY_CLEAN, { ...REAL_RESULT, superseded: 0 })],
    })
    const { onResend } = renderConversation(conversation(), { kind: 'orchestrator' })

    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[1]!)
    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }))

    await waitFor(() => {
      expect(rewindRequests()(api.requests)).toHaveLength(2)
    })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    await waitFor(() => expect(onResend).toHaveBeenCalledWith('把日常做了'))
  })

  it('编辑后重发：预填正文、有副作用时确认框，然后重发编辑后的文字', async () => {
    const api = createConsoleApi({
      messages: conversation() as unknown as Record<string, unknown>[],
      overrides: [rewindOverride(DRY_WITH_EFFECT, REAL_RESULT)],
    })
    const { onResend } = renderConversation(conversation(), { kind: 'orchestrator' })

    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[0]!)
    fireEvent.click(await screen.findByRole('button', { name: '编辑后重发' }))

    const textarea = screen.getByRole('textbox', { name: '编辑消息' })
    expect(textarea).toHaveValue('把日常做了')
    fireEvent.change(textarea, { target: { value: '把日常刷完' } })
    fireEvent.click(screen.getByRole('button', { name: '重发' }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('改写后重发？')).toBeInTheDocument()
    expect(within(dialog).getByText('原消息和之后的 3 条消息会折叠。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '重发' }))

    await waitFor(() => {
      expect(rewindRequests()(api.requests).map((request) => request.body)).toEqual([
        { to_seq: -1, dry_run: true },
        { to_seq: -1, dry_run: false },
      ])
    })
    await waitFor(() => expect(onResend).toHaveBeenCalledWith('把日常刷完'))
  })

  it('运行中：四个动作都禁用并写明「正在运行，停下后再操作」', async () => {
    const messages = [
      ...conversation('w1'),
      message({ id: 5, role: 'user', session_id: 'w1', content: '再刷一次' }),
      message({ id: 6, role: 'assistant', session_id: 'w1', content: '好。' }),
    ]
    createConsoleApi({ messages: messages as unknown as Record<string, unknown>[] })
    renderConversation(messages, { kind: 'worker', running: true, sessionId: 'w1', ticketId: 't1' })

    // 第二轮人类消息：回到这里、编辑后重发、分叉三个动作同时出现。
    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[2]!)

    for (const label of ['回到这里', '编辑后重发', '分叉']) {
      expect(await screen.findByRole('button', { name: label })).toBeDisabled()
    }
    expect(screen.getAllByText('正在运行，停下后再操作')).toHaveLength(3)
  })
})

describe('分叉（M80 §4.6）', () => {
  it('分叉工单：写一句给新工单，成功后跳到新工单详情', async () => {
    const api = createConsoleApi({
      messages: conversation('w1') as unknown as Record<string, unknown>[],
      overrides: [
        ({ method, path }: OverrideContext) => (method === 'POST' && path === '/api/agent/tickets/t1/fork'
          ? json({ ticket: { id: 't9' } }, 201)
          : undefined),
      ],
    })
    renderConversation(conversation('w1'), { kind: 'worker', sessionId: 'w1', ticketId: 't1' })

    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[0]!)
    fireEvent.click(await screen.findByRole('button', { name: '分叉' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '分叉工单' })).toBeInTheDocument()
    expect(within(dialog).getByText('新工单带着到这条消息为止的记录和同一份委托，原工单不变。')).toBeInTheDocument()
    const textarea = within(dialog).getByLabelText('接下来怎么做')
    expect(textarea).toHaveAttribute('placeholder', '例如：换成 4 星干员再试一次')
    fireEvent.change(textarea, { target: { value: '换个打法' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '分叉工单' }))

    await waitFor(() => {
      const fork = api.requests.find((request) => request.path === '/api/agent/tickets/t1/fork')
      expect(fork?.body).toEqual({ at_seq: 1 })
    })
    await waitFor(() => {
      const send = api.requests.find((request) => request.method === 'POST' && request.path === '/api/agent/tickets/t9/messages')
      expect(send?.body).toEqual({ content: '换个打法', mentions: [] })
    })
    expect(await screen.findByText('已分叉')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/tickets/t9'))
  })

  it('分叉失败：toast 用「分叉失败：{message}」', async () => {
    createConsoleApi({
      messages: conversation('w1') as unknown as Record<string, unknown>[],
      overrides: [
        ({ method, path }: OverrideContext) => (method === 'POST' && path === '/api/agent/tickets/t1/fork'
          ? json({ error: { code: 'TICKET_NOT_FOUND', message: '工单不存在', details: {} } }, 404)
          : undefined),
      ],
    })
    renderConversation(conversation('w1'), { kind: 'worker', sessionId: 'w1', ticketId: 't1' })

    fireEvent.click(screen.getAllByRole('button', { name: '消息操作' })[0]!)
    fireEvent.click(await screen.findByRole('button', { name: '分叉' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '分叉工单' }))

    expect(await screen.findByText('分叉失败：工单不存在')).toBeInTheDocument()
  })
})

describe('折叠（M80 §4.6）', () => {
  it('连续被取代的 3 条消息合成一个默认收起的折叠条，组内没有动作菜单', () => {
    const messages = [
      message({ id: 1, role: 'user', content: '从这里继续' }),
      message({ id: 2, role: 'assistant', content: '旧的回复一', superseded_at: '2026-11-05T00:00:00Z' }),
      message({ id: 3, role: 'user', content: '旧的人类消息', superseded_at: '2026-11-05T00:00:00Z' }),
      message({ id: 4, role: 'assistant', content: '旧的回复二', superseded_at: '2026-11-05T00:00:00Z' }),
    ]
    createConsoleApi({})
    renderConversation(messages, { kind: 'orchestrator' })

    expect(screen.getByText('已回溯的 3 条消息')).toBeInTheDocument()
    const details = document.querySelector('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
    expect(within(details as HTMLElement).getByText('旧的回复一')).toBeInTheDocument()
    expect(within(details as HTMLElement).queryByRole('button', { name: '消息操作' })).toBeNull()
    // 未被取代的那条仍有动作菜单。
    expect(screen.getAllByRole('button', { name: '消息操作' })).toHaveLength(1)
  })
})
