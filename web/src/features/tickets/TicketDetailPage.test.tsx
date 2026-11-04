import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { useLocation, useNavigate } from 'react-router'
import {
  createConsoleApi,
  event,
  heldStream,
  json,
  makeConfirmation,
  makeRun,
  makeTicket,
  makeTicketDetail,
  makeV3Message,
  renderAt,
  stubMedia,
  type Json,
} from '@/features/console/test-utils'
import TicketDetailPage from './TicketDetailPage'

const DESKTOP = '(min-width: 1024px)'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

/** 模拟浏览器后退/前进之外的用户切工单动作（M59 工单版）。 */
function TicketSwitchProbe() {
  const navigate = useNavigate()
  return <button type="button" onClick={() => void navigate('/tickets/t2')}>切换工单</button>
}

function renderDetail(path = '/tickets/t1') {
  return renderAt(path, [{
    path: '/tickets/:ticketId',
    element: <><TicketDetailPage /><LocationProbe /><TicketSwitchProbe /></>,
  }])
}

function baseTicket(overrides: Json = {}): Json {
  return makeTicket({ id: 't1', title: '日常', kind: 'general', origin: 'user', ...overrides })
}

function baseDetail(overrides: Json = {}): Json {
  return makeTicketDetail({ ticket: baseTicket(), worker_session_id: 'w1', ...overrides })
}

function baseMandate(overrides: Json = {}): Json {
  return {
    scope: '刷 1-7 到没体力',
    strength: 'explicit',
    source: 'user',
    sender: null,
    risk_ceiling: 'consume',
    created_at: '2026-09-27T10:00:00Z',
    ...overrides,
  }
}

function report(overrides: Json = {}): Json {
  return {
    id: 1,
    ticket_id: 't1',
    run_id: null,
    status: 'progress',
    summary: '进展',
    human_action: null,
    details: null,
    forwarded: false,
    created_at: '2026-09-27T10:00:00Z',
    ...overrides,
  }
}

function workerMessage(id: number, content: string | null, overrides: Json = {}): Json {
  return makeV3Message({ id, session_id: 'w1', seq: id, role: 'assistant', content, run_id: 'r1', ...overrides })
}

