import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UpdatesPage from '@/routes/updates'
import { UpdateProgress } from '@/features/updates/UpdateProgress'
import { handleServerEvent } from '@/realtime/query-bridge'
import type { UpdateRecord, UpdateStatusResponse } from '@/features/updates/types'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const activeRecord: UpdateRecord = {
  id: 'update-1', target: 'resource', status: 'running', phase: 'downloading',
  percent: 20, bytes_downloaded: 2048, bytes_total: 10240,
  created_at: '2026-09-24T02:00:00Z',
}

function statusSnapshot(overrides: Partial<UpdateStatusResponse> = {}): UpdateStatusResponse {
  return {
    updates: {
      core: { target: 'core', current: '6.17.5', latest: '6.18.0', available: true },
      resource: {
        target: 'resource', available: true, reload_pending: false,
        channels: {
          ota: { channel: 'ota', available: false, last_synced_at: '2026-09-23T12:00:00Z' },
          repo: { channel: 'repo', current: '2026-09-20 11:08:36', latest: '2026-09-23 04:36:14', available: true },
        },
      },
      game: {
        target: 'game', available: true,
        channels: {
          Official: { channel: 'Official', current: '2.7.71', latest: null, remote_size: 1_879_048_192, available: true },
          Bilibili: { channel: 'Bilibili', current: '2.7.71', latest: '2.7.72', available: true },
        },
      },
    },
    checked_at: '2026-09-24T02:00:00Z',
    cached: true,
    running: null,
    ...overrides,
  }
}

function renderPage(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <UpdatesPage />
    </QueryClientProvider>,
  )
}

