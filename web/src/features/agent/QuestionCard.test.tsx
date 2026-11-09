import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { QuestionCard } from './QuestionCard'
import type { AgentQuestionPrompt } from './types'

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** 提问接口的替身：记下每次请求，由用例决定返回什么。 */
function installApi(respond: (request: RecordedRequest) => Response) {
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const record: RecordedRequest = {
      method: request.method,
      path: url.pathname,
      body: text ? JSON.parse(text) as unknown : undefined,
    }
    requests.push(record)
    return respond(record)
  })
  vi.stubGlobal('fetch', fetchMock)
  return requests
}

function makeQuestion(overrides: Partial<AgentQuestionPrompt> = {}): AgentQuestionPrompt {
  return {
    question_id: 'q-1',
    question: '用哪个渠道刷 1-7？',
    options: [
      { label: '理智', description: '默认渠道，最多 6 次' },
      { label: '理智药', description: null },
      { label: '源石', description: '不推荐' },
    ],
    multiple: false,
    ...overrides,
  }
}

function renderCard(question: AgentQuestionPrompt, onResolved = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <QuestionCard question={question} onResolved={onResolved} />
    </QueryClientProvider>,
  )
  return onResolved
}

const submitted = (question: AgentQuestionPrompt, status = 'answered', selection: string[] = []) =>
  json({ ...question, status, selection })