describe('M72 工单页', () => {
  beforeEach(() => {
    document.documentElement.scrollTop = 0
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows the summary, mandate and human action of a waiting ticket', async () => {
    stubMedia((query) => query === DESKTOP)
    const ticket = baseTicket({
      status: 'waiting',
      summary: '需要确认体力药',
      last_report_status: 'waiting',
      human_action: { type: 'confirmation', prompt: '可以用一次理智药吗？', ref_id: 'confirm-1' },
    })
    const confirmation = makeConfirmation({
      confirmation_id: 'confirm-1',
      origin: { ticket_id: 't1', ticket_title: '日常', agent: 'general', run_id: 'r1' },
    })
    createConsoleApi({
      tickets: [ticket],
      ticketDetails: { t1: baseDetail({ ticket, mandate: baseMandate() }) },
      ticketMessages: { t1: [] },
      confirmations: [confirmation],
      confirmationDetails: { 'confirm-1': confirmation },
    })
    renderDetail()

    expect(await screen.findByRole('heading', { level: 1, name: '日常' })).toBeInTheDocument()
    expect(screen.getAllByText('等你处理').length).toBeGreaterThan(0)
    expect(screen.getByText('需要确认体力药')).toBeInTheDocument()
    expect(screen.getByText(/可以用一次理智药吗/)).toBeInTheDocument()
    expect(screen.getAllByText('你发起').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '需要确认此操作' })).toBeInTheDocument()

    // 委托默认折叠，内容仍挂在同一个 details 里。
    const mandate = screen.getByText('委托').closest('details')!
    expect(mandate.open).toBe(false)
    expect(within(mandate).getByText('刷 1-7 到没体力')).toBeInTheDocument()
    expect(within(mandate).getByText('最高到消耗类操作')).toBeInTheDocument()
    expect(within(mandate).getAllByText('你明确要求的')).toHaveLength(2)
  })

  it('renders worker messages with subagent runs nested under delegate calls', async () => {
    const specTitles = [
      { name: 'general', title: '通用 Worker', kind: 'worker' },
      { name: 'scout', title: '情报员', kind: 'subagent' },
    ]
    createConsoleApi({
      specs: specTitles,
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'open' }) }) },
      ticketMessages: {
        t1: [
          makeV3Message({ id: 1, session_id: 'w1', seq: 1, role: 'user', content: '查一下 1-7 掉落', run_id: 'r1' }),
          makeV3Message({
            id: 2,
            session_id: 'w1',
            seq: 2,
            role: 'assistant',
            content: null,
            tool_calls: [{ id: 'call-d', type: 'function', function: { name: 'delegate', arguments: '{"agent":"scout","task":"查掉落"}' } }],
            run_id: 'r1',
          }),
          makeV3Message({
            id: 3,
            session_id: 'w1',
            seq: 3,
            role: 'tool',
            tool_name: 'delegate',
            tool_call_id: 'call-d',
            content: JSON.stringify({ status: 'ok', result: { run_id: 'r-sub', agent: 'scout' } }),
            run_id: 'r1',
          }),
          makeV3Message({ id: 4, session_id: 's1', seq: 1, role: 'assistant', content: '1-7 掉落固源岩。', run_id: 'r-sub' }),
          makeV3Message({ id: 5, session_id: 'w1', seq: 4, role: 'assistant', content: '查完了。', run_id: 'r1' }),
        ],
      },
    })
    renderDetail()

    expect(await screen.findByText('查一下 1-7 掉落')).toBeInTheDocument()
    expect(screen.getByText('子代理过程（情报员，1 条消息）')).toBeInTheDocument()
    expect(screen.getByText('1-7 掉落固源岩。')).toBeInTheDocument()
    expect(screen.getByText('查完了。')).toBeInTheDocument()
  })

  it('streams a worker reply after sending a message in the ticket', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    const api = createConsoleApi({
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'open' }) }) },
      ticketMessages: { t1: [workerMessage(1, '开始吧')] },
      stream: () => {
        held = heldStream([
          event('run_start', { run_id: 'r1', parent_run_id: null, agent: 'general', kind: 'worker_turn', ticket_id: 't1' }),
          event('report', { run_id: 'r1', ticket_id: 't1', report_id: 9, status: 'progress', summary: '正在刷 1-7', human_action: null }),
          event('text_delta', { run_id: 'r1', text: '开始刷了。' }),
        ])
        return held.response
      },
    })
    renderDetail()

    expect(await screen.findByText('开始吧')).toBeInTheDocument()
    const box = screen.getByRole('textbox', { name: '发送给 Worker' })
    fireEvent.change(box, { target: { value: '刷 1-7' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(api.requests.filter(
      (request) => request.method === 'POST' && request.path === '/api/agent/tickets/t1/messages',
    )).toHaveLength(1))
    expect(api.requests.find(
      (request) => request.method === 'POST' && request.path === '/api/agent/tickets/t1/messages',
    )!.body).toEqual({ content: '刷 1-7', mentions: [] })

    expect(await screen.findByText('开始刷了。')).toBeInTheDocument()
    expect(screen.getByText('正在刷 1-7')).toBeInTheDocument()
    expect(screen.getByText('进展')).toBeInTheDocument()

    api.state.ticketMessages.t1 = [
      makeV3Message({ id: 1, session_id: 'w1', seq: 1, role: 'user', content: '开始吧', run_id: 'r1' }),
      makeV3Message({ id: 2, session_id: 'w1', seq: 2, role: 'user', content: '刷 1-7', run_id: 'r2' }),
      makeV3Message({ id: 3, session_id: 'w1', seq: 3, role: 'assistant', content: '刷完了。', run_id: 'r2' }),
    ]
    act(() => {
      held?.push([
        event('text_delta', { run_id: 'r1', text: '刷完了。' }),
        event('run_end', { run_id: 'r1', finish_reason: 'report_submitted' }),
      ])
      held?.close()
    })

    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
    expect(await screen.findByText('刷完了。')).toBeInTheDocument()
    expect(screen.queryByText('开始刷了。')).not.toBeInTheDocument()
  })

  it('disables the composer when the ticket has ended', async () => {
    createConsoleApi({
      ticketDetails: {
        t1: baseDetail({ ticket: baseTicket({ status: 'done', summary: '做完了' }) }),
      },
      ticketMessages: { t1: [workerMessage(1, '收工。')] },
    })
    renderDetail()

    expect(await screen.findByText('工单已结束，不能再发消息')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '在指挥台继续' })).toHaveAttribute('href', '/')

    fireEvent.click(screen.getByRole('button', { name: '工单操作' }))
    const menu = await screen.findByRole('dialog', { name: '工单操作' })
    expect(within(menu).getByRole('button', { name: '查看运行追踪' })).toBeInTheDocument()
    expect(within(menu).queryByRole('button', { name: '取消工单' })).not.toBeInTheDocument()
    expect(within(menu).queryByRole('button', { name: '停止本轮' })).not.toBeInTheDocument()
  })

  it('shows the report timeline newest first', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'open' }) }) },
      ticketMessages: { t1: [] },
      reports: {
        t1: [
          report({ id: 2, status: 'progress', summary: '最新的进展', details: { pipeline_id: 'p1' }, created_at: '2026-09-27T10:05:00Z' }),
          report({ id: 1, status: 'ready', summary: '更早的汇报', created_at: '2026-09-27T10:00:00Z' }),
        ],
      },
    })
    renderDetail()

    const list = await screen.findByRole('list', { name: '汇报时间线' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(items[0]!).getByText('最新的进展')).toBeInTheDocument()
    expect(within(items[0]!).getByText('进展')).toBeInTheDocument()
    expect(within(items[1]!).getByText('更早的汇报')).toBeInTheDocument()
    expect(within(items[1]!).getByText('已完成')).toBeInTheDocument()
    expect(within(items[0]!).getByRole('link', { name: '查看流水线' })).toHaveAttribute('href', '/tasks/history/p1')
    expect(within(items[1]!).queryByRole('link', { name: '查看流水线' })).not.toBeInTheDocument()
  })

  it('expands the activity tree and links each run to its trace', async () => {
    stubMedia((query) => query === DESKTOP)
    const rootRun = makeRun({
      run_id: 'r1',
      agent_name: 'general',
      kind: 'worker_turn',
      status: 'succeeded',
      finish_reason: 'stop',
      steps: 3,
      prompt_tokens: 20,
      cached_tokens: 4,
      completion_tokens: 12,
      cost_micros: 12_345,
      currency: 'CNY',
      started_at: '2026-09-27T10:00:00Z',
      finished_at: '2026-09-27T10:01:05Z',
    })
    const childRun = makeRun({
      run_id: 'r2',
      agent_name: 'scout',
      kind: 'subagent',
      status: 'running',
      finish_reason: null,
      started_at: '2026-09-27T10:00:30Z',
      finished_at: null,
    })
    createConsoleApi({
      specs: [
        { name: 'general', title: '通用 Worker', kind: 'worker' },
        { name: 'scout', title: '情报员', kind: 'subagent' },
      ],
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'open' }) }) },
      ticketMessages: { t1: [] },
      runs: { t1: [rootRun] },
      runDetails: { r1: { ...rootRun, children: [childRun] } },
    })
    renderDetail()

    expect(await screen.findByText('通用 Worker · Worker 一轮')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.getByText('正常结束')).toBeInTheDocument()
    expect(screen.getByText(/3 步 · 输入 20（缓存 4）\/ 输出 12 token · ¥0\.0123 · 1 分 05 秒/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: '查看追踪' }).map((link) => link.getAttribute('href')))
      .toContain('/more/runs/r1')

    const expand = screen.getByRole('button', { name: '展开「通用 Worker · Worker 一轮」的运行' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(expand)

    expect(await screen.findByText('情报员 · 子代理')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收起「通用 Worker · Worker 一轮」的运行' }))
      .toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByRole('link', { name: '查看追踪' }).map((link) => link.getAttribute('href')))
      .toContain('/more/runs/r2')
  })

  it('shows the lease held by this ticket and releases it after confirmation', async () => {
    stubMedia((query) => query === DESKTOP)
    const expires = new Date(Date.now() + 5 * 60_000).toISOString()
    const api = createConsoleApi({
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'open' }) }) },
      ticketMessages: { t1: [] },
      lease: {
        held: true,
        waiters: 0,
        lease: {
          ticket_id: 't1',
          holder_agent: 'general',
          purpose: '刷理智',
          expires_at: expires,
        },
      },
    })
    renderDetail()

    expect(await screen.findByText(/正在使用设备 · 刷理智 · 剩余 \d{2}:\d{2}/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '释放设备' }))
    const dialog = await screen.findByRole('alertdialog', { name: '释放设备？' })
    expect(within(dialog).getByText('持有设备的工单下次操作设备时会收到「设备已被接管」。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '释放设备' }))

    await waitFor(() => expect(api.requests.filter(
      (request) => request.method === 'DELETE' && request.path === '/api/agent/device-lease',
    )).toHaveLength(1))
    expect(await screen.findByText('已释放设备')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/正在使用设备/)).not.toBeInTheDocument())
  })

  it('cancels the ticket after confirmation', async () => {
    stubMedia((query) => query === DESKTOP)
    let conflict = false
    const api = createConsoleApi({
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'waiting' }) }) },
      ticketMessages: { t1: [] },
      overrides: [({ method, path }) => (
        conflict && method === 'POST' && path === '/api/agent/tickets/t1/cancel'
          ? json({ error: { code: 'TICKET_ENDED', message: '工单已结束' } }, 409)
          : undefined
      )],
    })
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: '工单操作' }))
    let menu = await screen.findByRole('dialog', { name: '工单操作' })
    fireEvent.click(within(menu).getByRole('button', { name: '取消工单' }))
    let dialog = await screen.findByRole('alertdialog', { name: '取消工单「日常」？' })
    expect(within(dialog).getByText('Worker 会停下并释放设备，已提交的流水线不受影响。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '取消工单' }))

    await waitFor(() => expect(api.requests.filter(
      (request) => request.method === 'POST' && request.path === '/api/agent/tickets/t1/cancel',
    )).toHaveLength(1))
    expect(await screen.findByText('已取消工单')).toBeInTheDocument()

    conflict = true
    fireEvent.click(screen.getByRole('button', { name: '工单操作' }))
    menu = await screen.findByRole('dialog', { name: '工单操作' })
    fireEvent.click(within(menu).getByRole('button', { name: '取消工单' }))
    dialog = await screen.findByRole('alertdialog', { name: '取消工单「日常」？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消工单' }))
    expect(await screen.findByText('取消工单失败：工单已结束')).toBeInTheDocument()
  })

  it('drops the live copy of another ticket after navigation', async () => {
    let held: ReturnType<typeof heldStream> | null = null
    createConsoleApi({
      ticketDetails: {
        t1: baseDetail({ ticket: baseTicket({ status: 'open' }) }),
        t2: baseDetail({
          ticket: makeTicket({ id: 't2', title: '剿灭', kind: 'general', status: 'open' }),
          worker_session_id: 'w2',
        }),
      },
      ticketMessages: {
        t1: [workerMessage(1, '开始吧')],
        t2: [makeV3Message({ id: 7, session_id: 'w2', seq: 1, role: 'assistant', content: '另一个工单的回复', run_id: 'r2' })],
      },
      stream: () => {
        held = heldStream([
          event('run_start', { run_id: 'r1', parent_run_id: null, agent: 'general', kind: 'worker_turn', ticket_id: 't1' }),
          event('text_delta', { run_id: 'r1', text: '先看看设备。' }),
        ])
        return held.response
      },
    })
    renderDetail()

    expect(await screen.findByText('开始吧')).toBeInTheDocument()
    const box = screen.getByRole('textbox', { name: '发送给 Worker' })
    fireEvent.change(box, { target: { value: '检查一下' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText('先看看设备。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '切换工单' }))
    expect(await screen.findByText('另一个工单的回复')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('region', { name: '本轮消息' })).not.toBeInTheDocument())
  })

  it('shows the worker plan and artifacts in the sidebar', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      ticketDetails: { t1: baseDetail({ ticket: baseTicket({ status: 'waiting' }) }) },
      ticketMessages: { t1: [] },
      plan: {
        id: 'p1',
        session_id: 'w1',
        ticket_id: 't1',
        title: '清日常',
        items: [{ id: 's1', title: '提交剿灭', status: 'in_progress', note: null }],
        status: 'awaiting_approval',
        approved_by: null,
        approved_at: null,
      },
      artifacts: [{
        id: 'a1',
        ticket_id: 't1',
        session_id: 'w1',
        kind: 'note',
        title: '作战笔记',
        mime: 'text/markdown',
        size_bytes: 512,
        storage: 'db',
        pinned: false,
        created_at: '2026-10-01T09:30:00Z',
      }],
    })
    renderDetail()

    const sidebar = await screen.findByRole('complementary', { name: '工单侧栏' })
    expect(await within(sidebar).findByText('清日常')).toBeInTheDocument()
    expect(within(sidebar).getByRole('button', { name: '批准' })).toBeInTheDocument()
    expect(await within(sidebar).findByText('作战笔记')).toBeInTheDocument()
  })
})
