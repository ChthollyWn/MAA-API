import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConversationView } from './ConversationView'
import { createLiveTurn, reduceLiveTurn, type LiveTurn } from './live-turn'
import type { ConfirmationResolver } from './card-resolvers'
import type { V3Message } from './types'

const resolver: ConfirmationResolver = {
  resolve: vi.fn(),
  busyFor: () => null,
  error: null,
  clearError: vi.fn(),
}

function message(overrides: Partial<V3Message> & Pick<V3Message, 'id' | 'role'>): V3Message {
  return { session_id: 'main', seq: overrides.id, ...overrides }
}

function renderView(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={client}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>)
}

describe('ConversationView smoke', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders every entry kind, live cards and queued bubbles', () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      confirmation_id: 'cf1',
      action: 'submit_pipeline',
      risk_level: 'none',
      reason: '提交流水线',
      status: 'pending',
      expires_at: new Date(Date.now() + 600_000).toISOString(),
      payload: {},
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const history: V3Message[] = [
      message({ id: 1, role: 'user', content: '把日常做了\n\n【引用】\n- 知识「日常」' }),
      message({ id: 2, role: 'user', content: '【工单汇报】工单「日常」进行中：已提交', inbox_id: 1, inbox_kind: 'report', inbox_thread_key: 'ticket:t2' }),
      message({ id: 3, role: 'assistant', content: '好的，**马上**开始。' }),
      message({
        id: 4,
        role: 'assistant',
        content: null,
        tool_calls: [{ id: 'c1', function: { name: 'create_ticket', arguments: '{"title":"日常"}' } }],
      }),
      message({ id: 5, role: 'tool', tool_name: 'create_ticket', tool_call_id: 'c1', content: '{"result":{"ticket_id":"t1","title":"日常"}}' }),
      message({
        id: 6,
        role: 'assistant',
        content: null,
        tool_calls: [{ id: 'rep', function: { name: 'report', arguments: '{"status":"ready","summary":"刷完了"}' } }],
      }),
      message({ id: 7, role: 'tool', tool_name: 'report', tool_call_id: 'rep', content: '{"result":{}}' }),
    ]
    let live: LiveTurn = createLiveTurn('orchestrator', '再来一轮', 7)
    live = reduceLiveTurn(live, { type: 'run_start', runId: 'r1', parentRunId: null, agent: 'orchestrator', kind: 'orchestrator_turn', ticketId: null })
    live = reduceLiveTurn(live, { type: 'text_delta', runId: 'r1', text: '正在处理' })
    live = reduceLiveTurn(live, { type: 'ticket_created', runId: null, ticketId: 't9', title: '日常' })
    live = reduceLiveTurn(live, {
      type: 'confirmation_required', runId: 'r1', toolCallId: 'c9', confirmationId: 'cf1',
      origin: { ticket_id: 't2', ticket_title: '日常', agent: 'general', run_id: 'r1' },
    })

    renderView(
      <ConversationView
        scope={{ kind: 'orchestrator' }}
        history={history}
        mainSessionId="main"
        specTitles={new Map([['general', '通用 Worker']])}
        live={live}
        showOptimisticUser
        queued={[{ localId: 'q1', text: '排队的话' }]}
        resolver={resolver}
        earlier={{ hasEarlier: false, loading: false, error: null, onLoad: () => {} }}
      />,
    )

    expect(screen.getByText('引用 1 项')).toBeInTheDocument()
    expect(screen.getByText('工单汇报')).toBeInTheDocument()
    expect(screen.getByText('打开工单')).toHaveAttribute('href', '/tickets/t2')
    expect(screen.getByText(/好的/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '工单「日常」' })).toHaveAttribute('href', '/tickets/t1')
    expect(screen.getByText('汇报')).toBeInTheDocument()
    expect(screen.getByText('已建工单「日常」')).toBeInTheDocument()
    expect(screen.getByText('等待你确认，操作还没有执行。')).toBeInTheDocument()
    expect(screen.getByText('来自工单「日常」· 通用 Worker')).toBeInTheDocument()
    expect(screen.getByText('排队中')).toBeInTheDocument()
  })
})
