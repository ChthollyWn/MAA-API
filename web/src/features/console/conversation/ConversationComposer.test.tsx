import { createRef, type ReactNode, type RefObject } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConversationComposer } from './ConversationComposer'
import { createLiveTurn, type LiveTurn } from './live-turn'
import type { ConversationScope } from './types'
import type { ConversationController } from './use-conversation'

let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function makeController(overrides: Partial<ConversationController> = {}): ConversationController {
  const base: ConversationController = {
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
  return { ...base, ...overrides }
}

function makeLive(overrides: Partial<LiveTurn> = {}): LiveTurn {
  return { ...createLiveTurn('orchestrator', '你好', 0), ...overrides }
}

const pendingConfirmation = { confirmationId: 'cf1', runId: 'r1', toolCallId: 'tc1', origin: null }
const pendingQuestion = {
  question_id: 'q1',
  question: '选哪个',
  options: [],
  multiple: false,
  expires_at: '',
  runId: 'r1',
  origin: null,
}

interface ComposerOptions {
  scope?: ConversationScope
  seed?: { text: string; nonce: number }
}

function composer(
  controller: ConversationController,
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  options: ComposerOptions = {},
): ReactNode {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConversationComposer
          scope={options.scope ?? { kind: 'orchestrator' }}
          controller={controller}
          docked={false}
          textareaRef={textareaRef}
          seed={options.seed}
        />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('M72 ConversationComposer', () => {
  it('orders the status line by stopping, confirmation, question, progress, stopped, background', () => {
    const ref = createRef<HTMLTextAreaElement>()
    const running = makeLive({ running: true })
    const controller = makeController({
      live: { ...running, pendingConfirmation },
      stopping: true,
      canStop: true,
    })
    const view = render(composer(controller, ref))

    expect(screen.getByText('正在停止本轮…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeDisabled()
    expect(screen.queryByText('请先处理待确认的操作')).not.toBeInTheDocument()

    view.rerender(composer({ ...controller, stopping: false }, ref))
    expect(screen.getByText('请先处理待确认的操作')).toBeInTheDocument()
    expect(screen.queryByText('总控正在处理…')).not.toBeInTheDocument()

    view.rerender(composer({
      ...controller,
      stopping: false,
      live: { ...running, pendingConfirmation: null, pendingQuestion },
    }, ref))
    expect(screen.getByText('请先回答等待中的提问')).toBeInTheDocument()

    view.rerender(composer({ ...controller, stopping: false, live: running }, ref))
    expect(screen.getByText('总控正在处理…')).toBeInTheDocument()

    view.rerender(composer({
      ...controller,
      stopping: false,
      live: { ...running, running: false, interrupted: true },
    }, ref))
    expect(screen.getByText('本轮已停止')).toBeInTheDocument()
    expect(screen.queryByText('总控正在处理收到的消息')).not.toBeInTheDocument()

    view.rerender(composer({ ...controller, stopping: false, live: null, canStop: true }, ref))
    expect(screen.getByText('总控正在处理收到的消息')).toBeInTheDocument()

    view.rerender(composer(
      { ...controller, stopping: false, live: null, canStop: true },
      ref,
      { scope: { kind: 'ticket', ticketId: 't1' } },
    ))
    expect(screen.getByText('Worker 正在后台执行')).toBeInTheDocument()

    view.rerender(composer(
      { ...controller, stopping: false, live: running },
      ref,
      { scope: { kind: 'ticket', ticketId: 't1' } },
    ))
    expect(screen.getByText('Worker 正在处理…')).toBeInTheDocument()
  })

  it('shows a hint for /stop when no turn is running', () => {
    vi.useFakeTimers()
    const controller = makeController({ canStop: false })
    render(composer(controller, createRef<HTMLTextAreaElement>()))
    const textarea = screen.getByLabelText('发送给总控') as HTMLTextAreaElement

    fireEvent.change(textarea, { target: { value: '/stop' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(screen.getByText('现在没有在跑的一轮')).toBeInTheDocument()
    expect(controller.stop).not.toHaveBeenCalled()
    expect(controller.send).not.toHaveBeenCalled()
    expect(textarea.value).toBe('')

    act(() => { vi.advanceTimersByTime(3_000) })
    expect(screen.queryByText('现在没有在跑的一轮')).not.toBeInTheDocument()
  })

  it('shows stop for an empty draft during the live turn and send once the draft has text', async () => {
    const controller = makeController({ live: makeLive({ running: true }), canStop: true })
    render(composer(controller, createRef<HTMLTextAreaElement>()))

    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '停止本轮' }))
    expect(controller.stop).toHaveBeenCalledTimes(1)

    const textarea = screen.getByLabelText('发送给总控') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '再发一条' } })
    expect(screen.queryByRole('button', { name: '停止本轮' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(controller.send).toHaveBeenCalledWith({ content: '再发一条', mentions: [] }))
    await waitFor(() => expect(textarea.value).toBe(''))
  })

  it('fills the composer from a seed without sending', () => {
    const controller = makeController()
    const ref = createRef<HTMLTextAreaElement>()
    const view = render(composer(controller, ref))
    const textarea = screen.getByLabelText('发送给总控') as HTMLTextAreaElement

    view.rerender(composer(controller, ref, { seed: { text: '把日常做了', nonce: 1 } }))
    expect(textarea.value).toBe('把日常做了')
    expect(document.activeElement).toBe(textarea)
    expect(controller.send).not.toHaveBeenCalled()

    view.rerender(composer(controller, ref, { seed: { text: '设备连上了吗', nonce: 2 } }))
    expect(textarea.value).toBe('设备连上了吗')
  })
})
