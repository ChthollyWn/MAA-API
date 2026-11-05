import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { formatWatchdogTime } from './api'
import WatchdogPage from './WatchdogPage'

type Json = Record<string, unknown>

interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** 一条告警的全部列；测试只覆盖界面用得到的几个字段。 */
function alert(overrides: Json & { id: number }): Json {
  return {
    check: 'pipeline_no_callback',
    severity: 'warn',
    target_kind: 'device',
    target_id: 'pipeline-1',
    message: '流水线 3 分钟没有回调',
    evidence: {},
    action: 'stop_pipeline',
    action_status: 'recorded',
    created_at: '2026-11-05T04:00:00Z',
    resolved_at: null,
    ...overrides,
  }
}

function installApi(options: { status?: Json; alerts?: Json[] } = {}) {
  const status = options.status ?? {
    mode: 'record',
    last_run_at: '2026-11-05T04:00:00Z',
    open_count: 1,
  }
  const alerts = [...(options.alerts ?? [])]
  const requests: RecordedRequest[] = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push({ method: request.method, path: url.pathname, query: url.searchParams })

    if (url.pathname === '/api/agent/watchdog' && request.method === 'GET') {
      return json(status)
    }
    if (url.pathname === '/api/agent/watchdog/alerts' && request.method === 'GET') {
      const resolved = url.searchParams.get('resolved') === 'true'
      const items = alerts.filter((item) => (resolved ? item.resolved_at != null : item.resolved_at == null))
      return json({ items, total: items.length, page: 1, size: 50 })
    }
    const resolveMatch = /^\/api\/agent\/watchdog\/alerts\/(\d+)\/resolve$/.exec(url.pathname)
    if (resolveMatch && request.method === 'POST') {
      const item = alerts.find((entry) => entry.id === Number(resolveMatch[1]))
      if (!item) {
        return json({ error: { code: 'WATCHDOG_ALERT_NOT_FOUND', message: '告警不存在' } }, 404)
      }
      item.resolved_at = '2026-11-05T05:00:00Z'
      return json(item)
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    resolveCalls: () => requests.filter(
      (item) => item.method === 'POST' && item.path.startsWith('/api/agent/watchdog/alerts/'),
    ),
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [{ path: '/more/watchdog', element: <WatchdogPage /> }],
    { initialEntries: ['/more/watchdog'] },
  )
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe('M83 值守页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows the mode, last run time and unresolved alerts, then resolves one', async () => {
    const api = installApi({
      status: { mode: 'record', last_run_at: '2026-11-05T04:00:00Z', open_count: 2 },
      alerts: [
        alert({ id: 7, check: 'pipeline_no_callback', message: '流水线 3 分钟没有回调', created_at: '2026-11-05T03:50:00Z' }),
        alert({
          id: 8,
          check: 'queue_stuck',
          severity: 'critical',
          target_kind: 'queue',
          target_id: null,
          message: '队列里有流水线卡住',
          created_at: '2026-11-05T03:40:00Z',
        }),
      ],
    })
    renderPage()

    expect(await screen.findByText('只记录')).toBeInTheDocument()
    expect(screen.getByText(formatWatchdogTime('2026-11-05T04:00:00Z'))).toBeInTheDocument()
    expect(await screen.findByText('流水线 3 分钟没有回调')).toBeInTheDocument()
    expect(screen.getByText('队列里有流水线卡住')).toBeInTheDocument()
    expect(screen.getByText(/流水线无回调 · 设备「pipeline-1」/)).toBeInTheDocument()
    expect(screen.getByText(/队列卡住 · 队列 ·/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '解除' })).toHaveLength(2)
    // 页面只取未解除的告警。
    expect(api.requests.some(
      (request) => request.path === '/api/agent/watchdog/alerts'
        && request.query.get('resolved') === 'false',
    )).toBe(true)

    fireEvent.click(screen.getAllByRole('button', { name: '解除' })[0]!)
    await waitFor(() => expect(api.resolveCalls()).toHaveLength(1))
    expect(api.resolveCalls()[0]?.path).toBe('/api/agent/watchdog/alerts/7/resolve')
    await waitFor(() => expect(screen.queryByText('流水线 3 分钟没有回调')).not.toBeInTheDocument())
    expect(screen.getByText('队列里有流水线卡住')).toBeInTheDocument()
  })

  it('shows an empty state when there is no unresolved alert', async () => {
    installApi({ status: { mode: 'auto', last_run_at: null, open_count: 0 }, alerts: [] })
    renderPage()

    expect(await screen.findByText('自动处理')).toBeInTheDocument()
    expect(screen.getByText('还没有巡检')).toBeInTheDocument()
    expect(await screen.findByText('没有未解除的告警')).toBeInTheDocument()
  })
})
