import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import LogsPage from '@/routes/logs'
import { LogList } from '@/features/logs/LogList'
import { copyText } from '@/lib/clipboard'
import { LAST_SEEN_ID_STORAGE_KEY, realtimeClient, useRealtimeStatusStore } from '@/realtime/client'
import type { LogRecord } from '@/realtime/events'
import { useLogBuffer } from '@/stores/log-buffer'

vi.mock('@/lib/clipboard', () => ({ copyText: vi.fn(async () => true) }))

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function history(items: LogRecord[], hasMore = false, cursor: number | null = null) {
  return { items, page: { next_cursor: cursor, has_more: hasMore, limit: 100 } }
}

function record(id: number, source: string = 'task', content = `log-${id}`): LogRecord {
  return { id, source, level: 'INFO', content, ts: id }
}

function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <span data-testid="location">{location.pathname}</span>
      <button type="button" onClick={() => navigate(-1)}>浏览器后退</button>
    </>
  )
}

function renderPage(path = '/logs') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LogsPage />
      <LocationProbe />
    </MemoryRouter>,
  )
}

function listProps(records: LogRecord[]) {
  return { records, loading: false, hasMore: false, loadingMore: false, onLoadOlder: async () => undefined }
}

async function openFilters() {
  fireEvent.click(screen.getByRole('button', { name: /^筛选/ }))
  return screen.findByRole('dialog', { name: '筛选日志' })
}

