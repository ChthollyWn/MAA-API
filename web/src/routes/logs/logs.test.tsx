import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import LogsPage from '@/routes/logs'
import { LogEntry } from '@/features/logs/LogEntry'
import { LogList } from '@/features/logs/LogList'
import { LAST_SEEN_ID_STORAGE_KEY, realtimeClient, useRealtimeStatusStore } from '@/realtime/client'
import type { LogRecord } from '@/realtime/events'
import { useLogBuffer } from '@/stores/log-buffer'

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

function renderPage(path = '/logs') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LogsPage />
    </MemoryRouter>,
  )
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

  it('loads cursor pages newest-first, filters sources locally, and subscribes only for the page lifetime', async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      const query = new URL(request.url).searchParams
      if (query.has('before_id')) return response(history([record(8), record(7)], false))
      return response(history([record(10, 'task', 'task-only'), record(9, 'service', 'service-only')], true, 9))
    })
    const view = renderPage()

    expect(await screen.findByText('task-only')).toBeInTheDocument()
    expect(requests).toHaveLength(1)
    expect(new URL(requests[0]!.url).searchParams.get('order')).toBe('desc')
    expect(new URL(requests[0]!.url).searchParams.get('after_id')).toBeNull()
    expect(subscribeSpy).toHaveBeenCalledTimes(1)
    expect(subscribeSpy).toHaveBeenCalledWith(true)

    fireEvent.mouseDown(screen.getByRole('tab', { name: '任务' }), { button: 0, ctrlKey: false })
    expect(await screen.findByRole('tab', { name: '任务' })).toHaveAttribute('data-state', 'active')
    expect(await screen.findByText('task-only')).toBeInTheDocument()
    expect(screen.queryByText('service-only')).not.toBeInTheDocument()
    expect(subscribeSpy).toHaveBeenCalledTimes(1)
    expect(requests).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '加载更早日志' }))
    await waitFor(() => expect(screen.getByText('log-7')).toBeInTheDocument())
    const older = new URL(requests[1]!.url).searchParams
    expect(older.get('before_id')).toBe('9')
    expect(older.get('order')).toBe('desc')

    view.unmount()
    expect(subscribeSpy).toHaveBeenLastCalledWith(false)
  })

  it('uses the saved last seen cursor to fetch logs missed while the page was not subscribed', async () => {
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
    const catchup = new URL(requests[1]!.url).searchParams
    expect(catchup.get('after_id')).toBe('5')
    expect(catchup.get('order')).toBe('asc')
  })

  it('filters multiple selected levels with local OR semantics and leaves level out of REST params', async () => {
    const rows: LogRecord[] = [
      { ...record(13, 'task', 'debug row'), level: 'DEBUG' },
      { ...record(14, 'task', 'info row'), level: 'INFO' },
      { ...record(15, 'task', 'warning row'), level: 'WARNING' },
      { ...record(16, 'task', 'error row'), level: 'ERROR' },
    ]
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response(history(rows))
    })
    renderPage()

    expect(await screen.findByText('error row')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'ERROR' }))
    fireEvent.click(screen.getByRole('button', { name: 'WARNING' }))

    expect(await screen.findByText('warning row')).toBeInTheDocument()
    expect(screen.getByText('error row')).toBeInTheDocument()
    expect(screen.queryByText('info row')).not.toBeInTheDocument()
    expect(screen.queryByText('debug row')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ERROR' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'WARNING' })).toHaveAttribute('aria-pressed', 'true')
    expect(requests.every((request) => !new URL(request.url).searchParams.has('level'))).toBe(true)
  })

  it('supports the truncated gap action with an after_id and live lower boundary', async () => {
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

    expect(await screen.findByRole('alert')).toHaveTextContent('超出实时缓冲')
    fireEvent.click(screen.getByRole('button', { name: '加载缺失历史日志' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已从历史记录补齐 2 条'))
    const gapQuery = new URL(requests.at(-1)!.url).searchParams
    expect(gapQuery.get('after_id')).toBe('5')
    expect(gapQuery.get('before_id')).toBe('10')
    expect(gapQuery.get('order')).toBe('asc')
  })

  it('shows empty and error states and offers a history retry', async () => {
    renderPage()
    expect(await screen.findByText('暂无匹配的历史或实时日志')).toBeInTheDocument()
    cleanup()

    fetchMock.mockImplementationOnce(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      return response({ error: { code: 'SERVER_ERROR', message: 'history unavailable' } }, 500)
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('历史日志加载失败')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    expect(await screen.findByText('暂无匹配的历史或实时日志')).toBeInTheDocument()
  })

  it('keeps the scroll position when a new log arrives while the reader is away from the bottom', async () => {
    const first = [record(1), record(2)]
    const view = render(<LogList records={first} loading={false} hasMore={false} loadingMore={false} onLoadOlder={async () => undefined} />)
    const scrollRegion = screen.getByRole('region', { name: '日志滚动区域' })
    Object.defineProperty(scrollRegion, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(scrollRegion, 'clientHeight', { configurable: true, value: 400 })
    await waitFor(() => expect(document.querySelector('[data-index]')).toBeInTheDocument())
    scrollRegion.scrollTop = 100
    fireEvent.scroll(scrollRegion)
    const before = scrollRegion.scrollTop

    view.rerender(<LogList records={[...first, record(3)]} loading={false} hasMore={false} loadingMore={false} onLoadOlder={async () => undefined} />)
    expect(await screen.findByRole('button', { name: /1 条新日志/ })).toBeInTheDocument()
    expect(scrollRegion.scrollTop).toBe(before)
  })

  it('virtualizes long lists and measures screenshot rows with lazy thumbnail URLs', async () => {
    const records = Array.from({ length: 2_500 }, (_, index) => record(index + 1, 'core', 'very long line '.repeat(24)))
    render(<LogList records={records} loading={false} hasMore={false} loadingMore={false} onLoadOlder={async () => undefined} />)
    await waitFor(() => expect(document.querySelectorAll('[data-index]').length).toBeGreaterThan(0))
    expect(document.querySelectorAll('[data-index]').length).toBeLessThan(50)

    const hash = 'a'.repeat(64)
    const imageRecord = {
      ...record(3000),
      attachment: {
        kind: 'screenshot',
        sha256: hash,
        thumb_url: `/api/images/${hash}/thumb`,
        full_url: `/api/images/${hash}/full`,
      },
    }
    const image = render(<LogEntry record={imageRecord} />)
    const thumbnail = screen.getByRole('img', { name: '日志截图缩略图' })
    expect(thumbnail).toHaveAttribute('loading', 'lazy')
    expect(thumbnail).toHaveAttribute('src', `/api/images/${hash}/thumb`)
    fireEvent.click(screen.getByRole('button', { name: '查看日志 3000 的截图' }))
    expect(screen.getByRole('dialog', { name: '日志截图原图' }).querySelector('img')).toHaveAttribute('src', `/api/images/${hash}/full`)
    image.unmount()
  })
})
