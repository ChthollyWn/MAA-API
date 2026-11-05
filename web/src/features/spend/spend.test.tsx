import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import SpendPage from './SpendPage'

type Json = Record<string, unknown>

interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** 今日三项：理智药有上限、源石不限、加急许可有上限但没用。 */
const TODAY: Json[] = [
  { kind: 'medicine', label: '理智药', used: 4, limit: 5, remaining: 1 },
  { kind: 'stone', label: '源石', used: 3, limit: -1, remaining: null },
  { kind: 'expedite', label: '加急许可', used: 0, limit: 2, remaining: 2 },
]

/** 从 2026-11-05 往前数 `count` 天，日期与后端一致按升序给。 */
function dayRows(count: number): Json[] {
  const today = Date.parse('2026-11-05T00:00:00Z')
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(today - (count - 1 - index) * 86_400_000)
    return { date: date.toISOString().slice(0, 10), medicine: index, stone: 0, expedite: 0 }
  })
}

function installApi(options: { blocked?: Json[] } = {}) {
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push({ method: request.method, path: url.pathname, query: url.searchParams })

    if (url.pathname === '/api/agent/spend' && request.method === 'GET') {
      // 近 30 天显式带 since；缺省即接口默认的近 7 天。
      const count = url.searchParams.get('since') ? 30 : 7
      return json({
        date: '2026-11-05',
        day_start: '2026-11-04T20:00:00Z',
        day_end: '2026-11-05T20:00:00Z',
        today: TODAY,
        days: dayRows(count),
        blocked_today: options.blocked ?? [],
        capped_sources: ['agent', 'scheduled'],
      })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    spendRequests: () => requests.filter(
      (request) => request.method === 'GET' && request.path === '/api/agent/spend',
    ),
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/spend', element: <SpendPage /> },
      { path: '/more/settings/agent', element: <div>设置页</div> },
    ],
    { initialEntries: ['/more/spend'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe('M84 消耗护栏页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows today usage, the blocked submissions and the settings link', async () => {
    const api = installApi({
      blocked: [
        {
          at: '2026-11-05T12:00:00Z',
          source: 'scheduled',
          kind: 'medicine',
          requested: 3,
          used: 4,
          limit: 5,
          ref: 'schedule-1',
        },
      ],
    })
    renderPage()

    expect(await screen.findByText('理智药 4 / 5')).toBeInTheDocument()
    expect(screen.getByText('源石 3（不限）')).toBeInTheDocument()
    expect(screen.getByText('加急许可 0 / 2')).toBeInTheDocument()
    // 不限的源石不画进度条；另外两项各有一条。
    expect(screen.getAllByRole('progressbar')).toHaveLength(2)
    expect(screen.queryByRole('progressbar', { name: '源石用量' })).not.toBeInTheDocument()

    // 拦截记录按北京时间显示钟点：12:00Z 是 20:00。
    expect(await screen.findByText('20:00 定时任务 · 理智药 需要 3，已计 4 / 5')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '调整上限' })).toHaveAttribute('href', '/more/settings/agent')

    const table = screen.getByRole('table', { name: '按日用量' })
    expect(within(table).getAllByRole('row')).toHaveLength(8)
    // 默认近 7 天不传 since。
    expect(api.spendRequests()[0]?.query.get('since')).toBeNull()
  })

  it('switches to the last 30 days and hides the empty blocked section', async () => {
    const api = installApi()
    renderPage()

    expect(await screen.findByText('理智药 4 / 5')).toBeInTheDocument()
    expect(screen.queryByText('今天拦下的提交')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: '近 30 天' }))

    await waitFor(() => {
      const since = api.spendRequests().find((request) => request.query.get('since'))
      expect(since?.query.get('since')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
    await waitFor(() => {
      const table = screen.getByRole('table', { name: '按日用量' })
      expect(within(table).getAllByRole('row')).toHaveLength(31)
    })
    expect(screen.getByText('理智药 4 / 5')).toBeInTheDocument()
    // 切回近 7 天，表格恢复成 7 行。
    fireEvent.click(screen.getByRole('radio', { name: '近 7 天' }))
    await waitFor(() => {
      const table = screen.getByRole('table', { name: '按日用量' })
      expect(within(table).getAllByRole('row')).toHaveLength(8)
    })
  })
})
