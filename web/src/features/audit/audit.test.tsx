import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  session_id: 'session-7',
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

function toRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
}

function renderAudit(fetchMock: typeof fetch, initialEntry = '/more/audit') {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter([
    { path: '/more/audit', element: <AuditPage /> },
    { path: '/tasks/history/:pipelineId', element: <div>Pipeline log destination</div> },
  ], { initialEntries: [initialEntry] })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return router
}

async function openFilters() {
  fireEvent.click(screen.getByRole('button', { name: /^筛选/ }))
  return screen.findByRole('dialog', { name: '筛选审计记录' })
}

async function waitForSheetToClose() {
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

function recordList() {
  return screen.getByRole('list', { name: '审计记录' })
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

  it('A1 filters audit history, shows full details, and opens the related pipeline logs', async () => {
    const urls: URL[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = toRequest(input, init)
      const url = new URL(request.url)
      urls.push(url)
      if (url.pathname === '/api/agent/audits' && request.method === 'GET') {
        return json({ items: [auditRow], total: 1, page: 1, size: 20 })
      }
      if (url.pathname === '/api/agent/audits/91') return json(auditRow)
      return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    })
    const router = renderAudit(fetchMock)

    expect(await screen.findByRole('heading', { name: 'Agent 审计', level: 1 })).toBeInTheDocument()
    expect(within(await screen.findByRole('list', { name: '审计记录' })).getByText('submit_pipeline')).toBeInTheDocument()
    const row = within(recordList()).getByRole('button', { name: /submit_pipeline/ })
    expect(row).toHaveTextContent('成功')
    expect(row).toHaveTextContent('资源消耗')
    expect(row).toHaveTextContent('1250 ms')

    const sheet = await openFilters()
    expect(within(sheet).getByText(/UTC/)).toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('radio', { name: 'REST' }))
    fireEvent.change(within(sheet).getByLabelText('工具名'), { target: { value: 'submit_pipeline' } })
    fireEvent.change(within(sheet).getByLabelText('状态'), { target: { value: 'success' } })
    fireEvent.change(within(sheet).getByLabelText('风险'), { target: { value: 'consume' } })
    fireEvent.change(within(sheet).getByLabelText('开始日期'), { target: { value: '2026-09-24' } })
    fireEvent.click(within(sheet).getByRole('button', { name: '应用' }))
    await waitForSheetToClose()

    await waitFor(() => expect(urls.some((url) => (
      url.pathname === '/api/agent/audits'
      && url.searchParams.get('caller') === 'rest'
      && url.searchParams.get('tool_name') === 'submit_pipeline'
      && url.searchParams.get('status') === 'success'
      && url.searchParams.get('risk_level') === 'consume'
      && url.searchParams.get('since') === '2026-09-24T00:00:00.000Z'
    ))).toBe(true))
    expect(new URLSearchParams(router.state.location.search).get('since')).toBe('2026-09-24T00:00:00.000Z')
    expect(screen.getByRole('button', { name: '筛选，已生效 5 项' })).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: '生效的筛选条件' })).getAllByRole('listitem')).toHaveLength(5)

    const reopened = await openFilters()
    expect(within(reopened).getByLabelText('开始日期')).toHaveValue('2026-09-24')
    expect(within(reopened).getByLabelText('工具名')).toHaveValue('submit_pipeline')
    fireEvent.click(within(reopened).getByRole('button', { name: '关闭' }))
    await waitForSheetToClose()

    fireEvent.click(within(recordList()).getByRole('button', { name: /submit_pipeline/ }))
    const detail = await screen.findByRole('dialog', { name: 'submit_pipeline' })
    expect(within(detail).getByText('91')).toBeInTheDocument()
    expect(await within(detail).findByText('request-1')).toBeInTheDocument()
    expect(within(detail).getByText('session-7')).toBeInTheDocument()
    expect(within(detail).getByText('confirm-1')).toBeInTheDocument()
    expect(within(detail).getByText(/"Fight"/)).toBeInTheDocument()
    fireEvent.click(within(detail).getByRole('link', { name: '查看流水线与日志' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history/pipeline-1'))
    expect(await screen.findByText('Pipeline log destination')).toBeInTheDocument()
  })

  it('A2 recovers from failed audit loading with a visible retry action', async () => {
    let available = false
    const fetchMock = vi.fn<typeof fetch>(async () => (available
      ? json({ items: [auditRow], total: 1, page: 1, size: 20 })
      : json({ error: { code: 'SERVICE_UNAVAILABLE', message: 'audit down' } }, 503)))
    renderAudit(fetchMock)

    expect(await screen.findByRole('alert')).toHaveTextContent(/audit down/)
    available = true
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await within(await screen.findByRole('list', { name: '审计记录' })).findByText('submit_pipeline')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('A3 moves through filtered result pages and keeps filters in the URL', async () => {
    const urls: URL[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(toRequest(input, init).url)
      urls.push(url)
      if (url.pathname === '/api/agent/audits') return json({ items: [auditRow], total: 42, page: Number(url.searchParams.get('page') ?? 1), size: 20 })
      return json(auditRow)
    })
    const router = renderAudit(fetchMock)

    expect(await screen.findByText('submit_pipeline')).toBeInTheDocument()
    const sheet = await openFilters()
    fireEvent.click(within(sheet).getByRole('radio', { name: 'REST' }))
    fireEvent.change(within(sheet).getByLabelText('状态'), { target: { value: 'success' } })
    fireEvent.click(within(sheet).getByRole('button', { name: '应用' }))
    await waitForSheetToClose()
    await waitFor(() => expect(router.state.location.search).toContain('caller=rest'))
    await waitFor(() => expect(urls.some((url) => url.searchParams.get('page') === '1' && url.searchParams.get('caller') === 'rest' && url.searchParams.get('status') === 'success')).toBe(true))
    expect(await screen.findByText('第 1 / 3 页')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(router.state.location.search).toContain('page=2'))
    const search = new URLSearchParams(router.state.location.search)
    expect(search.get('caller')).toBe('rest')
    expect(search.get('status')).toBe('success')
    await waitFor(() => expect(urls.some((url) => url.searchParams.get('page') === '2' && url.searchParams.get('caller') === 'rest' && url.searchParams.get('status') === 'success')).toBe(true))
    expect(await screen.findByText('第 2 / 3 页')).toBeInTheDocument()
  })

  it('A4 restores the date control from the ISO since value in a shared URL', async () => {
    const urls: URL[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      urls.push(new URL(toRequest(input, init).url))
      return json({ items: [], total: 0, page: 1, size: 20 })
    })
    renderAudit(fetchMock, '/more/audit?since=2026-09-24T00%3A00%3A00.000Z')

    expect(await screen.findByText('没有符合条件的审计记录')).toBeInTheDocument()
    expect(urls.some((url) => url.searchParams.get('since') === '2026-09-24T00:00:00.000Z')).toBe(true)
    expect(screen.getByRole('button', { name: '移除筛选条件：开始日期：2026-09-24' })).toBeInTheDocument()
    const sheet = await openFilters()
    expect(within(sheet).getByLabelText('开始日期')).toHaveValue('2026-09-24')
  })

  it('removes a single filter chip or all of them and keeps the URL in sync', async () => {
    const urls: URL[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(toRequest(input, init).url)
      urls.push(url)
      return json({ items: [auditRow], total: 42, page: Number(url.searchParams.get('page') ?? 1), size: 20 })
    })
    const router = renderAudit(fetchMock, '/more/audit?caller=internal&status=failed&page=2')

    const chips = await screen.findByRole('list', { name: '生效的筛选条件' })
    expect(within(chips).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('button', { name: '筛选，已生效 2 项' })).toBeInTheDocument()
    expect(await screen.findByText('第 2 / 3 页')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '移除筛选条件：状态：失败' }))
    await waitFor(() => expect(router.state.location.search).toBe('?caller=internal'))
    await waitFor(() => expect(urls.some((url) => (
      url.searchParams.get('caller') === 'internal'
      && !url.searchParams.has('status')
      && url.searchParams.get('page') === '1'
    ))).toBe(true))
    expect(within(screen.getByRole('list', { name: '生效的筛选条件' })).getAllByRole('listitem')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '清除全部' }))
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.queryByRole('list', { name: '生效的筛选条件' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '筛选' })).toBeInTheDocument()
  })

  it('clears every filter from the filter sheet', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({ items: [auditRow], total: 1, page: 1, size: 20 }))
    const router = renderAudit(fetchMock, '/more/audit?tool_name=submit_pipeline&risk_level=destructive')

    const sheet = await openFilters()
    expect(within(sheet).getByLabelText('风险')).toHaveValue('destructive')
    fireEvent.click(within(sheet).getByRole('button', { name: '清除' }))
    await waitForSheetToClose()
    await waitFor(() => expect(router.state.location.search).toBe(''))
  })
})