async function closeFilters(sheet: HTMLElement) {
  fireEvent.click(within(sheet).getByRole('button', { name: '完成' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

function searchParamsOf(request: Request) {
  return new URL(request.url).searchParams
}

describe('logs page', () => {
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
  let requests: Request[]
  let subscribeSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    localStorage.clear()
    requests = []
    useLogBuffer.setState({ bySource: {} })
    useRealtimeStatusStore.getState().setStatus({
      status: 'CONNECTED', reconnectAttempt: 0, rtt: null, truncated: false, closeCode: null, error: null,
    })
    subscribeSpy = vi.spyOn(realtimeClient, 'setLogSubscription').mockImplementation(() => undefined)
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('aria-label') === '日志滚动区域') {
        return {
          x: 0, y: 0, top: 0, left: 0, right: 640, bottom: 480, width: 640, height: 480,
          toJSON: () => ({}),
        } as DOMRect
      }
      if (this.hasAttribute('data-index')) {
        return {
          x: 0, y: 0, top: 0, left: 0, right: 640, bottom: 84, width: 640, height: 84,
          toJSON: () => ({}),
        } as DOMRect
      }
      return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) } as DOMRect
    })
    fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history([]))
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('G1 loads cursor pages newest-first, filters sources locally through the route, and subscribes only for the page lifetime', async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const query = new URL(request.url).searchParams
      if (query.has('before_id')) return response(history([record(8), record(7)], false))
      return response(history([record(10, 'task', 'task-only'), record(9, 'service', 'service-only')], true, 9))
    })
    const view = renderPage()

    expect(await screen.findByText('task-only')).toBeInTheDocument()
    expect(screen.getByText('service-only')).toBeInTheDocument()
    expect(requests).toHaveLength(1)
    expect(searchParamsOf(requests[0]!).get('order')).toBe('desc')
    expect(searchParamsOf(requests[0]!).get('after_id')).toBeNull()
    expect(subscribeSpy).toHaveBeenCalledTimes(1)
    expect(subscribeSpy).toHaveBeenCalledWith(true)
    expect(screen.getByRole('radio', { name: '全部' })).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(screen.getByRole('radio', { name: '任务' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/logs/task'))
    expect(screen.getByRole('radio', { name: '任务' })).toHaveAttribute('aria-checked', 'true')
    expect(await screen.findByText('task-only')).toBeInTheDocument()
    expect(screen.queryByText('service-only')).not.toBeInTheDocument()
    expect(subscribeSpy).toHaveBeenCalledTimes(1)
    expect(requests).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '加载更早日志' }))
    await waitFor(() => expect(screen.getByText('log-7')).toBeInTheDocument())
    const older = searchParamsOf(requests[1]!)
    expect(older.get('before_id')).toBe('9')
    expect(older.get('order')).toBe('desc')

    fireEvent.click(screen.getByRole('button', { name: '浏览器后退' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/logs$/))
    expect(screen.getByRole('radio', { name: '全部' })).toHaveAttribute('aria-checked', 'true')
    expect(await screen.findByText('service-only')).toBeInTheDocument()

    view.unmount()
    expect(subscribeSpy).toHaveBeenLastCalledWith(false)
  })

  it('G1 opens a source directly from its route', async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history([record(3, 'core', 'core-row'), record(2, 'task', 'task-row')]))
    })
    renderPage('/logs/core')

    expect(await screen.findByText('core-row')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '内核' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByText('task-row')).not.toBeInTheDocument()
  })

  it('G2 uses the saved last seen cursor to fetch logs missed while the page was not subscribed', async () => {
    localStorage.setItem(LAST_SEEN_ID_STORAGE_KEY, '5')
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const query = new URL(request.url).searchParams
      if (query.get('order') === 'asc') return response(history([record(6), record(7)], false))
      return response(history([record(20)], false))
    })
    renderPage()

    expect(await screen.findByText('log-7')).toBeInTheDocument()
    await waitFor(() => expect(requests).toHaveLength(2))
    const catchup = searchParamsOf(requests[1]!)
    expect(catchup.get('after_id')).toBe('5')
    expect(catchup.get('order')).toBe('asc')
  })

  it('G3 filters multiple levels chosen in the filter sheet with local OR semantics and leaves level out of REST params', async () => {
    const rows: LogRecord[] = [
      { ...record(13, 'task', 'debug row'), level: 'DEBUG' },
      { ...record(14, 'task', 'info row'), level: 'INFO' },
      { ...record(15, 'task', 'warning row'), level: 'WARN' },
      { ...record(16, 'task', 'error row'), level: 'ERROR' },
    ]
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history(rows))
    })
    renderPage()

    expect(await screen.findByText('error row')).toBeInTheDocument()
    const sheet = await openFilters()
    expect(within(sheet).getByRole('button', { name: '全部级别' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(sheet).getByRole('button', { name: 'ERROR' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'WARNING' }))
    expect(within(sheet).getByRole('button', { name: 'ERROR' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(sheet).getByRole('button', { name: 'WARNING' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(sheet).getByRole('button', { name: '全部级别' })).toHaveAttribute('aria-pressed', 'false')
    await closeFilters(sheet)

    expect(await screen.findByText('warning row')).toBeInTheDocument()
    expect(screen.getByText('error row')).toBeInTheDocument()
    expect(screen.queryByText('info row')).not.toBeInTheDocument()
    expect(screen.queryByText('debug row')).not.toBeInTheDocument()
    expect(requests.every((request) => !searchParamsOf(request).has('level'))).toBe(true)

    const reopened = await openFilters()
    fireEvent.click(within(reopened).getByRole('button', { name: '全部级别' }))
    await closeFilters(reopened)
    expect(await screen.findByText('info row')).toBeInTheDocument()
    expect(screen.getByText('debug row')).toBeInTheDocument()
  })

  it('G4 supports the truncated gap action with an after_id and live lower boundary', async () => {
    localStorage.setItem(LAST_SEEN_ID_STORAGE_KEY, '5')
    useRealtimeStatusStore.getState().setStatus({ truncated: true })
    useLogBuffer.setState({ bySource: { task: [record(10)] } })
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const query = new URL(request.url).searchParams
      if (query.has('before_id')) return response(history([record(7), record(8)], false))
      if (query.get('order') === 'asc') return response(history([record(6), record(7), record(8)], false))
      return response(history([record(9)], false))
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('断线期间的日志可能没有全部收到')
    fireEvent.click(screen.getByRole('button', { name: '加载缺失历史日志' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已从历史记录补齐 2 条'))
    expect(screen.queryByRole('button', { name: '加载缺失历史日志' })).not.toBeInTheDocument()
    const gapQuery = searchParamsOf(requests.at(-1)!)
    expect(gapQuery.get('after_id')).toBe('5')
    expect(gapQuery.get('before_id')).toBe('10')
    expect(gapQuery.get('order')).toBe('asc')
  })

  it('G5 shows empty and error states and offers a history retry', async () => {
    renderPage()
    expect(await screen.findByText('还没有匹配的日志')).toBeInTheDocument()
    cleanup()

    fetchMock.mockImplementationOnce(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response({ error: { code: 'SERVER_ERROR', message: 'history unavailable' } }, 500)
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('历史日志加载失败')
    expect(screen.getByRole('alert')).toHaveTextContent('history unavailable')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    expect(await screen.findByText('还没有匹配的日志')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('G5 keeps the offline notice that only loaded history is shown', async () => {
    useRealtimeStatusStore.getState().setStatus({ status: 'OFFLINE', error: '访问 token 已失效，请重新登录' })
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history([record(4)]))
    })
    renderPage()

    expect(await screen.findByText('log-4')).toBeInTheDocument()
    const notice = screen.getAllByRole('status').find((element) => element.textContent?.includes('当前显示已加载的历史记录'))
    expect(notice).toBeDefined()
    expect(notice).toHaveTextContent('访问 token 已失效，请重新登录')
  })

  it('G6 keeps the scroll position when a new log arrives while the reader is away from the bottom, and follows while at the bottom', async () => {
    const first = [record(1), record(2)]
    const view = render(<LogList {...listProps(first)} />)
    const scrollRegion = screen.getByRole('region', { name: '日志滚动区域' })
    Object.defineProperty(scrollRegion, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(scrollRegion, 'clientHeight', { configurable: true, value: 400 })
    await waitFor(() => expect(document.querySelector('[data-index]')).toBeInTheDocument())
    // LogList's first animation frame pins the initial view to the newest row;
    // wait for that initialization before simulating the user scrolling upward.
    await waitFor(() => expect(scrollRegion.scrollTop).toBe(scrollRegion.scrollHeight))
    scrollRegion.scrollTop = 100
    fireEvent.scroll(scrollRegion)
    const before = scrollRegion.scrollTop

    view.rerender(<LogList {...listProps([...first, record(3)])} />)
    const pill = await screen.findByRole('button', { name: /1 条新日志/ })
    expect(scrollRegion.scrollTop).toBe(before)

    fireEvent.click(pill)
    expect(scrollRegion.scrollTop).toBe(1000)
    await waitFor(() => expect(screen.queryByRole('button', { name: /条新日志/ })).not.toBeInTheDocument())

    Object.defineProperty(scrollRegion, 'scrollHeight', { configurable: true, value: 1200 })
    view.rerender(<LogList {...listProps([...first, record(3), record(4)])} />)
    await waitFor(() => expect(scrollRegion.scrollTop).toBe(1200))
    expect(screen.queryByRole('button', { name: /条新日志/ })).not.toBeInTheDocument()
  })

  it('G7 virtualizes long lists', async () => {
    const records = Array.from({ length: 2_500 }, (_, index) => record(index + 1, 'core', 'very long line '.repeat(24)))
    render(<LogList {...listProps(records)} />)
    await waitFor(() => expect(document.querySelectorAll('[data-index]').length).toBeGreaterThan(0))
    expect(document.querySelectorAll('[data-index]').length).toBeLessThan(50)
  })

  it('G7 lazy-loads screenshot thumbnails and opens the original in a viewer that closes by Escape, backdrop or button', async () => {
    const hash = 'a'.repeat(64)
    const imageRecord: LogRecord = {
      ...record(3000),
      attachment: {
        kind: 'screenshot',
        sha256: hash,
        thumb_url: `/api/images/${hash}/thumb`,
        full_url: `/api/images/${hash}/full`,
      },
    }
    render(<LogList {...listProps([imageRecord])} />)

    const thumbnail = await screen.findByRole('img', { name: '日志截图缩略图' })
    expect(thumbnail).toHaveAttribute('loading', 'lazy')
    expect(thumbnail).toHaveAttribute('src', `/api/images/${hash}/thumb`)

    fireEvent.click(screen.getByRole('button', { name: '查看日志 3000 的截图' }))
    const viewer = await screen.findByRole('dialog', { name: '日志截图原图' })
    expect(within(viewer).getByRole('img', { name: '日志截图原图' })).toHaveAttribute('src', `/api/images/${hash}/full`)
    fireEvent.keyDown(viewer, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '查看日志 3000 的截图' }))
    fireEvent.click(await screen.findByRole('dialog', { name: '日志截图原图' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '查看日志 3000 的截图' }))
    const again = await screen.findByRole('dialog', { name: '日志截图原图' })
    fireEvent.click(within(again).getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('G8 shows active filters as removable chips and clears them all', async () => {
    const now = Math.floor(Date.now() / 1000)
    const rows: LogRecord[] = [
      { ...record(21, 'task', 'debug row'), level: 'DEBUG', ts: now - 40 },
      { ...record(22, 'task', 'info row'), level: 'INFO', ts: now - 30 },
      { ...record(23, 'task', 'warning row'), level: 'WARNING', ts: now - 20 },
      { ...record(24, 'task', 'error row'), level: 'ERROR', ts: now - 10 },
    ]
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history(rows))
    })
    renderPage()
    expect(await screen.findByText('info row')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: '生效的筛选条件' })).not.toBeInTheDocument()

    const sheet = await openFilters()
    fireEvent.click(within(sheet).getByRole('button', { name: 'ERROR' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'WARNING' }))
    fireEvent.click(within(sheet).getByRole('button', { name: '最近 1 小时' }))
    expect(within(sheet).getByRole('button', { name: '最近 1 小时' })).toHaveAttribute('aria-pressed', 'true')
    await closeFilters(sheet)

    const conditions = screen.getByRole('list', { name: '生效的筛选条件' })
    expect(within(conditions).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['WARNING', 'ERROR', '最近 1 小时'])
    expect(screen.getByRole('button', { name: '筛选，已生效 3 项' })).toBeInTheDocument()
    await waitFor(() => {
      const since = Number(searchParamsOf(requests.at(-1)!).get('since'))
      expect(since).toBeGreaterThanOrEqual(now - 3_600 - 120)
      expect(since).toBeLessThanOrEqual(now - 3_600 + 60)
    })
    expect(await screen.findByText('warning row')).toBeInTheDocument()
    expect(screen.getByText('error row')).toBeInTheDocument()
    expect(screen.queryByText('info row')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '移除筛选条件：ERROR' }))
    await waitFor(() => expect(screen.queryByText('error row')).not.toBeInTheDocument())
    expect(await screen.findByText('warning row')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '筛选，已生效 2 项' })).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: '生效的筛选条件' })).getAllByRole('listitem').map((item) => item.textContent))
      .toEqual(['WARNING', '最近 1 小时'])

    fireEvent.click(screen.getByRole('button', { name: '清除全部' }))
    expect(screen.queryByRole('list', { name: '生效的筛选条件' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '筛选' })).toBeInTheDocument()
    expect(await screen.findByText('info row')).toBeInTheDocument()
    expect(screen.getByText('debug row')).toBeInTheDocument()
    await waitFor(() => expect(searchParamsOf(requests.at(-1)!).has('since')).toBe(false))
  })

  it('G8 converts a custom start time from local time and clears it from the sheet', async () => {
    renderPage()
    expect(await screen.findByText('还没有匹配的日志')).toBeInTheDocument()

    const sheet = await openFilters()
    fireEvent.change(within(sheet).getByLabelText('开始时间'), { target: { value: '2026-09-27T10:00' } })
    expect(within(sheet).getByRole('button', { name: '不限时间' })).toHaveAttribute('aria-pressed', 'false')
    await waitFor(() => expect(searchParamsOf(requests.at(-1)!).get('since')).toBe(String(new Date('2026-09-27T10:00').getTime() / 1000)))
    expect(searchParamsOf(requests.at(-1)!).has('until')).toBe(false)

    fireEvent.click(within(sheet).getByRole('button', { name: '清除' }))
    expect(within(sheet).getByLabelText('开始时间')).toHaveValue('')
    expect(within(sheet).getByRole('button', { name: '不限时间' })).toHaveAttribute('aria-pressed', 'true')
    await closeFilters(sheet)
    expect(screen.queryByRole('list', { name: '生效的筛选条件' })).not.toBeInTheDocument()
    await waitFor(() => expect(searchParamsOf(requests.at(-1)!).has('since')).toBe(false))
  })

  it('searches content locally and sends the keyword to the history query', async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history([record(31, 'task', 'Alpha BOOM'), record(32, 'task', 'beta')]))
    })
    renderPage()
    expect(await screen.findByText('beta')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索日志' }), { target: { value: 'boom' } })
    await waitFor(() => expect(screen.queryByText('beta')).not.toBeInTheDocument())
    expect(screen.getByText('Alpha BOOM')).toBeInTheDocument()
    await waitFor(() => expect(requests.some((request) => searchParamsOf(request).get('q') === 'boom')).toBe(true))

    fireEvent.click(screen.getByRole('button', { name: '清除搜索' }))
    expect(screen.getByRole('searchbox', { name: '搜索日志' })).toHaveValue('')
    expect(await screen.findByText('beta')).toBeInTheDocument()
  })

  it('expands a row to show the full text and logger, and copies it', async () => {
    const detailed: LogRecord = {
      ...record(40, 'service', 'first line\nsecond line\nthird line'),
      level: 'ERROR',
      logger: 'maa_api.core.session',
    }
    render(<LogList {...listProps([detailed])} />)

    const row = await screen.findByRole('button', { name: /first line/ })
    expect(row).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument()
    expect(screen.queryByText('maa_api.core.session')).not.toBeInTheDocument()

    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('maa_api.core.session')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => expect(copyText).toHaveBeenCalledTimes(1))
    const copied = vi.mocked(copyText).mock.calls[0]![0]
    expect(copied).toContain('ERROR')
    expect(copied).toContain('maa_api.core.session')
    expect(copied).toContain('first line\nsecond line\nthird line')
    expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument()

    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: /复制/ })).not.toBeInTheDocument()
  })
})
