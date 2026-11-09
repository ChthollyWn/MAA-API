import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { keys } from '@/api/keys'
import ConsolePage from './ConsolePage'
import {
  createConsoleApi,
  event,
  heldStream,
  json,
  makeConfirmation,
  makeMemory,
  makeQuestion,
  makeTicket,
  makeV3Message,
  makeWatchdogAlert,
  renderAt,
  sse,
  stubMedia,
  type Json,
} from './test-utils'

const composerName = '发送给总控'

const runStart = (runId = 'r1') => event('run_start', {
  run_id: runId,
  parent_run_id: null,
  agent: 'orchestrator',
  kind: 'orchestrator_turn',
  ticket_id: null,
})
const runEnd = (runId = 'r1', reason = 'stop') => event('run_end', { run_id: runId, finish_reason: reason })
const delta = (text: string, runId = 'r1') => event('text_delta', { run_id: runId, text })

function userMessage(id: number, content: string, runId: string | null = 'r1'): Json {
  return makeV3Message({ id, role: 'user', content, run_id: runId })
}

function assistantMessage(id: number, content: string, runId: string | null = 'r1'): Json {
  return makeV3Message({ id, role: 'assistant', content, run_id: runId })
}

function renderConsole(api: ReturnType<typeof createConsoleApi>, path = '/') {
  const view = renderAt(path, [{ path: '/', element: <ConsolePage /> }])
  return { ...view, api }
}

async function composerBox() {
  return screen.findByRole('textbox', { name: composerName })
}

