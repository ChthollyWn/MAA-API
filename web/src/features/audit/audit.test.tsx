import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { AuditPage } from '@/features/audit/AuditPage'
import { useAuth } from '@/stores/auth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const auditRow = {
  audit_id: 91,
  caller: 'rest',
  caller_detail: null,
  session_id: null,
  tool_name: 'submit_pipeline',
  arguments: { tasks: [{ name: 'Fight', stone: 2 }] },
  result_summary: '{"pipeline_id":"pipeline-1"}',
  result_ref: { pipeline_id: 'pipeline-1' },
  status: 'success',
  error_code: null,
  risk_level: 'consume',
  forced: false,
  confirmation_id: 'confirm-1',
  authorized_by_id: null,
  duration_ms: 1250,
  request_id: 'request-1',
  created_at: '2026-09-25T12:00:00Z',
}

function renderAudit(fetchMock: typeof fetch) {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter([
    { path: '/more/audit', element: <AuditPage /> },
    { path: '/tasks/history/:pipelineId', element: <div>Pipeline log destination</div> },
  ], { initialEntries: ['/more/audit'] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return router
}

describe('Agent audit page', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuth.getState().clearToken()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('filters audit history, expands full details, and opens related pipeline logs', async () => {
    const urls: URL[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      urls.push(url)
      if (url.pathname === '/api/agent/audits' && request.method === 'GET') {
        return json({ items: [auditRow], total: 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/agent/audits/91') return json(auditRow)
      return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    })
    const router = renderAudit(fetchMock)

    expect(await screen.findByRole('heading', { name: 'Agent 审计' })).toBeInTheDocument()
    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('调用方'), { target: { value: 'rest' } })
    fireEvent.change(screen.getByLabelText('工具名'), { target: { value: 'submit_pipeline' } })
    fireEvent.change(screen.getByLabelText('状态'), { target: { value: 'success' } })
    fireEvent.change(screen.getByLabelText('风险'), { target: { value: 'consume' } })
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-09-24' } })
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    expect(screen.getByLabelText('开始日期')).toHaveValue('2026-09-24')
    await waitFor(() => expect(urls.some((url) => (
      url.searchParams.get('caller') === 'rest'
      && url.searchParams.get('tool_name') === 'submit_pipeline'
      && url.searchParams.get('status') === 'success'
      && url.searchParams.get('risk_level') === 'consume'
      && url.searchParams.get('since') === '2026-09-24T00:00:00.000Z'
    ))).toBe(true))

    fireEvent.click(screen.getByRole('button', { name: /查看详情/ }))
    expect(await screen.findByText(/request-1/)).toBeInTheDocument()
    expect(screen.getByText(/Fight/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '查看流水线与日志' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history/pipeline-1'))
    expect(await screen.findByText('Pipeline log destination')).toBeInTheDocument()
  })

  it('recovers from failed audit loading with a visible retry action', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json(
      { error: { code: 'SERVICE_UNAVAILABLE', message: 'audit down' } }, 503,
    ))
    renderAudit(fetchMock)

    expect(await screen.findByRole('alert')).toHaveTextContent(/audit down/)
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('moves through filtered result pages and keeps filters in the URL', async () => {
    const urls: URL[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      const url = new URL(request.url)
      urls.push(url)
      if (url.pathname === '/api/agent/audits') return json({ items: [auditRow], total: 42, page: Number(url.searchParams.get('page') ?? 1), size: 20 })
      return json(auditRow)
    })
    const router = renderAudit(fetchMock)

    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('调用方'), { target: { value: 'rest' } })
    fireEvent.change(screen.getByLabelText('状态'), { target: { value: 'success' } })
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    await waitFor(() => expect(router.state.location.search).toContain('caller=rest'))
    await waitFor(() => expect(urls.some((url) => url.searchParams.get('page') === '1' && url.searchParams.get('caller') === 'rest' && url.searchParams.get('status') === 'success')).toBe(true))
    await screen.findByRole('button', { name: '下一页' })
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(router.state.location.search).toContain('page=2'))
    await waitFor(() => expect(urls.some((url) => url.searchParams.get('page') === '2' && url.searchParams.get('caller') === 'rest' && url.searchParams.get('status') === 'success')).toBe(true))
  })

  it('restores a date control from the ISO since value in the shared URL', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({ items: [], total: 0, page: 1, size: 20 }))
    vi.stubGlobal('fetch', fetchMock)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const router = createMemoryRouter([
      { path: '/more/audit', element: <AuditPage /> },
    ], { initialEntries: ['/more/audit?since=2026-09-24T00%3A00%3A00.000Z'] })
    render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)

    expect(await screen.findByLabelText('开始日期')).toHaveValue('2026-09-24')
  })
})
