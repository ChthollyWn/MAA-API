import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UpdatesPage from '@/routes/updates'
import { UpdateProgress } from '@/features/updates/UpdateProgress'
import type { UpdateRecord, UpdateStatusResponse } from '@/features/updates/types'
import { handleServerEvent } from '@/realtime/query-bridge'

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

const failedRecord: UpdateRecord = {
  id: 'failed-1', target: 'core', status: 'failed', phase: 'verifying',
  from_version: '6.17.5', to_version: '6.18.0', error_code: 'CHECKSUM_MISMATCH',
  error_message: '下载文件校验失败', created_at: '2026-09-23T12:00:00Z', log: ['checksum mismatch'],
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

type Handler = (request: Request, url: URL) => Response | undefined

function isRequest(request: Request, pathname: string, method = 'GET'): boolean {
  return new URL(request.url).pathname === pathname && request.method === method
}

async function bodyOf(request: Request | undefined): Promise<unknown> {
  expect(request).toBeDefined()
  return JSON.parse(await request!.clone().text()) as unknown
}

function renderPage(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/more/updates']}>
        <UpdatesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** 目标卡片是以目标名称为标题的 article。 */
function card(name: string): HTMLElement {
  const article = screen.getByRole('heading', { name }).closest('article')
  expect(article).not.toBeNull()
  return article as HTMLElement
}

async function waitForSheetToClose() {
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

describe('updates page', () => {
  let queryClient: QueryClient
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
  let requests: Request[]

  function serve(handler: Handler = () => undefined) {
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
      requests.push(request)
      const url = new URL(request.url)
      const handled = handler(request, url)
      if (handled) return handled
      if (url.pathname === '/api/updates/status') return response(statusSnapshot())
      if (url.pathname === '/api/updates') return response({ items: [], total: 0, page: 1, size: 20 })
      return response({ detail: 'unexpected request' }, 404)
    })
  }

  beforeEach(() => {
    requests = []
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    })
    fetchMock = vi.fn<typeof fetch>()
    serve()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    cleanup()
    queryClient.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('U1 shows each target with its versions and keeps cached data visible when a manual check fails', async () => {
    renderPage(queryClient)

    expect(await screen.findByRole('heading', { name: '热更新', level: 1 })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'MAA 内核' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '活动资源' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '游戏本体' })).toBeInTheDocument()
    const core = card('MAA 内核')
    expect(within(core).getByText('6.17.5')).toBeInTheDocument()
    expect(within(core).getByText('6.18.0')).toBeInTheDocument()
    expect(within(core).getByText('可更新')).toBeInTheDocument()
    const resource = card('活动资源')
    expect(within(resource).getByText('资源仓库')).toBeInTheDocument()
    expect(within(resource).getByText('OTA 资源')).toBeInTheDocument()
    expect(within(resource).getByText('2026-09-20 11:08:36')).toBeInTheDocument()
    expect(within(resource).getByText('2026-09-23 04:36:14')).toBeInTheDocument()
    expect(within(card('游戏本体')).getByText(/官服未提供版本号接口/)).toBeInTheDocument()
    expect(screen.getByText(/缓存结果/)).toBeInTheDocument()

    serve(() => response({ detail: '版本源暂时不可用' }, 503))
    fireEvent.click(screen.getByRole('button', { name: '立即检查' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('立即检查失败')
    expect(alert).toHaveTextContent('版本源暂时不可用')
    expect(requests.some((request) => new URL(request.url).searchParams.get('refresh') === 'true')).toBe(true)
    expect(screen.getByRole('heading', { name: 'MAA 内核' })).toBeInTheDocument()
    expect(within(card('MAA 内核')).getByText('6.17.5')).toBeInTheDocument()
  })

  it('U2 posts the chosen resource channel with force interruption only after the options sheet is confirmed, then follows and cancels progress', async () => {
    let status = statusSnapshot()
    let detail = activeRecord
    serve((request, url) => {
      if (url.pathname === '/api/updates/status') return response(status)
      if (isRequest(request, '/api/updates/resource', 'POST')) return response(activeRecord, 202)
      if (isRequest(request, '/api/updates/update-1', 'DELETE')) {
        detail = { ...activeRecord, status: 'cancelled', phase: 'cancelled' }
        status = statusSnapshot()
        return response(detail, 202)
      }
      // 与真实服务一致：详情接口返回的就是状态里正在运行的那条记录。
      if (url.pathname === '/api/updates/update-1') return response(status.running ?? detail)
      return undefined
    })
    const statusRequests = () => requests.filter((request) => isRequest(request, '/api/updates/status')).length
    renderPage(queryClient)

    await screen.findByRole('heading', { name: '活动资源' })
    fireEvent.click(within(card('活动资源')).getByRole('button', { name: '更新活动资源' }))
    const sheet = await screen.findByRole('dialog', { name: '更新活动资源' })
    expect(within(sheet).getByRole('radio', { name: '全部' })).toHaveAttribute('aria-checked', 'true')
    expect(within(sheet).getByRole('radio', { name: '等待空闲' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(sheet).getByRole('radio', { name: '仅仓库' }))
    expect(within(sheet).queryByText(/流水线会被立即取消/)).not.toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('switch', { name: '允许强制中断正在运行的流水线' }))
    expect(within(sheet).getByText(/流水线会被立即取消/)).toBeInTheDocument()
    expect(requests.some((request) => isRequest(request, '/api/updates/resource', 'POST'))).toBe(false)

    fireEvent.click(within(sheet).getByRole('button', { name: '中断流水线并更新' }))
    await waitForSheetToClose()
    expect(await screen.findByText('20%')).toBeInTheDocument()
    expect(await bodyOf(requests.find((request) => isRequest(request, '/api/updates/resource', 'POST')))).toEqual({
      channel: 'repo', force: false, force_interrupt: true, reload_mode: 'wait',
    })
    expect(requests.some((request) => isRequest(request, '/api/updates/update-1'))).toBe(true)
    expect(screen.getByText('2.0 KB / 10 KB')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '更新进度' })).toHaveAttribute('aria-valuenow', '20')

    const statusReadsBeforeEvent = statusRequests()
    status = statusSnapshot({ running: { ...activeRecord, percent: 64 } })
    await act(async () => {
      handleServerEvent({ type: 'update_progress', data: { update_id: 'update-1', phase: 'downloading', percent: 64 } }, queryClient)
    })
    // WS 事件经 query-bridge 使状态失效，重新读取的 running 项带来新的进度。
    await waitFor(() => expect(statusRequests()).toBeGreaterThan(statusReadsBeforeEvent), { timeout: 3000 })
    expect(await screen.findByText('64%', undefined, { timeout: 3000 })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '取消更新' }))
    const confirm = await screen.findByRole('alertdialog', { name: '取消这次更新？' })
    expect(requests.some((request) => request.method === 'DELETE')).toBe(false)
    fireEvent.click(within(confirm).getByRole('button', { name: '取消更新' }))
    await waitFor(() => expect(requests.some((request) => isRequest(request, '/api/updates/update-1', 'DELETE'))).toBe(true))
  })

  it('does not submit anything when the options sheet is dismissed, and resets force interruption on reopen', async () => {
    renderPage(queryClient)

    await screen.findByRole('heading', { name: 'MAA 内核' })
    fireEvent.click(within(card('MAA 内核')).getByRole('button', { name: '更新至 6.18.0' }))
    let sheet = await screen.findByRole('dialog', { name: '更新 MAA 内核' })
    fireEvent.click(within(sheet).getByRole('switch', { name: '允许强制中断正在运行的流水线' }))
    fireEvent.click(within(sheet).getByRole('button', { name: '取消' }))
    await waitForSheetToClose()

    fireEvent.click(within(card('MAA 内核')).getByRole('button', { name: '更新至 6.18.0' }))
    sheet = await screen.findByRole('dialog', { name: '更新 MAA 内核' })
    expect(within(sheet).getByRole('switch', { name: '允许强制中断正在运行的流水线' })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(within(sheet).getByRole('button', { name: '关闭' }))
    await waitForSheetToClose()

    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('asks before cancelling a running download and keeps it running when the user backs out', async () => {
    serve((request, url) => {
      if (url.pathname === '/api/updates/status') return response(statusSnapshot({ running: activeRecord }))
      if (isRequest(request, '/api/updates/update-1')) return response(activeRecord)
      if (isRequest(request, '/api/updates/update-1', 'DELETE')) return response({ ...activeRecord, status: 'cancelled' }, 202)
      return undefined
    })
    renderPage(queryClient)

    const cancel = await screen.findByRole('button', { name: '取消更新' })
    expect(screen.getByRole('heading', { name: '正在更新活动资源' })).toBeInTheDocument()
    expect(screen.getByText(/共用一条更新队列/)).toBeInTheDocument()
    expect(within(card('活动资源')).getByRole('button', { name: '更新进行中' })).toBeDisabled()
    expect(within(card('MAA 内核')).getByRole('button', { name: '更新至 6.18.0' })).toBeDisabled()

    fireEvent.click(cancel)
    const dialog = await screen.findByRole('alertdialog', { name: '取消这次更新？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '继续更新' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(requests.some((request) => request.method === 'DELETE')).toBe(false)
    expect(screen.getByText('20%')).toBeInTheDocument()
  })

  it('U3 opens a failed record with its error code and message and retries it', async () => {
    serve((request, url) => {
      if (url.pathname === '/api/updates') return response({ items: [failedRecord], total: 21, page: 1, size: 20 })
      if (isRequest(request, '/api/updates/failed-1')) return response(failedRecord)
      if (isRequest(request, '/api/updates/failed-1/retry', 'POST')) return response(activeRecord, 202)
      if (url.pathname === '/api/updates/update-1') return response(activeRecord)
      return undefined
    })
    renderPage(queryClient)

    expect(await screen.findByText('6.17.5 → 6.18.0')).toBeInTheDocument()
    expect(screen.getByText('显示最近 1 条 / 共 21 条')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /MAA 内核.*失败/ }))
    const sheet = await screen.findByRole('dialog', { name: '更新详情' })
    expect(within(sheet).getByText('failed-1')).toBeInTheDocument()
    expect(within(sheet).getByText('CHECKSUM_MISMATCH')).toBeInTheDocument()
    expect(within(sheet).getByText('下载文件校验失败')).toBeInTheDocument()
    expect(within(sheet).getByText(/checksum mismatch/)).toBeInTheDocument()

    fireEvent.click(within(sheet).getByRole('button', { name: '重试更新' }))
    await waitForSheetToClose()
    expect(await screen.findByText('20%')).toBeInTheDocument()
    const retry = requests.find((request) => new URL(request.url).pathname === '/api/updates/failed-1/retry')
    expect(retry?.method).toBe('POST')
  })

  it('U4 keeps stable core installation available when the running core version is unknown', async () => {
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
    serve((request, url) => {
      if (url.pathname === '/api/updates/status') return response(unavailableCore)
      if (isRequest(request, '/api/updates/core', 'POST')) return response(accepted, 202)
      if (url.pathname === '/api/updates/core-update-1') return response(accepted)
      return undefined
    })
    renderPage(queryClient)

    expect(await screen.findByText('内核检查失败：内核未就绪')).toBeInTheDocument()
    expect(within(card('MAA 内核')).getByText('检查失败')).toBeInTheDocument()
    const install = within(card('MAA 内核')).getByRole('button', { name: '安装 stable 内核' })
    expect(install).toBeEnabled()
    fireEvent.click(install)
    const sheet = await screen.findByRole('dialog', { name: '更新 MAA 内核' })
    fireEvent.click(within(sheet).getByRole('button', { name: '安装 stable 内核' }))

    await waitFor(() => expect(requests.some((request) => isRequest(request, '/api/updates/core', 'POST'))).toBe(true))
    expect(await bodyOf(requests.find((request) => isRequest(request, '/api/updates/core', 'POST')))).toEqual({
      channel: 'stable', force: false, force_interrupt: false,
    })
  })

  it('installs the game channel chosen on its card', async () => {
    const accepted: UpdateRecord = { id: 'game-update-1', target: 'game', status: 'pending', phase: 'checking' }
    serve((request, url) => {
      if (isRequest(request, '/api/updates/game', 'POST')) return response(accepted, 202)
      if (url.pathname === '/api/updates/game-update-1') return response(accepted)
      return undefined
    })
    renderPage(queryClient)

    await screen.findByRole('heading', { name: '游戏本体' })
    const game = card('游戏本体')
    fireEvent.click(within(game).getByRole('radio', { name: 'B 服' }))
    expect(within(game).getByText('2.7.72')).toBeInTheDocument()
    fireEvent.click(within(game).getByRole('button', { name: '下载并安装' }))
    const sheet = await screen.findByRole('dialog', { name: '更新游戏本体' })
    expect(within(sheet).getByText(/B 服/)).toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('button', { name: '下载并安装' }))

    await waitFor(() => expect(requests.some((request) => isRequest(request, '/api/updates/game', 'POST'))).toBe(true))
    expect(await bodyOf(requests.find((request) => isRequest(request, '/api/updates/game', 'POST')))).toEqual({
      channel: 'Bilibili', force: false, force_interrupt: false,
    })
  })
})

describe('update progress', () => {
  afterEach(() => cleanup())

  it('U5 shows the API byte-done field as "1.0 KB / 4.0 KB"', () => {
    render(
      <UpdateProgress
        record={{
          ...activeRecord,
          percent: 50,
          bytes_downloaded: undefined,
          bytes_done: 1024,
          bytes_total: 4096,
          bytes_per_second: 2048,
          eta_seconds: 2,
        }}
        onCancel={() => undefined}
      />,
    )

    expect(screen.getByText('1.0 KB / 4.0 KB')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB/秒')).toBeInTheDocument()
    expect(screen.getByText('剩余约 2 秒')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消更新' })).toBeEnabled()
  })

  it('explains that an update cannot be cancelled while it is being applied', () => {
    render(<UpdateProgress record={{ ...activeRecord, phase: 'applying', percent: 90 }} onCancel={() => undefined} />)

    expect(screen.getByText('正在应用更新，这一步无法取消。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消更新' })).toBeDisabled()
  })
})