async function sendText(text: string) {
  const box = await composerBox()
  fireEvent.change(box, { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  return box
}

describe('M72 指挥台', () => {
  beforeEach(() => {
    document.documentElement.scrollTop = 0
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows the orchestrator history with inbox event cards and ticket links', async () => {
    const api = createConsoleApi({
      messages: [
        makeV3Message({ id: 1, role: 'user', content: '把日常做了\n\n【引用】\n- 知识「日常」', run_id: null }),
        makeV3Message({
          id: 2,
          role: 'user',
          content: '【工单汇报】工单「日常」进行中',
          run_id: null,
          inbox_id: 1,
          inbox_kind: 'report',
          inbox_thread_key: 'ticket:t2',
        }),
        makeV3Message({ id: 3, role: 'assistant', content: '好的，马上开始。', run_id: null }),
      ],
    })
    renderConsole(api)

    expect(await screen.findByText('工单汇报')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /打开工单/ })).toHaveAttribute('href', '/tickets/t2')
    expect(screen.getByText('引用 1 项')).toBeInTheDocument()
    expect(screen.getByText('好的，马上开始。')).toBeInTheDocument()
    expect(screen.getByText('工单「日常」进行中')).toBeInTheDocument()
  })

  it('streams a reply and shows it exactly once after the live copy is dropped', async () => {
    const api = createConsoleApi({
      messages: [],
      stream: (body) => {
        api.state.messages = [
          userMessage(1, String(body.content)),
          assistantMessage(2, '设备在线，队列为空。'),
        ]
        return sse([
          runStart(),
          delta('设备在线，队列为空。'),
          runEnd(),
        ])
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('检查一下')
    expect(await screen.findByText('设备在线，队列为空。')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.getAllByText('设备在线，队列为空。')).toHaveLength(1)
  })

  it('waits for an inline confirmation, then shows the reply exactly once after the live copy is dropped', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    let streamFinished = false
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-1',
      action: 'consume_resource',
      risk_level: 'consume',
      reason: '此操作会使用一次理智药。',
    })
    const api = createConsoleApi({
      messages: [],
      confirmationDetails: { 'confirm-1': confirmation },
      stream: () => {
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-1', name: 'submit_pipeline', arguments: confirmation.payload }),
          event('confirmation_required', {
            run_id: 'r1',
            tool_call_id: 'call-1',
            name: 'submit_pipeline',
            confirmation_id: 'confirm-1',
            expires_at: confirmation.expires_at,
          }),
        ])
        return held.response
      },
      onResolve: () => {
        held?.push([
          event('tool_result', { run_id: 'r1', tool_call_id: 'call-1', name: 'submit_pipeline', audit_id: 31, result: { pipeline_id: 'pipeline-9' } }),
          delta('已提交流水线，当前状态为排队中。'),
          runEnd(),
        ])
        held?.close()
        api.state.messages = [
          userMessage(11, '刷一次 1-7'),
          assistantMessage(12, '已提交流水线，当前状态为排队中。'),
        ]
        streamFinished = true
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('刷一次 1-7')

    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(await screen.findByText('此操作会使用一次理智药。')).toBeInTheDocument()
    expect(screen.getByText('拒绝或过期后不会重试这个操作。')).toBeInTheDocument()
    expect(screen.getByText(/等待你确认/)).toBeInTheDocument()
    const liveTurn = screen.getByRole('region', { name: '本轮消息' })
    expect(within(liveTurn).getByText('submit_pipeline')).toBeInTheDocument()
    expect(within(liveTurn).getByText('等待确认')).toBeInTheDocument()

    fireEvent.click(await screen.findByRole('button', { name: '批准并继续' }))

    expect(await screen.findByText('已提交流水线，当前状态为排队中。')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '需要确认此操作' })).not.toBeInTheDocument()
    const resolution = api.requests.find((item) => item.method === 'POST' && item.path === '/api/confirmations/confirm-1')
    expect(resolution?.body).toEqual({ approved: true })

    await waitFor(() => expect(streamFinished).toBe(true))
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('刷一次 1-7')).toHaveLength(1)
    expect(screen.getAllByText('已提交流水线，当前状态为排队中。')).toHaveLength(1)
  })

  it('keeps the live copy on screen until the refreshed history has arrived', async () => {
    let holdHistory = false
    let releaseHistory: () => void = () => {}
    const api = createConsoleApi({
      messages: [],
      holdMessages: () => (holdHistory ? new Promise<void>((resolve) => { releaseHistory = resolve }) : undefined),
      stream: (body) => {
        api.state.messages = [
          userMessage(51, String(body.content)),
          assistantMessage(52, '设备在线，队列为空。'),
        ]
        holdHistory = true
        return sse([delta('设备在线，队列为空。'), runEnd()])
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('检查一下')

    await waitFor(() => expect(api.messagePages()).toHaveLength(2))
    expect(screen.getByRole('region', { name: '本轮消息' })).toBeInTheDocument()
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.getAllByText('设备在线，队列为空。')).toHaveLength(1)

    act(() => releaseHistory())
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.getAllByText('设备在线，队列为空。')).toHaveLength(1)
  })

  it('keeps the turn and its card when the stream ends while the confirmation is still pending', async () => {
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-7',
      action: 'grant_atomic_ops',
      risk_level: 'atomic',
      reason: '需要点击游戏画面',
      payload: { tool_name: 'click', arguments: { x: 10, y: 20 }, session_id: 'main', window_seconds: 900 },
    })
    const api = createConsoleApi({
      messages: [],
      confirmationDetails: { 'confirm-7': confirmation },
      stream: () => sse([
        runStart(),
        event('tool_start', { run_id: 'r1', tool_call_id: 'call-7', name: 'click', arguments: confirmation.payload }),
        event('confirmation_required', {
          run_id: 'r1',
          tool_call_id: 'call-7',
          name: 'click',
          confirmation_id: 'confirm-7',
          expires_at: confirmation.expires_at,
        }),
      ]),
    })
    const { queryClient } = renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('点一下开始')

    expect(await screen.findByText('会话级屏幕操作授权')).toBeInTheDocument()
    expect(screen.getByText(/15 分钟屏幕操作授权/)).toBeInTheDocument()
    await waitFor(() => expect(api.messagePages()).toHaveLength(2))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(screen.getByRole('region', { name: '本轮消息' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(screen.getByText('等待你确认，操作还没有执行。')).toBeInTheDocument()
  })

  it('renders one copy of the turn when history refreshes while the stream is still live', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const history = [
      userMessage(1, '分析刚才的失败', null),
      assistantMessage(2, '上一轮的回答。', null),
    ]
    const api = createConsoleApi({
      messages: history,
      stream: () => {
        held = heldStream([runStart(), delta('先看看设备。')])
        return held.response
      },
    })
    const { queryClient } = renderConsole(api)

    expect(await screen.findByText('上一轮的回答。')).toBeInTheDocument()
    await sendText('检查一下')
    expect(await screen.findByText('先看看设备。')).toBeInTheDocument()

    api.state.messages = [
      ...history,
      userMessage(201, '检查一下'),
      assistantMessage(202, '先看看设备。'),
    ]
    const before = api.messagePages().length
    act(() => { void queryClient.invalidateQueries({ queryKey: keys.agent.orchestrator.messages() }) })
    await waitFor(() => expect(api.messagePages().length).toBeGreaterThan(before))

    await waitFor(() => expect(screen.getAllByText('检查一下')).toHaveLength(1))
    expect(screen.getAllByText('先看看设备。')).toHaveLength(1)
    expect(screen.getAllByText('分析刚才的失败')).toHaveLength(1)

    act(() => {
      held?.push([runEnd()])
      held?.close()
    })
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.getAllByText('先看看设备。')).toHaveLength(1)
  })

  it('drops the live copy when the pending confirmation was resolved outside this page', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-9',
      action: 'grant_atomic_ops',
      risk_level: 'atomic',
      reason: '需要点击游戏画面',
      payload: { tool_name: 'click', arguments: { x: 10, y: 20 }, session_id: 'main', window_seconds: 900 },
    })
    const api = createConsoleApi({
      messages: [],
      confirmationDetails: { 'confirm-9': confirmation },
      stream: () => {
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-9', name: 'click', arguments: confirmation.payload }),
          event('confirmation_required', {
            run_id: 'r1',
            tool_call_id: 'call-9',
            name: 'click',
            confirmation_id: 'confirm-9',
            expires_at: confirmation.expires_at,
          }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('点一下开始')
    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()

    api.state.messages = [
      userMessage(301, '点一下开始'),
      assistantMessage(302, '确认被拒绝或过期了，我不会重试。'),
    ]
    act(() => {
      held?.push([
        event('tool_result', {
          run_id: 'r1',
          tool_call_id: 'call-9',
          name: 'click',
          error: { code: 'CONFIRMATION_EXPIRED', message: '确认请求已过期' },
        }),
        delta('确认被拒绝或过期了，我不会重试。'),
        runEnd('r1', 'confirmation_resolved'),
      ])
      held?.close()
    })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: '需要确认此操作' })).not.toBeInTheDocument()
    expect(screen.getAllByText('确认被拒绝或过期了，我不会重试。')).toHaveLength(1)
  })

  it('keeps a pending confirmation when an unrelated tool reports its result', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-10',
      action: 'grant_atomic_ops',
      risk_level: 'atomic',
      reason: '需要点击游戏画面',
      payload: { tool_name: 'click', arguments: { x: 10, y: 20 }, session_id: 'main', window_seconds: 900 },
    })
    const api = createConsoleApi({
      messages: [],
      confirmationDetails: { 'confirm-10': confirmation },
      stream: () => {
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-10', name: 'click', arguments: confirmation.payload }),
          event('confirmation_required', {
            run_id: 'r1',
            tool_call_id: 'call-10',
            name: 'click',
            confirmation_id: 'confirm-10',
            expires_at: confirmation.expires_at,
          }),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-shot', name: 'get_screenshot', arguments: {} }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('点一下开始')
    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()

    act(() => {
      held?.push([
        event('tool_result', { run_id: 'r1', tool_call_id: 'call-shot', name: 'get_screenshot', result: { format: 'jpeg' } }),
      ])
    })
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(screen.getByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()

    api.state.messages = [
      userMessage(601, '点一下开始'),
      assistantMessage(602, '确认被拒绝或过期了，我不会重试。'),
    ]
    act(() => {
      held?.push([
        event('tool_result', {
          run_id: 'r1',
          tool_call_id: 'call-10',
          name: 'click',
          error: { code: 'CONFIRMATION_EXPIRED', message: '确认请求已过期' },
        }),
        delta('确认被拒绝或过期了，我不会重试。'),
        runEnd('r1', 'confirmation_resolved'),
      ])
      held?.close()
    })
    await waitFor(() => expect(screen.queryByRole('heading', { name: '需要确认此操作' })).not.toBeInTheDocument())
    expect(screen.getAllByText('确认被拒绝或过期了，我不会重试。')).toHaveLength(1)
  })

  it('asks through a card inside the live turn and keeps the same turn going after the answer', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const question = makeQuestion()
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        api.state.questions = [question]
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-ask', name: 'ask_user', arguments: { question: question.question, options: question.options } }),
          event('question_required', {
            run_id: 'r1',
            tool_call_id: 'call-ask',
            question_id: question.question_id,
            question: question.question,
            options: question.options,
            multiple: false,
            expires_at: question.expires_at,
          }),
        ])
        return held.response
      },
      onQuestion: (id, _action, selected) => {
        api.state.messages = [
          userMessage(101, '刷一次 1-7'),
          assistantMessage(102, '按理智刷。'),
        ]
        held?.push([
          event('tool_result', { run_id: 'r1', tool_call_id: 'call-ask', name: 'ask_user', audit_id: 51, result: { status: 'answered', selected, question_id: id } }),
          delta('按理智刷。'),
          runEnd(),
        ])
        held?.close()
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('刷一次 1-7')

    expect(await screen.findByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(screen.getByText('用哪个渠道刷 1-7？')).toBeInTheDocument()
    expect(screen.getByText('请先回答等待中的提问')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
    const liveTurn = screen.getByRole('region', { name: '本轮消息' })
    expect(within(liveTurn).getByText('向你提问')).toBeInTheDocument()
    expect(within(liveTurn).getByText('ask_user')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { name: '需要你选择' })).toHaveLength(1)

    const submit = screen.getByRole('button', { name: '提交' })
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /理智药/ }))
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    const resolution = await waitFor(() => {
      const posted = api.requests.find((item) => item.method === 'POST' && item.path === '/api/agent/questions/question-1')
      expect(posted).toBeDefined()
      return posted!
    })
    expect(resolution.body).toEqual({ selected: ['理智药'] })
    expect(api.state.questions).toHaveLength(0)

    await waitFor(() => expect(screen.queryByRole('heading', { name: '需要你选择' })).not.toBeInTheDocument())
    expect(await screen.findByText('按理智刷。')).toBeInTheDocument()

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('按理智刷。')).toHaveLength(1)
    expect(screen.queryByText('请先回答等待中的提问')).not.toBeInTheDocument()
  })

  it('ends the turn with the server closing text when the question times out', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const expiredText = '提问超时未得到回答，本轮结束。你可以重新描述需求，我会重新发起提问。'
    const question = makeQuestion({ expires_at: new Date(Date.now() - 1000).toISOString() })
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        api.state.questions = [question]
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-ask', name: 'ask_user', arguments: {} }),
          event('question_required', {
            run_id: 'r1',
            tool_call_id: 'call-ask',
            question_id: question.question_id,
            question: question.question,
            options: question.options,
            multiple: false,
            expires_at: question.expires_at,
          }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('刷一次 1-7')

    expect(await screen.findByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(await screen.findByText('提问已超时，本轮已结束。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '跳过' })).toBeDisabled()

    api.state.questions = []
    api.state.messages = [
      userMessage(111, '刷一次 1-7'),
      assistantMessage(112, expiredText),
    ]
    act(() => {
      held?.push([
        event('tool_result', { run_id: 'r1', tool_call_id: 'call-ask', name: 'ask_user', result: { status: 'expired', question_id: question.question_id } }),
        delta(expiredText),
        runEnd('r1', 'question_unanswered'),
      ])
      held?.close()
    })

    await waitFor(() => expect(screen.queryByRole('heading', { name: '需要你选择' })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText(expiredText)).toHaveLength(1)
    expect(screen.queryByText('请先回答等待中的提问')).not.toBeInTheDocument()
    expect(api.requests.some((item) => item.method === 'POST' && item.path.includes('/skip'))).toBe(false)
  })

  it('keeps the confirmation wording when a confirmation and a question are pending at once', async () => {
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-9',
      action: 'consume_resource',
      risk_level: 'consume',
      reason: '此操作会使用一次理智药。',
    })
    const question = makeQuestion()
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      confirmationDetails: { 'confirm-9': confirmation },
      questions: [question],
      stream: () => {
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-9', name: 'submit_pipeline', arguments: {} }),
          event('confirmation_required', {
            run_id: 'r1',
            tool_call_id: 'call-9',
            name: 'submit_pipeline',
            confirmation_id: 'confirm-9',
            expires_at: confirmation.expires_at,
          }),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-ask', name: 'ask_user', arguments: {} }),
          event('question_required', {
            run_id: 'r1',
            tool_call_id: 'call-ask',
            question_id: question.question_id,
            question: question.question,
            options: question.options,
            multiple: false,
            expires_at: question.expires_at,
          }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('刷一次 1-7')

    expect(await screen.findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(screen.getByText('请先处理待确认的操作')).toBeInTheDocument()
    expect(screen.queryByText('请先回答等待中的提问')).not.toBeInTheDocument()

    expect(api.sentMessages()).toHaveLength(1)
    await act(async () => { held?.close() })
  })

  it('keeps a failed turn visible after the live copy is dropped and links LLM errors to the settings', async () => {
    const failure = '当前模型未通过 function calling 能力检查，无法进行 Agent 对话'
    const api = createConsoleApi({
      messages: [],
      stream: (body) => {
        api.state.messages = [userMessage(21, String(body.content))]
        return sse([event('error', { code: 'LLM_REQUEST_FAILED', message: failure })])
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('你好')

    expect(await screen.findByText(failure)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getByText(failure)).toBeInTheDocument()
    expect(screen.getAllByText('你好')).toHaveLength(1)
    expect(screen.getByRole('link', { name: '前往设置检查 LLM 配置' })).toHaveAttribute('href', '/more/settings/agent')
  })

  it('puts the draft back when the message cannot be sent', async () => {
    const api = createConsoleApi({
      messages: [],
      stream: () => json({ error: { code: 'LLM_NOT_CONFIGURED', message: '请先配置 LLM base_url、api_key 和 model' } }, 400),
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await sendText('查看设备')

    expect(await screen.findByText('请先配置 LLM base_url、api_key 和 model')).toBeInTheDocument()
    expect(box).toHaveValue('查看设备')
    expect(screen.getByRole('link', { name: '前往设置检查 LLM 配置' })).toHaveAttribute('href', '/more/settings/agent')
    expect(api.sentMessages()).toHaveLength(1)
  })
  it('fills the composer from a quick prompt without sending it', async () => {
    const api = createConsoleApi({ messages: [] })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    for (const prompt of ['把日常做了', '设备连上了吗', '上次那条流水线为什么失败']) {
      expect(screen.getByRole('button', { name: prompt })).toBeInTheDocument()
    }

    fireEvent.click(screen.getByRole('button', { name: '上次那条流水线为什么失败' }))

    expect(await composerBox()).toHaveValue('上次那条流水线为什么失败')
    expect(api.sentMessages()).toHaveLength(0)
  })

  it('sends with Enter on a mouse-and-keyboard device and keeps Shift+Enter for a new line', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        held = heldStream([runStart()])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await composerBox()
    fireEvent.change(box, { target: { value: '查看队列' } })
    expect(fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })).toBe(true)
    expect(api.sentMessages()).toHaveLength(0)

    expect(fireEvent.keyDown(box, { key: 'Enter' })).toBe(false)
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(api.sentMessages()[0]!.body).toEqual({ content: '查看队列', mentions: [] })
    expect(await screen.findByText('总控正在处理…')).toBeInTheDocument()

    // 流式进行中：发送按钮原地变「停止」，Enter 不会再发一条。
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()

    api.state.messages = [
      userMessage(31, '查看队列'),
      assistantMessage(32, '队列为空。'),
    ]
    act(() => {
      held?.push([delta('队列为空。'), runEnd()])
      held?.close()
    })
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('队列为空。')).toHaveLength(1)
    fireEvent.change(await composerBox(), { target: { value: '再来一条' } })
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled()
  })

  it('treats Enter as a new line on touch devices and sends only from the button', async () => {
    stubMedia((query) => query === '(pointer: coarse)')
    const api = createConsoleApi({
      messages: [],
      stream: () => sse([delta('好的。'), runEnd()]),
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await composerBox()
    fireEvent.change(box, { target: { value: '第一行' } })
    expect(fireEvent.keyDown(box, { key: 'Enter' })).toBe(true)
    expect(api.sentMessages()).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(api.sentMessages()[0]!.body).toEqual({ content: '第一行', mentions: [] })
  })

  it('手机端轻点发送按钮一次就发出，不依赖可能被键盘收起吞掉的合成 click', async () => {
    stubMedia((query) => query === '(pointer: coarse)')
    const api = createConsoleApi({
      messages: [],
      stream: () => sse([delta('好的。'), runEnd()]),
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await composerBox()
    fireEvent.change(box, { target: { value: '手机端发一条' } })
    act(() => box.focus())
    expect(box).toHaveFocus()

    // 真机第一下轻点会先收起键盘：贴底输入栏随键盘和视口变化位移，浏览器随后就不再把这个
    // 轻点的合成 click 派发到按钮。这里只派发 touch 事件并在中间让输入框失焦，模拟被吞掉
    // 的那一次轻点；jsdom 没有 TouchEvent 触点，事件本身即可。
    const button = screen.getByRole('button', { name: '发送' })
    fireEvent(button, new Event('touchstart', { bubbles: true, cancelable: true }))
    act(() => box.blur())
    expect(box).not.toHaveFocus()
    const touchEnd = new Event('touchend', { bubbles: true, cancelable: true })
    fireEvent(button, touchEnd)

    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(api.sentMessages()[0]!.body).toEqual({ content: '手机端发一条', mentions: [] })
    // 同一次轻点只发一条：真机上合成 click 被 preventDefault 拦掉，这里人为补发验证第二道防线。
    expect(touchEnd.defaultPrevented).toBe(true)
    fireEvent.click(button)
    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
  })

  it('turns the send button into a stop button during a turn and wraps the turn up after stopping', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    let holdHistory = false
    const releases: Array<() => void> = []
    const releaseHistory = () => {
      holdHistory = false
      for (const release of releases.splice(0)) release()
    }
    const api = createConsoleApi({
      messages: [],
      holdMessages: () => (holdHistory ? new Promise<void>((resolve) => { releases.push(resolve) }) : undefined),
      stream: () => {
        held = heldStream([
          runStart(),
          delta('正在查询…'),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-wait', name: 'wait_pipeline', arguments: { pipeline_id: 'p1' } }),
        ])
        return held.response
      },
      onInterrupt: () => {
        api.state.messages = [
          userMessage(201, '查一下队列', null),
          assistantMessage(202, '正在查询…'),
          makeV3Message({
            id: 203,
            role: 'tool',
            tool_name: 'wait_pipeline',
            tool_call_id: 'call-wait',
            content: '{"error":{"code":"AGENT_TURN_INTERRUPTED","message":"本轮已被中断，该调用未完成"}}',
            run_id: 'r1',
          }),
          assistantMessage(204, '本轮已被你中断。'),
        ]
        holdHistory = true
        return { session_id: 'main', interrupted: true, turn_running: false }
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await sendText('查一下队列')

    expect(await screen.findByText('总控正在处理…')).toBeInTheDocument()
    expect(screen.getByText('执行中')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '停止本轮' }))

    await waitFor(() => expect(api.interruptCalls()).toHaveLength(1))
    await waitFor(() => expect(screen.queryByText('总控正在处理…')).not.toBeInTheDocument())
    expect(screen.getByText('正在停止本轮…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeDisabled()
    expect(screen.getByRole('region', { name: '本轮消息' })).toBeInTheDocument()

    act(() => releaseHistory())

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(await screen.findByText('本轮已被你中断。')).toBeInTheDocument()
    expect(screen.getAllByText('正在查询…')).toHaveLength(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    fireEvent.change(box, { target: { value: '再看一眼' } })
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled()
  })

  it('restores the stop entry from turn_running when the page is reopened', async () => {
    const api = createConsoleApi({
      messages: [],
      orchestrator: { session_id: 'main', status: 'active', turn_running: true, pending_inbox: 0, archived_count: 0 },
      onInterrupt: () => {
        api.state.orchestrator = { ...api.state.orchestrator, turn_running: false }
        return { session_id: 'main', interrupted: true, turn_running: false }
      },
    })
    renderConsole(api)

    expect(await screen.findByText('总控正在处理收到的消息')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止本轮' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '停止本轮' }))

    await waitFor(() => expect(api.interruptCalls()).toHaveLength(1))
    await waitFor(() => expect(screen.queryByText('总控正在处理收到的消息')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })

  it('ends the turn without an error when another client interrupts it', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        held = heldStream([runStart(), delta('正在查询…')])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('查一下队列')
    expect(await screen.findByText('总控正在处理…')).toBeInTheDocument()

    act(() => {
      held?.push([
        event('interrupted', { run_id: 'r1' }),
        runEnd('r1', 'interrupted'),
      ])
    })

    await waitFor(() => expect(screen.queryByText('总控正在处理…')).not.toBeInTheDocument())
    expect(screen.getByText('本轮已停止')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(api.sentMessages()).toHaveLength(1)

    api.state.messages = [
      userMessage(301, '查一下队列', null),
      assistantMessage(302, '正在查询…'),
      assistantMessage(303, '本轮已被你中断。'),
    ]
    await act(async () => { held?.close() })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(await screen.findByText('本轮已被你中断。')).toBeInTheDocument()
    expect(screen.getAllByText('正在查询…')).toHaveLength(1)
  })

  it('shows the media cards a live turn produced, then the same cards rebuilt from history', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const sha = 'd'.repeat(64)
    const imageCard = {
      type: 'image',
      screenshot_id: 'shot-media-1',
      sha256: sha,
      url: `/api/images/${sha}/full`,
      thumb_url: `/api/images/${sha}/thumb`,
      alt: '当前的游戏画面',
      caption: '现在长这样',
      width: 1920,
      height: 1080,
    }
    const chartCard = {
      type: 'chart',
      caption: '最近的掉落',
      chart: {
        chart_type: 'bar',
        title: '1-7 掉落',
        categories: ['固源岩'],
        series: [{ name: '数量', values: [12] }],
        stacked: false,
        x_label: '物品',
        y_label: '个数',
        unit: '个',
        source: 'get_drop_stats(stage=1-7)',
      },
    }
    const historyMessages: Json[] = [
      userMessage(201, '给我看看画面和掉落'),
      makeV3Message({
        id: 202,
        role: 'assistant',
        content: null,
        tool_calls: [
          { id: 'call-shot', function: { name: 'send_screenshot', arguments: '{}' } },
          { id: 'call-chart', function: { name: 'send_chart', arguments: '{"chart_type":"bar"}' } },
        ],
        run_id: 'r1',
      }),
      makeV3Message({ id: 203, role: 'tool', tool_name: 'send_screenshot', tool_call_id: 'call-shot', audit_id: 41, content: JSON.stringify({ result: { media: [imageCard] } }), run_id: 'r1' }),
      makeV3Message({ id: 204, role: 'tool', tool_name: 'send_chart', tool_call_id: 'call-chart', audit_id: 42, content: JSON.stringify({ result: { media: [chartCard] } }), run_id: 'r1' }),
      assistantMessage(205, '这是当前的画面与掉落统计。'),
    ]
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-shot', name: 'send_screenshot', arguments: {} }),
          event('tool_result', { run_id: 'r1', tool_call_id: 'call-shot', name: 'send_screenshot', audit_id: 41, result: { media: [imageCard] } }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('给我看看画面和掉落')

    expect(await screen.findByRole('img', { name: '当前的游戏画面' })).toBeInTheDocument()
    const liveTurn = screen.getByRole('region', { name: '本轮消息' })
    expect(within(liveTurn).getAllByTestId('media-cards')).toHaveLength(1)
    expect(within(liveTurn).getByText('发送截图')).toBeInTheDocument()
    expect(liveTurn.querySelectorAll('details[open]')).toHaveLength(0)

    await act(async () => {
      held?.push([
        event('tool_start', { run_id: 'r1', tool_call_id: 'call-chart', name: 'send_chart', arguments: { chart_type: 'bar' } }),
        event('tool_result', { run_id: 'r1', tool_call_id: 'call-chart', name: 'send_chart', audit_id: 42, result: { media: [chartCard] } }),
        delta('这是当前的画面与掉落统计。'),
      ])
    })
    expect(within(liveTurn).getAllByTestId('media-cards')).toHaveLength(2)
    expect(within(liveTurn).getByText('1-7 掉落')).toBeInTheDocument()

    api.state.messages = historyMessages
    await act(async () => {
      held?.push([runEnd()])
      held?.close()
    })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByTestId('media-cards')).toHaveLength(2)
    expect(screen.getAllByRole('img', { name: '当前的游戏画面' })).toHaveLength(1)
    expect(screen.getByText('1-7 掉落')).toBeInTheDocument()
    expect(screen.getAllByText('这是当前的画面与掉落统计。')).toHaveLength(1)
  })

  it('marks a run joined midway and shows its later output once', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        held = heldStream([delta('补上后面的输出。', 'r-late')])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('检查一下')

    expect(await screen.findByText('补上后面的输出。')).toBeInTheDocument()
    expect(screen.getByText('接着处理之前的消息')).toBeInTheDocument()

    api.state.messages = [
      userMessage(401, '检查一下', 'r-late'),
      assistantMessage(402, '补上后面的输出。', 'r-late'),
    ]
    act(() => {
      held?.push([runEnd('r-late')])
      held?.close()
    })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(screen.getAllByText('补上后面的输出。')).toHaveLength(1)
    expect(screen.getAllByText('检查一下')).toHaveLength(1)
    expect(screen.queryByText('接着处理之前的消息')).not.toBeInTheDocument()
  })

  it('queues a second message while a reply is still streaming', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      stream: (body) => {
        if (body.content === '第二条') return new Response(null, { status: 200 })
        held = heldStream([runStart(), delta('处理中')])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('第一条')
    expect(await screen.findByText('处理中')).toBeInTheDocument()

    fireEvent.change(await composerBox(), { target: { value: '第二条' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(api.sentMessages()).toHaveLength(2))
    expect(await screen.findByText('排队中')).toBeInTheDocument()
    expect(screen.getAllByText('第二条')).toHaveLength(1)

    api.state.messages = [
      userMessage(501, '第一条'),
      assistantMessage(502, '处理中'),
      userMessage(503, '第二条'),
      assistantMessage(504, '处理完了。'),
    ]
    act(() => {
      held?.push([runEnd()])
      held?.close()
    })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByText('排队中')).not.toBeInTheDocument())
    expect(screen.getAllByText('第一条')).toHaveLength(1)
    expect(screen.getAllByText('第二条')).toHaveLength(1)
    expect(screen.getAllByText('处理完了。')).toHaveLength(1)
  })

  it('shows the ticket created by the orchestrator inside the live turn', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      stream: () => {
        held = heldStream([
          runStart(),
          event('ticket_created', { run_id: 'r1', ticket_id: 't1', title: '日常' }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('帮我建个工单')

    const link = await screen.findByRole('link', { name: /已建工单「日常」/ })
    expect(link).toHaveAttribute('href', '/tickets/t1')
    await act(async () => { held?.close() })
  })

  it('lists confirmations, questions and waiting tickets with their origin', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-1',
      origin: { ticket_id: 't-confirm', ticket_title: '确认来源', agent: 'general', run_id: 'r1' },
    })
    const question = makeQuestion({
      question_id: 'q-1',
      origin: { ticket_id: 't-question', ticket_title: '提问来源', agent: 'general', run_id: 'r1' },
    })
    const api = createConsoleApi({
      messages: [],
      confirmations: [confirmation],
      confirmationDetails: { 'confirm-1': confirmation },
      questions: [question],
      attentionTickets: [
        makeTicket({ id: 't-waiting', title: '等体力的日常', status: 'waiting', updated_at: '2026-09-27T10:03:00Z' }),
        makeTicket({ id: 't-blocked', title: '卡住的剿灭', status: 'blocked', updated_at: '2026-09-27T10:02:00Z' }),
      ],
    })
    renderConsole(api)

    const session = await screen.findByRole('region', { name: '总控会话' })
    expect(await within(session).findByRole('heading', { name: '待你处理' })).toBeInTheDocument()
    expect(await within(session).findByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()
    expect(await within(session).findByRole('heading', { name: '需要你选择' })).toBeInTheDocument()
    expect(within(session).getByText('等体力的日常')).toBeInTheDocument()
    expect(within(session).getByText('卡住的剿灭')).toBeInTheDocument()
    expect(within(session).getByText('来自工单「确认来源」· 通用 Worker')).toBeInTheDocument()
    expect(within(session).getByText('来自工单「提问来源」· 通用 Worker')).toBeInTheDocument()
    // 「待你处理」只在主会话里，桌面右栏不再重复。
    const aside = screen.getByRole('complementary', { name: '指挥台侧栏' })
    expect(within(aside).queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument()

    const kinds = Array.from(document.querySelectorAll('[data-attention-kind]'))
      .map((node) => node.getAttribute('data-attention-kind'))
    expect(kinds).toEqual(['confirmation', 'question', 'ticket', 'ticket'])
    const ids = Array.from(document.querySelectorAll('[data-attention-id]'))
      .map((node) => node.getAttribute('data-attention-id'))
    expect(ids).toEqual(['confirm-1', 'q-1', 't-blocked', 't-waiting'])
  })

  it('reviews pending memories in place (M79)', async () => {
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

    expect(await screen.findByText('2 条记忆待审核')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去记忆页' })).toHaveAttribute('href', '/more/memories')
    fireEvent.click(screen.getByRole('button', { name: '展开审核' }))
    expect(screen.getByText('刷图不要用源石恢复理智')).toBeInTheDocument()
    expect(screen.queryByText('已生效的记忆')).not.toBeInTheDocument()

    const first = document.querySelector('[data-memory-id="m1"]') as HTMLElement
    fireEvent.click(within(first).getByRole('button', { name: '批准' }))
    await waitFor(() => expect(api.memoryReviews()).toHaveLength(1))
    expect(api.memoryReviews()[0]?.body).toEqual({ approved: true })
    await waitFor(() => expect(screen.queryByText('刷图不要用源石恢复理智')).not.toBeInTheDocument())

    const second = document.querySelector('[data-memory-id="m2"]') as HTMLElement
    fireEvent.click(within(second).getByRole('button', { name: '拒绝' }))
    await waitFor(() => expect(api.memoryReviews()).toHaveLength(2))
    expect(api.memoryReviews()[1]?.body).toEqual({ approved: false })
    await waitFor(() => expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument())
  })

  it('shows the memory review error without breaking the other rows', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      memories: [
        makeMemory({ id: 'm1', content: '刷图不要用源石恢复理智' }),
        makeMemory({ id: 'm2', content: 'ADB 掉线先重连一次' }),
      ],
      overrides: [({ method, path }) => (
        method === 'POST' && path === '/api/agent/memories/m1/review'
          ? json({ error: { code: 'MEMORY_NOT_PROPOSED', message: '这条记忆已经不在待审核状态，刷新后再试。' } }, 409)
          : undefined
      )],
    })
    renderConsole(api)

    fireEvent.click(await screen.findByRole('button', { name: '展开审核' }))
    const first = document.querySelector('[data-memory-id="m1"]') as HTMLElement
    fireEvent.click(within(first).getByRole('button', { name: '批准' }))
    expect(await screen.findByText('这条记忆已经不在待审核状态，刷新后再试。')).toBeInTheDocument()

    // 其它行不受影响，仍可处理。
    const second = document.querySelector('[data-memory-id="m2"]') as HTMLElement
    expect(within(second).getByRole('button', { name: '拒绝' })).toBeEnabled()
  })

  it('lists unresolved critical watchdog alerts and resolves them (M83)', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      watchdogAlerts: [
        makeWatchdogAlert({ id: 7, message: '流水线 3 分钟没有回调' }),
        // 警告与已解除的告警不进待你处理。
        makeWatchdogAlert({ id: 8, severity: 'warn', message: '只是警告' }),
        makeWatchdogAlert({ id: 9, message: '已经处理过', resolved_at: '2026-11-05T05:00:00Z' }),
      ],
    })
    renderConsole(api)

    expect(await screen.findByText('流水线 3 分钟没有回调')).toBeInTheDocument()
    expect(screen.getByText(/对象：设备「pipeline-1」 · 时间：/)).toBeInTheDocument()
    expect(screen.queryByText('只是警告')).not.toBeInTheDocument()
    expect(screen.queryByText('已经处理过')).not.toBeInTheDocument()
    // 待你处理只读 critical 列表。
    expect(api.watchdogAlertQueries().every((request) => request.query.get('severity') === 'critical'))
      .toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '标记已处理' }))
    await waitFor(() => expect(api.watchdogAlertResolves()).toHaveLength(1))
    await waitFor(() => expect(screen.queryByText('流水线 3 分钟没有回调')).not.toBeInTheDocument())
  })

  it('renders attention cards in the main session on phones', async () => {
    const confirmation = makeConfirmation({ confirmation_id: 'confirm-1' })
    const question = makeQuestion({ question_id: 'q-1' })
    const api = createConsoleApi({
      messages: [],
      confirmations: [confirmation],
      confirmationDetails: { 'confirm-1': confirmation },
      questions: [question],
    })
    renderConsole(api)

    expect(await screen.findByRole('heading', { name: '待你处理' })).toBeInTheDocument()
    // 手机端不再有汇总行与底部面板：待办卡直接在主会话里。
    expect(screen.queryByRole('button', { name: '2 件事等你处理' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '待你处理' })).not.toBeInTheDocument()

    fireEvent.click(await screen.findByRole('button', { name: '批准并继续' }))
    await waitFor(() => expect(api.confirmationPosts()).toHaveLength(1))
    fireEvent.click(await screen.findByRole('radio', { name: /理智药/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交' }))
    await waitFor(() => expect(api.questionPosts()).toHaveLength(1))

    await waitFor(() => expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument())
  })

  it('replies to a waiting ticket in place', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [makeTicket({
        id: 't-waiting',
        title: '每日免费单抽没领到',
        status: 'waiting',
        human_action: { type: 'question', prompt: '请确认是否批准验证流水线？', ref_id: null },
      })],
    })
    renderConsole(api)

    expect(await screen.findByText('请确认是否批准验证流水线？')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '回复' }))
    fireEvent.change(screen.getByLabelText('回复工单'), { target: { value: '批准，明早 04:00 跑' } })
    fireEvent.click(screen.getByRole('button', { name: '发送回复' }))

    await waitFor(() => expect(api.ticketMessagePosts()).toHaveLength(1))
    expect(api.ticketMessagePosts()[0]?.path).toBe('/api/agent/tickets/t-waiting/messages')
    expect(api.ticketMessagePosts()[0]?.body).toEqual({ content: '批准，明早 04:00 跑' })
    expect(await screen.findByText('已发给工单')).toBeInTheDocument()
    expect(screen.queryByLabelText('回复工单')).not.toBeInTheDocument()
  })

  it('keeps the ticket reply failure visible and retryable', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [makeTicket({
        id: 't-waiting',
        title: '每日免费单抽没领到',
        status: 'waiting',
        human_action: { type: 'question', prompt: '请确认是否批准验证流水线？', ref_id: null },
      })],
      overrides: [({ method, path }) => (
        method === 'POST' && path === '/api/agent/tickets/t-waiting/messages'
          ? json({ error: { code: 'SERVICE_UNAVAILABLE', message: '服务暂时不可用' } }, 500)
          : undefined
      )],
    })
    renderConsole(api)

    fireEvent.click(await screen.findByRole('button', { name: '回复' }))
    fireEvent.change(screen.getByLabelText('回复工单'), { target: { value: '批准' } })
    fireEvent.click(screen.getByRole('button', { name: '发送回复' }))

    expect(await screen.findByText('发送消息失败：服务暂时不可用')).toBeInTheDocument()
    const draft = screen.getByLabelText('回复工单') as HTMLTextAreaElement
    expect(draft.value).toBe('批准')
    fireEvent.click(screen.getByRole('button', { name: '发送回复' }))
    await waitFor(() => expect(api.ticketMessagePosts()).toHaveLength(2))
  })

  it('reports a ticket that has ended when replying', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [makeTicket({
        id: 't-waiting',
        title: '通关活动 EX 关',
        status: 'waiting',
        human_action: { type: 'question', prompt: 'EX 段开放了吗？', ref_id: null },
      })],
      overrides: [({ method, path }) => (
        method === 'POST' && path === '/api/agent/tickets/t-waiting/messages'
          ? json({ error: { code: 'TICKET_ENDED', message: '工单已结束，不能再发消息' } }, 409)
          : undefined
      )],
    })
    renderConsole(api)

    fireEvent.click(await screen.findByRole('button', { name: '回复' }))
    fireEvent.change(screen.getByLabelText('回复工单'), { target: { value: '已开放' } })
    fireEvent.click(screen.getByRole('button', { name: '发送回复' }))

    expect(await screen.findByText('发送消息失败：工单已结束，不能再发消息')).toBeInTheDocument()
  })

  it('hides reply for a ticket without human action', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [makeTicket({ id: 't-blocked', title: '更新活动资源', status: 'blocked', summary: '卡在缺少工具' })],
    })
    renderConsole(api)

    expect(await screen.findByText('卡在缺少工具')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /更新活动资源/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '回复' })).not.toBeInTheDocument()
  })

  it('shows a retryable error when attention cannot be read', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      overrides: [({ method, path }) => (
        method === 'GET' && path === '/api/agent/memories'
          ? json({ error: { code: 'SERVICE_UNAVAILABLE', message: '服务暂时不可用' } }, 500)
          : undefined
      )],
    })
    renderConsole(api)

    expect(await screen.findByText('读取待你处理的事项失败：服务暂时不可用')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('hides the attention section when nothing is pending', async () => {
    const api = createConsoleApi({ messages: [] })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument()
  })

  it('shows each pending card once when the live turn already renders it', async () => {
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-1',
      action: 'consume_resource',
      risk_level: 'consume',
      reason: '此操作会使用一次理智药。',
    })
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      messages: [],
      confirmations: [confirmation],
      confirmationDetails: { 'confirm-1': confirmation },
      stream: () => {
        held = heldStream([
          runStart(),
          event('tool_start', { run_id: 'r1', tool_call_id: 'call-1', name: 'submit_pipeline', arguments: {} }),
          event('confirmation_required', {
            run_id: 'r1',
            tool_call_id: 'call-1',
            name: 'submit_pipeline',
            confirmation_id: 'confirm-1',
            expires_at: confirmation.expires_at,
          }),
        ])
        return held.response
      },
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    await sendText('刷一次 1-7')

    // 实时副本里挂着的确认卡已经就地展示；同一张不再出现在「待你处理」里。
    await waitFor(() => expect(screen.queryByRole('heading', { name: '待你处理' })).not.toBeInTheDocument())
    expect(screen.getAllByRole('heading', { name: '需要确认此操作' })).toHaveLength(1)
    await act(async () => { held?.close() })
  })

  it('offers to write in the composer when there are no active tickets', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({ messages: [] })
    renderConsole(api)

    expect(await screen.findByText('还没有进行中的工单')).toBeInTheDocument()
    expect(screen.getByText('在总控会话里说出要做的事')).toBeInTheDocument()
    const compose = screen.getByRole('button', { name: '写下要做的事' })
    fireEvent.click(compose)
    expect(document.activeElement).toBe(await composerBox())
  })

  it('lists open tickets and marks the one holding the device', async () => {
    stubMedia((query) => query === '(min-width: 1024px)')
    const api = createConsoleApi({
      messages: [],
      activeTickets: [
        makeTicket({ id: 't1', title: '日常', status: 'open' }),
        makeTicket({ id: 't2', title: '剿灭', status: 'open' }),
      ],
      lease: {
        held: true,
        waiters: 0,
        lease: {
          ticket_id: 't1',
          holder_agent: 'general',
          purpose: '刷理智',
          expires_at: '2026-09-27T10:10:00Z',
        },
      },
    })
    renderConsole(api)

    expect(await screen.findByText('进行中的工单')).toBeInTheDocument()
    expect(await screen.findByText('日常')).toBeInTheDocument()
    expect(screen.getByText('剿灭')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()

    const ticketLink = screen.getByText('日常').closest('a')!
    expect(within(ticketLink).getByText('占用设备')).toBeInTheDocument()
    expect(screen.getAllByText('占用设备')).toHaveLength(1)
  })

  it('runs /stop, /tickets and /help locally without sending a message', async () => {
    const api = createConsoleApi({ messages: [] })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await composerBox()

    fireEvent.change(box, { target: { value: '/stop' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText('现在没有在跑的一轮')).toBeInTheDocument()
    expect(api.sentMessages()).toHaveLength(0)

    fireEvent.change(box, { target: { value: '/help' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByRole('note', { name: '可用命令' })).toBeInTheDocument()
    expect(screen.getByText('/stop')).toBeInTheDocument()
    expect(api.sentMessages()).toHaveLength(0)

    fireEvent.change(box, { target: { value: '/tickets' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/tickets'))
    expect(api.sentMessages()).toHaveLength(0)
  })

  it('sends selected @ mentions with the message', async () => {
    const api = createConsoleApi({
      messages: [],
      activeTickets: [
        makeTicket({ id: 't1', title: '日常', status: 'open' }),
        makeTicket({ id: 't2', title: '剿灭', status: 'open' }),
      ],
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    const box = await composerBox()
    fireEvent.change(box, { target: { value: '看看 @日常' } })

    const first = await screen.findByRole('listbox', { name: '提及' })
    fireEvent.click(within(first).getByRole('option', { name: /日常/ }))
    expect(box).toHaveValue('看看 @日常 ')

    fireEvent.change(box, { target: { value: '看看 @日常 再 @剿灭' } })
    const second = await screen.findByRole('listbox', { name: '提及' })
    fireEvent.click(within(second).getByRole('option', { name: /剿灭/ }))

    fireEvent.change(box, { target: { value: '看看 @剿灭' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(api.sentMessages()).toHaveLength(1))
    expect(api.sentMessages()[0]!.body).toEqual({
      content: '看看 @剿灭',
      mentions: [{ type: 'ticket', id: 't2', label: '剿灭' }],
    })
  })

  it('resets the orchestrator session after confirmation', async () => {
    let conflict = false
    const api = createConsoleApi({
      messages: [],
      onReset: () => (conflict
        ? json({ error: { code: 'RUN_ALREADY_ACTIVE', message: '总控正在处理消息' } }, 409)
        : json({ session_id: 'main-2', status: 'active', turn_running: false, pending_inbox: 0, archived_count: 1 })),
    })
    renderConsole(api)

    expect(await screen.findByText('你可以这样开始')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '指挥台操作' }))
    const menu = await screen.findByRole('dialog', { name: '指挥台操作' })
    expect(within(menu).queryByRole('button', { name: '旧会话' })).not.toBeInTheDocument()
    expect(within(menu).getByRole('button', { name: '运行状态' })).toBeInTheDocument()
    fireEvent.click(within(menu).getByRole('button', { name: '重新开始会话' }))

    const dialog = await screen.findByRole('alertdialog', { name: '重新开始总控会话？' })
    expect(within(dialog).getByText('当前会话会归档；进行中的工单不受影响。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '重新开始' }))

    await waitFor(() => expect(api.resetCalls()).toHaveLength(1))
    expect(await screen.findByText('已重新开始总控会话')).toBeInTheDocument()

    conflict = true
    fireEvent.click(screen.getByRole('button', { name: '指挥台操作' }))
    const menuAgain = await screen.findByRole('dialog', { name: '指挥台操作' })
    fireEvent.click(within(menuAgain).getByRole('button', { name: '重新开始会话' }))
    const dialogAgain = await screen.findByRole('alertdialog', { name: '重新开始总控会话？' })
    fireEvent.click(within(dialogAgain).getByRole('button', { name: '重新开始' }))

    expect(await screen.findByText('重新开始总控会话失败：总控正在处理消息，先停止本轮')).toBeInTheDocument()
  })

})
