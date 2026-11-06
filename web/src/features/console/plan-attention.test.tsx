import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ConsolePage from './ConsolePage'
import { toTicketView } from './api'
import { useAttentionPlans } from './use-attention-plans'
import {
  createConsoleApi,
  json,
  makePlan,
  makeTicket,
  makeTicketDetail,
  renderAt,
  stubMedia,
  type Json,
} from './test-utils'

const DESKTOP = '(min-width: 1024px)'

function decisionTicket(overrides: Json = {}): Json {
  return makeTicket({
    id: 't1',
    title: '清日常',
    status: 'waiting',
    human_action: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' },
    ...overrides,
  })
}

function plan(overrides: Json = {}): Json {
  return makePlan({
    id: 'p1',
    session_id: 'w1',
    ticket_id: 't1',
    title: '清日常',
    items: [{ id: 'a1', title: '提交剿灭', status: 'in_progress', note: '先看体力' }],
    ...overrides,
  })
}

function workerDetail(ticket: Json = decisionTicket()): Json {
  return makeTicketDetail({ ticket, worker_session_id: 'w1' })
}

function renderConsole() {
  return renderAt('/', [{ path: '/', element: <ConsolePage /> }])
}

function approvalPosts(api: ReturnType<typeof createConsoleApi>) {
  return api.requests.filter(
    (item) => item.method === 'POST' && item.path === '/api/agent/sessions/w1/plan/approval',
  )
}