describe('updates page', () => {
  let queryClient: QueryClient
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
  let requests: Request[]

  beforeEach(() => {
    requests = []
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    })
    fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const url = new URL(request.url)
      if (url.pathname === '/api/updates/status') return response(statusSnapshot())
      if (url.pathname === '/api/updates') return response({ items: [], total: 0, page: 1, size: 20 })
      return response({ detail: 'unexpected request' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    cleanup()
    queryClient.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows each target and keeps cached target data visible when a manual check fails', async () => {
    renderPage(queryClient)

    const core = await screen.findByRole('heading', { name: 'MAA 内核' })
    expect(core).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '活动资源' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '游戏本体' })).toBeInTheDocument()
    expect(await screen.findByText('6.18.0（stable）')).toBeInTheDocument()
    expect(screen.getByText('6.17.5')).toBeInTheDocument()
    expect(screen.getByText('2026-09-20 11:08:36')).toBeInTheDocument()
    expect(screen.getByText('官服未提供版本号接口，以下按远端包信息展示。')).toBeInTheDocument()

    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response({ detail: '版本源暂时不可用' }, 503)
    })
    fireEvent.click(screen.getByRole('button', { name: '立即检查更新' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('立即检查失败')
    expect(requests.some((request) => new URL(request.url).searchParams.get('refresh') === 'true')).toBe(true)
    expect(screen.getByRole('heading', { name: 'MAA 内核' })).toBeInTheDocument()
    expect(screen.getByText('6.17.5')).toBeInTheDocument()
  })

  it('submits a user-selected channel only after force interruption is confirmed', async () => {
    let running = statusSnapshot()
    let currentDetail = activeRecord
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const url = new URL(request.url)
      if (url.pathname === '/api/updates/status') return response(running)
      if (url.pathname === '/api/updates') return response({ items: [], total: 0, page: 1, size: 20 })
      if (url.pathname === '/api/updates/resource' && request.method === 'POST') {
        return response(activeRecord, 202)
      }
      if (url.pathname === '/api/updates/update-1' && request.method === 'DELETE') {
        currentDetail = { ...activeRecord, status: 'cancelled', phase: 'cancelled' }
        running = statusSnapshot()
        return response(currentDetail, 202)
      }
      if (url.pathname === '/api/updates/update-1') return response(currentDetail)
      return response({ detail: 'unexpected request' }, 404)
    })
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)

    renderPage(queryClient)
    const resourceCard = await screen.findByRole('heading', { name: '活动资源' }).then((heading) => heading.closest('[class*="rounded-xl"]'))
    expect(resourceCard).not.toBeNull()
    fireEvent.change(within(resourceCard as HTMLElement).getByLabelText('更新范围'), { target: { value: 'repo' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /允许强制中断流水线/ }))
    fireEvent.click(within(resourceCard as HTMLElement).getByRole('button', { name: '更新资源仓库' }))

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('取消正在执行的流水线'))
    await screen.findByText('20%')
    const startRequest = requests.find((request) => new URL(request.url).pathname === '/api/updates/resource' && request.method === 'POST')
    expect(startRequest).toBeDefined()
    expect(JSON.parse(await startRequest!.clone().text())).toEqual({
      channel: 'repo', force: false, force_interrupt: true, reload_mode: 'wait',
    })
    expect(requests.some((request) => new URL(request.url).pathname === '/api/updates/update-1')).toBe(true)
    expect(screen.getByRole('button', { name: '取消更新' })).toBeInTheDocument()

    running = statusSnapshot({ running: { ...activeRecord, percent: 64 } })
    await act(async () => {
      handleServerEvent({ type: 'update_progress', data: { update_id: 'update-1', phase: 'downloading', percent: 64 } }, queryClient)
    })
    // The realtime bridge invalidates the status query; the refreshed running item supplies the new phase/percent.
    expect(await screen.findByText('64%')).toBeInTheDocument()
    expect(requests.filter((request) => new URL(request.url).pathname === '/api/updates/status').length).toBeGreaterThanOrEqual(3)

    fireEvent.click(screen.getByRole('button', { name: '取消更新' }))
    await waitFor(() => expect(requests.some((request) => new URL(request.url).pathname === '/api/updates/update-1' && request.method === 'DELETE')).toBe(true))
  })

  it('renders failed update details and calls the manual retry endpoint', async () => {
    const failed: UpdateRecord = {
      id: 'failed-1', target: 'core', status: 'failed', phase: 'verifying',
      from_version: '6.17.5', to_version: '6.18.0', error_code: 'CHECKSUM_MISMATCH',
      error_message: '下载文件校验失败', created_at: '2026-09-23T12:00:00Z', log: ['checksum mismatch'],
    }
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const url = new URL(request.url)
      if (url.pathname === '/api/updates/status') return response(statusSnapshot())
      if (url.pathname === '/api/updates') return response({ items: [failed], total: 1, page: 1, size: 20 })
      if (url.pathname === '/api/updates/failed-1') return response(failed)
      if (url.pathname === '/api/updates/failed-1/retry' && request.method === 'POST') return response(activeRecord, 202)
      if (url.pathname === '/api/updates/update-1') return response(activeRecord)
      return response({ detail: 'unexpected request' }, 404)
    })

    renderPage(queryClient)
    expect(await screen.findByText('6.17.5 → 6.18.0')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }))
    expect(screen.getByText('CHECKSUM_MISMATCH')).toBeInTheDocument()
    expect(screen.getByText('下载文件校验失败')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试更新' }))
    await screen.findByText('20%')

    const retryRequest = requests.find((request) => new URL(request.url).pathname === '/api/updates/failed-1/retry')
    expect(retryRequest?.method).toBe('POST')
  })

  it('keeps stable core installation available when the running core version is unknown', async () => {
    const unavailableCore = statusSnapshot({
      updates: {
        ...statusSnapshot().updates,
        core: { target: 'core', current: null, latest: null, available: null, error: '内核未就绪' },
      },
    })
    const accepted: UpdateRecord = {
      id: 'core-update-1', target: 'core', status: 'running', phase: 'downloading',
      created_at: '2026-09-24T02:00:00Z',
    }
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const url = new URL(request.url)
      if (url.pathname === '/api/updates/status') return response(unavailableCore)
      if (url.pathname === '/api/updates') return response({ items: [], total: 0, page: 1, size: 20 })
      if (url.pathname === '/api/updates/core' && request.method === 'POST') return response(accepted, 202)
      if (url.pathname === '/api/updates/core-update-1') return response(accepted)
      return response({ detail: 'unexpected request' }, 404)
    })

    renderPage(queryClient)
    await screen.findByText('内核检查失败：内核未就绪')
    const installButton = await screen.findByRole('button', { name: '安装 stable 内核' })
    expect(installButton).toBeEnabled()
    fireEvent.click(installButton)

    await waitFor(() => expect(requests.some((request) =>
      new URL(request.url).pathname === '/api/updates/core' && request.method === 'POST')).toBe(true))
    const updateRequest = requests.find((request) => new URL(request.url).pathname === '/api/updates/core')!
    expect(JSON.parse(await updateRequest.clone().text())).toEqual({
      channel: 'stable', force: false, force_interrupt: false,
    })
  })

  it('uses the API byte-done field when showing active download progress', () => {
    render(
      <UpdateProgress
        record={{
          ...activeRecord,
          percent: 50,
          bytes_downloaded: undefined,
          bytes_done: 1024,
          bytes_total: 4096,
        }}
        onCancel={() => undefined}
      />,
    )

    expect(screen.getByText('1.0 KB / 4.0 KB')).toBeInTheDocument()
  })
})