describe('agent question card', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('asks for exactly one choice, lets the user change it, and submits the picked label (M21)', async () => {
    const requests = installApi(() => submitted(makeQuestion()))
    const onResolved = renderCard(makeQuestion())

    expect(screen.getByText('用哪个渠道刷 1-7？')).toBeInTheDocument()
    expect(screen.getByText('默认渠道，最多 6 次')).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: '提交' })
    expect(screen.getByText('选一项，或直接跳过结束本轮。')).toBeInTheDocument()
    // 一项都没选就不许提交。
    expect(submit).toBeDisabled()

    const sanity = screen.getByRole('radio', { name: /理智药/ })
    fireEvent.click(sanity)
    expect(sanity).toBeChecked()
    expect(submit).toBeEnabled()

    // 单选互斥：换一项之后前一项自动取消。
    const stone = screen.getByRole('radio', { name: /源石/ })
    fireEvent.click(stone)
    expect(stone).toBeChecked()
    expect(sanity).not.toBeChecked()

    // 再点一次自己可以取消（选定之前先允许反悔），于是又不能提交了。
    fireEvent.click(stone)
    expect(stone).not.toBeChecked()
    expect(submit).toBeDisabled()

    fireEvent.click(sanity)
    fireEvent.click(submit)

    await waitFor(() => expect(requests).toHaveLength(1))
    expect(requests[0]!.method).toBe('POST')
    expect(requests[0]!.path).toBe('/api/agent/questions/q-1')
    expect(requests[0]!.body).toEqual({ selected: ['理智药'] })
    expect(await screen.findByText('已提交选择，Agent 正在继续本轮。')).toBeInTheDocument()
    expect(onResolved).toHaveBeenCalledWith('q-1')
  })

  it('collects several labels when the question is multiple choice (M21)', async () => {
    const requests = installApi(() => submitted(makeQuestion({ multiple: true }), 'answered', ['理智', '源石']))
    const onResolved = renderCard(makeQuestion({
      multiple: true,
      options: [
        { label: '理智', description: null },
        { label: '源石', description: null },
      ],
    }))

    const reason = screen.getByRole('checkbox', { name: '理智' })
    const stone = screen.getByRole('checkbox', { name: '源石' })
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.getByText('选多项，或直接跳过结束本轮。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled()

    fireEvent.click(reason)
    fireEvent.click(stone)
    expect(reason).toBeChecked()
    expect(stone).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    await waitFor(() => expect(requests).toHaveLength(1))
    expect(requests[0]!.body).toEqual({ selected: ['理智', '源石'] })
    expect(onResolved).toHaveBeenCalledWith('q-1')
  })

  it('skips the question without picking anything and ends this turn (M21)', async () => {
    const requests = installApi(() => submitted(makeQuestion(), 'skipped'))
    const onResolved = renderCard(makeQuestion())

    fireEvent.click(screen.getByRole('button', { name: '跳过' }))

    await waitFor(() => expect(requests).toHaveLength(1))
    expect(requests[0]!.path).toBe('/api/agent/questions/q-1/skip')
    expect(requests[0]!.body).toBeUndefined()
    expect(await screen.findByText('已跳过这次提问，本轮已结束。')).toBeInTheDocument()
    // 跳过也要清卡：本轮已经结束，收尾文案由服务端随后推来。
    expect(onResolved).toHaveBeenCalledWith('q-1')
  })

  it('stays answerable without any deadline (questions never expire)', () => {
    renderCard(makeQuestion())

    // 维护性补丁 questions-no-expiry：卡片没有倒计时，也没有本地超时。
    expect(screen.getByText('等待回答')).toBeInTheDocument()
    expect(screen.queryByText(/剩余/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '跳过' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled()

    fireEvent.click(screen.getByRole('radio', { name: /理智药/ }))
    expect(screen.getByRole('button', { name: '提交' })).toBeEnabled()
  })

  it('turns a historical server-side expiry into the finished state instead of a red error', async () => {
    installApi(() => json({ error: { code: 'QUESTION_EXPIRED', message: '提问已过期' } }, 409))
    const onResolved = renderCard(makeQuestion())

    fireEvent.click(screen.getByRole('radio', { name: /理智药/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    expect(await screen.findByText(/这次提问已经结束/)).toBeInTheDocument()
    expect(screen.getByText('已结束')).toBeInTheDocument()
    expect(screen.queryByText(/提交失败/)).not.toBeInTheDocument()
    // 历史过期行不能留在待你处理里，交给调用方把卡撤下。
    expect(onResolved).toHaveBeenCalledWith('q-1')
  })

  it('treats an already answered question as finished and hands the card back (M21)', async () => {
    installApi(() => json({ error: { code: 'QUESTION_ALREADY_ANSWERED', message: '提问已被回答' } }, 409))
    const onResolved = renderCard(makeQuestion())

    fireEvent.click(screen.getByRole('button', { name: '跳过' }))

    expect(await screen.findByText(/这次提问已经结束/)).toBeInTheDocument()
    expect(screen.queryByText(/提交失败/)).not.toBeInTheDocument()
    expect(onResolved).toHaveBeenCalledWith('q-1')
  })

  it('keeps the card usable and reports the error when the request really fails (M21)', async () => {
    installApi(() => json({ error: { code: 'INTERNAL_ERROR', message: '服务端开小差了' } }, 500))
    const onResolved = renderCard(makeQuestion())

    fireEvent.click(screen.getByRole('radio', { name: /理智药/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    expect(await screen.findByText(/提交失败：服务端开小差了/)).toBeInTheDocument()
    expect(onResolved).not.toHaveBeenCalled()
    // 真失败不该把卡锁死：选择还在，可以再试一次。
    expect(screen.getByRole('button', { name: '提交' })).toBeEnabled()
  })

  it('shows the ticket and agent that raised the question (M71)', async () => {
    installApi((request) => {
      if (request.path === '/api/agent/specs') {
        return json({ items: [{ name: 'worker', title: '执行代理' }] })
      }
      return submitted(makeQuestion())
    })
    renderCard(makeQuestion({
      origin: { ticket_id: 'ticket-1', ticket_title: '日常清理', agent: 'worker', run_id: 'run-1' },
    }))

    expect(await screen.findByText('来自工单「日常清理」（执行代理）')).toBeInTheDocument()
  })

  it('names only the agent when the run belongs to no ticket (M71)', async () => {
    installApi((request) => {
      if (request.path === '/api/agent/specs') {
        return json({ items: [{ name: 'worker', title: '执行代理' }] })
      }
      return submitted(makeQuestion())
    })
    renderCard(makeQuestion({
      origin: { ticket_id: null, ticket_title: null, agent: 'worker', run_id: 'run-1' },
    }))

    expect(await screen.findByText('来自执行代理')).toBeInTheDocument()
  })

  it('does not request specs for a question without an origin (M71)', async () => {
    const requests = installApi(() => submitted(makeQuestion()))
    renderCard(makeQuestion())

    await act(async () => { await Promise.resolve() })

    expect(requests.some((request) => request.path === '/api/agent/specs')).toBe(false)
  })
})