describe('指挥台计划待批准', () => {
  beforeEach(() => {
    document.documentElement.scrollTop = 0
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows a pending plan in the console and approves it', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
    })
    renderConsole()

    expect(await screen.findByRole('heading', { name: '计划待批准' })).toBeInTheDocument()
    expect(screen.getByText('提交剿灭')).toBeInTheDocument()
    expect(screen.getByText('来自工单「清日常」· 通用 Worker')).toBeInTheDocument()
    expect(screen.queryByText('等你处理')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '批准计划' }))

    await waitFor(() => expect(approvalPosts(api)).toHaveLength(1))
    expect(approvalPosts(api)[0]?.body).toEqual({ approved: true })
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
    })
    expect(await screen.findByText('已批准计划')).toBeInTheDocument()
  })

  it('rejects a plan from the console with a non-empty reason', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '退回计划' }))
    const submit = screen.getByRole('button', { name: '提交退回' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '第二步改到下午' } })
    fireEvent.click(submit)

    await waitFor(() => expect(approvalPosts(api)).toHaveLength(1))
    expect(approvalPosts(api)[0]?.body).toEqual({ approved: false, reason: '第二步改到下午' })
    expect(await screen.findByText('已退回计划')).toBeInTheDocument()
  })

  it('opens the plan card in the phone attention sheet', async () => {
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '1 件事等你处理' }))
    const sheet = await screen.findByRole('dialog', { name: '待你处理' })
    expect(await within(sheet).findByRole('heading', { name: '计划待批准' })).toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('button', { name: '批准计划' }))

    await waitFor(() => expect(approvalPosts(api)).toHaveLength(1))
    expect(approvalPosts(api)[0]?.body).toEqual({ approved: true })
  })

  it('falls back to the ticket card when the plan cannot be read', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: makeTicketDetail({ ticket: decisionTicket(), worker_session_id: null }) },
    })
    renderConsole()

    expect(await screen.findByText('清日常')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
    expect(api.requests.filter((item) => item.path.includes('/plan'))).toHaveLength(0)
  })

  it('hides the card while the plan has already left awaiting approval', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan({ status: 'active', approved_by: 'console', approved_at: '2026-10-01T10:00:00Z' }) },
    })
    renderConsole()

    await waitFor(() => expect(api.requests.some((item) => item.path.endsWith('/plan'))).toBe(true))
    expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
    // 计划已批准但工单还挂着旧的 decision，不出工单卡（等 Worker 汇报刷新）。
    expect(screen.queryByText('等你处理')).not.toBeInTheDocument()
  })

  it('shows an error when the approval request fails', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
      overrides: [({ method, path }) => (
        method === 'POST' && path.endsWith('/plan/approval')
          ? json({ error: { code: 'SERVICE_UNAVAILABLE', message: '计划服务暂不可用' } }, 500)
          : undefined
      )],
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '批准计划' }))

    expect(await screen.findByText('处理计划失败：计划服务暂不可用')).toBeInTheDocument()
    // 失败后计划卡保留，用户可以重试。
    expect(screen.getByRole('heading', { name: '计划待批准' })).toBeInTheDocument()
  })

  it('keeps the plan error visible after the last item disappears', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
      overrides: [({ method, path, state }) => {
        if (method === 'POST' && path.endsWith('/plan/approval')) {
          state.plans.w1 = plan({ status: 'active' })
          return json({ error: { code: 'PLAN_NOT_AWAITING_APPROVAL', message: '计划不在待批准状态' } }, 409)
        }
        return undefined
      }],
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '批准计划' }))

    expect(await screen.findByText('处理计划失败：计划不在待批准状态')).toBeInTheDocument()
    expect(await screen.findByText('还没有要你处理的事')).toBeInTheDocument()
  })

  it('keeps the phone attention sheet open when the plan approval fails', async () => {
    createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
      overrides: [({ method, path, state }) => {
        if (method === 'POST' && path.endsWith('/plan/approval')) {
          state.plans.w1 = plan({ status: 'active' })
          return json({ error: { code: 'PLAN_NOT_AWAITING_APPROVAL', message: '计划不在待批准状态' } }, 409)
        }
        return undefined
      }],
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '1 件事等你处理' }))
    const sheet = await screen.findByRole('dialog', { name: '待你处理' })
    fireEvent.click(await within(sheet).findByRole('button', { name: '批准计划' }))

    expect(await within(sheet).findByText('处理计划失败：计划不在待批准状态')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: '待你处理' })).toBeInTheDocument()
  })

  it('falls back to the ticket card when the plan request fails', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      overrides: [({ method, path }) => (
        method === 'GET' && path.endsWith('/plan')
          ? json({ error: { code: 'PLAN_NOT_FOUND', message: '计划不存在' } }, 404)
          : undefined
      )],
    })
    renderConsole()

    expect(await screen.findByText('清日常')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
  })

  it('falls back to the ticket card when the plan does not match the decision', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan({ id: 'p2' }) },
    })
    renderConsole()

    expect(await screen.findByText('清日常')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
  })

  it('falls back to the ticket card when there is no plan', async () => {
    stubMedia((query) => query === DESKTOP)
    createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: null },
    })
    renderConsole()

    expect(await screen.findByText('清日常')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
  })

  it('lets the user retry after an approval failure', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
      overrides: [({ method, path }) => (
        method === 'POST' && path.endsWith('/plan/approval')
          ? json({ error: { code: 'SERVICE_UNAVAILABLE', message: '计划服务暂不可用' } }, 500)
          : undefined
      )],
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '批准计划' }))
    expect(await screen.findByText('处理计划失败：计划服务暂不可用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准计划' }))
    await waitFor(() => expect(approvalPosts(api)).toHaveLength(2))
  })

  it('suppresses the ticket card after approving the plan', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: workerDetail() },
      plans: { w1: plan() },
    })
    renderConsole()

    fireEvent.click(await screen.findByRole('button', { name: '批准计划' }))
    await waitFor(() => expect(approvalPosts(api)).toHaveLength(1))
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: '计划待批准' })).not.toBeInTheDocument()
    })
    // 计划批准后工单还挂着旧 human_action 时，不能再出现一张点了就 409 的工单卡。
    expect(document.querySelector('[data-attention-kind="ticket"]')).not.toBeInTheDocument()
    expect(await screen.findByText('还没有要你处理的事')).toBeInTheDocument()
  })

  it('does not stay pending when a decision ticket has no worker session', async () => {
    stubMedia((query) => query === DESKTOP)
    const api = createConsoleApi({
      messages: [],
      attentionTickets: [decisionTicket()],
      ticketDetails: { t1: makeTicketDetail({ ticket: decisionTicket(), worker_session_id: null }) },
    })
    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>
    }
    const { result } = renderHook(
      () => useAttentionPlans([toTicketView(decisionTicket())]),
      { wrapper: Wrapper },
    )

    await waitFor(() => expect(api.requests.some((item) => item.path === '/api/agent/tickets/t1')).toBe(true))
    await waitFor(() => expect(result.current.isPending).toBe(false))
    expect(result.current.items).toEqual([])
    expect(result.current.suppressedTicketIds.size).toBe(0)
  })

})
