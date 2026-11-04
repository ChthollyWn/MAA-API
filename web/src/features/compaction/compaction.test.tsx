import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from '@/components/ui/toaster'
import type { components } from '@/types/api'
import { ConversationComposer } from '@/features/console/conversation/ConversationComposer'
import { ConversationView } from '@/features/console/conversation/ConversationView'
import type { ConversationController } from '@/features/console/conversation/use-conversation'
import type { V3Message } from '@/features/console/conversation/types'
import { CompactionMarker } from './CompactionMarker'
import { insertMarkers } from './insert-markers'

type CheckpointView = components['schemas']['CheckpointView']

function checkpoint(
  overrides: Partial<CheckpointView> & Pick<CheckpointView, 'id' | 'upto_seq'>,
): CheckpointView {
  return {
    session_id: 'main',
    run_id: null,
    trigger: 'auto',
    status: 'superseded',
    tokens_before: 5000,
    tokens_after: 1000,
    model: 'summary-model',
    degraded: false,
    summary: '较早的消息已按规则压缩。',
    created_at: '2026-11-04T10:00:00Z',
    ...overrides,
  }
}

function message(overrides: Partial<V3Message> & Pick<V3Message, 'id' | 'seq' | 'role'>): V3Message {
  return { session_id: 'main', ...overrides }
}

function renderWithProviders(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
      <Toaster />
    </QueryClientProvider>,
  )
}

function makeController(): ConversationController {
  return {
    history: [],
    visible: [],
    query: { isPending: false, isSuccess: true } as unknown as ConversationController['query'],
    hasEarlier: false,
    loadEarlier: vi.fn(),
    loadingEarlier: false,
    earlierError: null,
    live: null,
    showOptimisticUser: false,
    queued: [],
    send: vi.fn(async () => true),
    stop: vi.fn(),
    stopping: false,
    canStop: false,
    error: null,
    clearError: vi.fn(),
    confirmationResolver: {
      resolve: vi.fn(),
      busyFor: () => null,
      error: null,
      clearError: vi.fn(),
    },
    onQuestionResolved: vi.fn(),
  }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('M74 压缩标记', () => {
  it('标记插在 upto_seq 之后，invalidated 的不显示', () => {
    const history: V3Message[] = [
      message({ id: 1, seq: 1, role: 'user', content: '第一轮的问题' }),
      message({ id: 2, seq: 2, role: 'assistant', content: '第一轮的回答' }),
      message({ id: 3, seq: 3, role: 'user', content: '第三轮的问题' }),
      message({ id: 4, seq: 4, role: 'assistant', content: '第四轮的回答' }),
    ]
    const checkpoints = [
      checkpoint({ id: 'cp-1', upto_seq: 2, status: 'superseded', tokens_before: 98213, tokens_after: 21455 }),
      checkpoint({ id: 'cp-2', upto_seq: 4, status: 'active', tokens_before: 5000, tokens_after: 900 }),
      checkpoint({ id: 'cp-3', upto_seq: 3, status: 'invalidated', tokens_before: 12345, tokens_after: 100 }),
    ]

    const sequence = insertMarkers(history, checkpoints).map((item) => (
      item.kind === 'checkpoint' ? `mark:${item.checkpoint.id}` : `msg:${item.message.seq}`
    ))
    expect(sequence).toEqual(['msg:1', 'msg:2', 'mark:cp-1', 'msg:3', 'msg:4', 'mark:cp-2'])

    const view = renderWithProviders(
      <ConversationView
        scope={{ kind: 'orchestrator' }}
        history={history}
        mainSessionId="main"
        specTitles={new Map()}
        checkpoints={checkpoints}
      />,
    )
    const text = view.container.textContent ?? ''
    const marker1 = '已压缩较早的消息（约 9.8 万 → 2.1 万 token）'
    const marker2 = '已压缩较早的消息（约 5000 → 900 token）'
    expect(text).toContain(marker1)
    expect(text).toContain(marker2)
    expect(text.indexOf('第一轮的回答')).toBeLessThan(text.indexOf(marker1))
    expect(text.indexOf(marker1)).toBeLessThan(text.indexOf('第三轮的问题'))
    expect(text.indexOf('第四轮的回答')).toBeLessThan(text.indexOf(marker2))
    // invalidated 的检查点（12345 → 1.2 万）不渲染。
    expect(text).not.toContain('1.2 万')
  })

  it('展开摘要', () => {
    const view = renderWithProviders(
      <CompactionMarker
        checkpoint={checkpoint({
          id: 'cp-1',
          upto_seq: 2,
          status: 'active',
          degraded: true,
          summary: '第一行摘要\n第二行摘要',
        })}
      />,
    )
    const details = view.container.querySelector('details')
    expect(details).not.toBeNull()
    expect(details!.open).toBe(false)
    expect(screen.getByText('按规则生成')).toBeInTheDocument()

    fireEvent.click(screen.getByText('查看摘要'))
    expect(details!.open).toBe(true)
    expect(screen.getByText(/第一行摘要/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '按规则生成说明' }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('摘要模型不可用时按规则生成，内容比较简略。')
  })

  it('/compact 命令：假后端收到 POST，skipped 时提示没有可压缩的消息', async () => {
    const urlOf = (input: RequestInfo | URL): string => (
      input instanceof Request ? input.url : String(input)
    )
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      if (urlOf(input).endsWith('/api/agent/sessions/s1/compact')) {
        return new Response(JSON.stringify({
          checkpoint_id: null,
          skipped: true,
          tokens_before: 1200,
          tokens_after: 1200,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '没有这个接口' } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const controller = makeController()
    const textareaRef = createRef<HTMLTextAreaElement>()

    renderWithProviders(
      <ConversationComposer
        scope={{ kind: 'orchestrator' }}
        controller={controller}
        docked={false}
        textareaRef={textareaRef}
        sessionId="s1"
      />,
    )

    const textarea = screen.getByLabelText('发送给总控')
    fireEvent.change(textarea, { target: { value: '/compact' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const call = fetchMock.mock.calls.find(([input]) => urlOf(input).endsWith('/api/agent/sessions/s1/compact'))
    expect(call).toBeDefined()
    // openapi-fetch 传的是 Request；方法在 Request 上，不在 init 里。
    expect(call![0] instanceof Request ? call![0].method : (call![1] as RequestInit).method).toBe('POST')
    expect(await screen.findByText('没有可压缩的消息')).toBeInTheDocument()
    expect(controller.send).not.toHaveBeenCalled()
  })
})
