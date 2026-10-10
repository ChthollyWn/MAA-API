import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { ConfirmationsPanel } from '@/features/confirmations/ConfirmationsPanel'
import { handleServerEvent } from '@/realtime/query-bridge'
import type { ServerEvent } from '@/realtime/events'
import { useAuth } from '@/stores/auth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const pendingConfirmation = {
  confirmation_id: 'confirm-1',
  action: 'submit_pipeline',
  risk_level: 'consume',
  reason: '碎石 2 颗',
  payload: { tool_name: 'submit_pipeline', arguments: { tasks: [{ name: 'Fight', stone: 2 }] } },
  status: 'pending',
  requested_by: 'rest',
  audit_id: 91,
  expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  created_at: '2026-09-25T12:20:00Z',
  resolved_at: null,
  resolved_by: null,
  resolved_reason: null,
  execution: null,
}

function renderPanel(fetchMock: typeof fetch) {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  // 面板用 useSearchParams 支持推送深链（/?confirm=<id>），所以需要 Router 上下文。
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <ConfirmationsPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...view, queryClient }
}

describe('M11 pending confirmations panel', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads pending approvals, shows the exact action impact, and submits approval', async () => {
    const requests: Request[] = []
    let approved = false
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      requests.push(request)
      const url = new URL(request.url)
      if (url.pathname === '/api/confirmations' && request.method === 'GET') {
        return json({ items: approved ? [] : [pendingConfirmation], total: approved ? 0 : 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/confirmations/confirm-1' && request.method === 'POST') {
        expect(await request.json()).toEqual({ approved: true })
        approved = true
        return json({ ...pendingConfirmation, status: 'approved' })
      }
      return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    })

    renderPanel(fetchMock)

    expect(await screen.findByRole('heading', { name: '需要人工确认' })).toBeInTheDocument()
    expect(screen.getByText(/碎石 2 颗/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('查看操作参数'))
    expect(screen.getByText(/Fight/)).toBeInTheDocument()
    expect(screen.getByText(/stone/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准' }))

    await waitFor(() => expect(approved).toBe(true))
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
    expect(requests.some((request) => request.url.endsWith('/api/confirmations/confirm-1'))).toBe(true)
  })

  it('renders an account write as structured details instead of a JSON blob', async () => {
    const accountConfirmation = {
      ...pendingConfirmation,
      confirmation_id: 'confirm-2',
      action: 'agent_tool',
      risk_level: 'consume',
      reason: '养成计划写入：1 个目标',
      payload: {
        tool_name: 'start_upgrade_plan',
        arguments: { name: '阿米娅精二' },
        summary: '把阿米娅精二，顺带刷固源岩',
        targets: [{ operator: '阿米娅', elite: 2, skill_level: 7, masteries: { '2': 3 }, modules: [{ type: 'X', level: 2 }] }],
        run_times: ['04:00', '16:00'],
        preview: {
          today_tasks: [{ name: 'Fight', stage: '1-7', times: 12 }],
          daily_template: [{ name: 'StartUp' }, { name: 'Fight', stage: '1-7', times: 5, expiring_medicine: 999 }],
          medicine: { today: 0, stone: 0, expiring_medicine: 999 },
          days_estimated: 6,
          total_sanity: 216,
          stages: [{ stage_code: '1-7', runs: 12, sanity: 216, items: [{ item_name: '固源岩' }] }],
          conflicts: [{ schedule_id: 'sched-1', name: '夜间刷关' }],
          unobtainable: [{ name: '聚酸酯', amount: 10, have: 4, missing: 6, reason: '仓库不足', hint: '先刷 4-4' }],
          notes: ['预览：没有提交流水线'],
        },
      },
    }
    const fetchMock = vi.fn<typeof fetch>(async () => json({ items: [accountConfirmation], total: 1, page: 1, size: 20 }))

    renderPanel(fetchMock)

    expect(await screen.findByText('养成计划摘要')).toBeInTheDocument()
    expect(screen.getByText('阿米娅')).toBeInTheDocument()
    expect(screen.getByText('精二 · 技能 7 · S2 3 · 模组 X2')).toBeInTheDocument()
    expect(screen.getByLabelText('今天要提交的刷关清单')).toBeInTheDocument()
    expect(screen.getByLabelText('每日定时任务模板')).toBeInTheDocument()
    expect(screen.getByText('今天 理智药 0、源石 0、临期理智药 999')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('与现有定时任务争抢理智：夜间刷关（sched-1）')
    // 账号写入不再退回 JSON 展开。
    expect(screen.queryByText('查看操作参数')).not.toBeInTheDocument()
  })

  it('shows a recovery action when loading approvals fails', async () => {
    renderPanel(vi.fn<typeof fetch>(async () => json(
      { error: { code: 'SERVICE_UNAVAILABLE', message: 'service down' } }, 503,
    )))

    expect(await screen.findByRole('alert')).toHaveTextContent(/service down/)
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('identifies the session whose screen controls the grant will authorize', async () => {
    const grant = {
      ...pendingConfirmation,
      action: 'grant_atomic_ops',
      reason: '该会话尚未获得有效的原子操作授权',
      payload: { grant: true, session_id: 'session-target-42', window_seconds: 900 },
    }
    const fetchMock = vi.fn<typeof fetch>(async () => json({ items: [grant], total: 1, page: 1, size: 20 }))

    renderPanel(fetchMock)

    expect(await screen.findByText(/session-target-42/)).toBeInTheDocument()
    expect(screen.getAllByText(/15 分钟/).some((element) => element.tagName === 'P')).toBe(true)
  })

  it('refreshes a stale card after the server rejects a raced duplicate decision', async () => {
    let resolved = false
    let reads = 0
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      if (url.pathname === '/api/confirmations' && request.method === 'GET') {
        reads += 1
        return json({ items: resolved ? [] : [pendingConfirmation], total: resolved ? 0 : 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/confirmations/confirm-1' && request.method === 'POST') {
        resolved = true
        return json({ error: { code: 'CONFIRMATION_ALREADY_RESOLVED', message: 'already resolved' } }, 409)
      }
      return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    })

    renderPanel(fetchMock)
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准' }))

    await waitFor(() => expect(reads).toBeGreaterThan(1))
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
  })

  it('refetches pending approvals when the shared WebSocket reports resolution', async () => {
    let reads = 0
    const fetchMock = vi.fn<typeof fetch>(async () => {
      reads += 1
      return json({ items: reads === 1 ? [pendingConfirmation] : [], total: 0, page: 1, size: 20 })
    })
    const { queryClient } = renderPanel(fetchMock)
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()

    act(() => handleServerEvent({
      type: 'confirm_resolved',
      data: { confirmation_id: 'confirm-1', resolution: 'approved' },
      ts: Date.now() / 1000,
    } as ServerEvent, queryClient))

    await waitFor(() => expect(reads).toBeGreaterThan(1), { timeout: 1000 })
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
  })

  it('reloads approvals after the WebSocket subscription acknowledgement', async () => {
    let reads = 0
    const fetchMock = vi.fn<typeof fetch>(async () => {
      reads += 1
      return json({ items: reads === 1 ? [pendingConfirmation] : [], total: 0, page: 1, size: 20 })
    })
    const { queryClient } = renderPanel(fetchMock)
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()

    act(() => handleServerEvent({
      type: 'subscribed',
      data: { channels: ['agent', 'logs'], backfilled: 0 },
      ts: Date.now() / 1000,
    } as ServerEvent, queryClient))

    await waitFor(() => expect(reads).toBeGreaterThan(1), { timeout: 1000 })
    await waitFor(() => expect(screen.queryByText('submit_pipeline')).not.toBeInTheDocument())
  })

  it('fetches a new approval after the shared WebSocket reports a request', async () => {
    let requested = false
    const fetchMock = vi.fn<typeof fetch>(async () => json({
      items: requested ? [pendingConfirmation] : [],
      total: requested ? 1 : 0,
      page: 1,
      size: 20,
    }))
    const { queryClient } = renderPanel(fetchMock)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    requested = true

    act(() => handleServerEvent({
      type: 'confirm_request',
      data: { confirmation_id: 'confirm-1', action: 'submit_pipeline' },
      ts: Date.now() / 1000,
    } as ServerEvent, queryClient))

    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
  })
})
