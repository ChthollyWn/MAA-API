import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Toaster } from '@/components/ui/toaster'
import DemonstrationsPage from './DemonstrationsPage'

type Json = Record<string, unknown>

interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function image(): Response {
  return new Response(new Blob(['jpeg-bytes'], { type: 'image/jpeg' }), {
    headers: { 'Content-Type': 'image/jpeg' },
  })
}

/** jsdom 没有 PointerEvent；pointer 事件用 MouseEvent 派发，React 仍按事件类型分派。 */
function pointer(type: string, clientX: number, clientY: number): MouseEvent {
  return new MouseEvent(type, { bubbles: true, cancelable: true, clientX, clientY, button: 0 })
}

function mockCanvasRect(): void {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    width: 1280,
    height: 720,
    right: 1280,
    bottom: 720,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect)
}

function step(seq: number, overrides: Json = {}): Json {
  return {
    seq,
    action: 'click',
    x: 100 + seq,
    y: 200 + seq,
    x1: null,
    y1: null,
    x2: null,
    y2: null,
    duration_ms: null,
    delay_ms: seq === 1 ? 0 : 1200,
    screen: [1280, 720],
    screenshot_id: `shot-before-${seq}`,
    result_screenshot_id: `shot-after-${seq}`,
    at: '2026-11-10T01:00:00Z',
    ...overrides,
  }
}

function demonstration(overrides: Json & { id: string }): Json {
  return {
    title: '示教 11-10 01:00',
    status: 'recorded',
    steps: [step(1)],
    converted_to: null,
    created_at: '2026-11-10T01:00:00Z',
    updated_at: '2026-11-10T01:00:00Z',
    ...overrides,
  }
}

const compileFixture: Json = {
  entry: 'Custom_Demo_demo0002_1',
  tasks: {
    Custom_Demo_demo0002_1: { algorithm: 'JustReturn', action: 'ClickRect' },
    Custom_Demo_demo0002_2: { algorithm: 'JustReturn', action: 'Swipe' },
    Custom_Demo_demo0002_Done: { algorithm: 'JustReturn', action: 'Stop' },
  },
  chain: ['Custom_Demo_demo0002_1', 'Custom_Demo_demo0002_2', 'Custom_Demo_demo0002_Done'],
  valid: true,
  errors: [],
  chain_errors: [],
  register: false,
  registered: [],
}

const connectedDevice: Json = {
  core_id: 'default',
  state: 'connected',
  address: '127.0.0.1:5555',
  uuid: 'fake-uuid',
  resolution: { width: 1280, height: 720 },
  last_connected_at: null,
  retry: { attempt: 0, max: 3, next_at: null },
  last_error: null,
}

interface ApiOptions {
  demos?: Json[]
  device?: Json
  compile?: Json
  expired?: string[]
  /** 模拟 convert 的 422：details 里的错误数组。 */
  compileError?: Json
}

function installApi(options: ApiOptions = {}) {
  const demos = (options.demos ?? []).map((item) => ({ ...item }))
  const compile = options.compile ?? compileFixture
  const device = options.device ?? connectedDevice
  const requests: RecordedRequest[] = []
  let created = 0

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const path = url.pathname
    const method = request.method
    let body: unknown
    if (method !== 'GET' && method !== 'HEAD') {
      try {
        body = await request.clone().json()
      } catch {
        body = undefined
      }
    }
    requests.push({ method, path, query: url.searchParams, body })

    if (path === '/api/device/status' && method === 'GET') return json(device)
    if (path === '/api/device/screenshot') return image()
    if (path.startsWith('/api/screenshots/')) {
      const id = decodeURIComponent(path.slice('/api/screenshots/'.length))
      if ((options.expired ?? []).includes(id)) {
        return json({ error: { code: 'SCREENSHOT_NOT_FOUND', message: '截图不存在或已过期' } }, 410)
      }
      return image()
    }
    if (path === '/api/agent/demonstrations' && method === 'GET') {
      return json({ items: demos, total: demos.length, page: 1, size: 20 })
    }
    if (path === '/api/agent/demonstrations' && method === 'POST') {
      created += 1
      const row = demonstration({
        id: `demo-new-${created}`,
        title: `示教 11-10 01:0${created}`,
        status: 'recording',
        steps: [],
      })
      demos.unshift(row)
      return json(row, 201)
    }
    const stopMatch = /^\/api\/agent\/demonstrations\/([^/]+)\/stop$/.exec(path)
    if (stopMatch && method === 'POST') {
      const row = demos.find((item) => item.id === stopMatch[1])
      if (!row) return json({ error: { code: 'DEMONSTRATION_NOT_FOUND', message: '示教不存在' } }, 404)
      const discard = Boolean((body as Json | undefined)?.discard)
      const steps = Array.isArray(row.steps) ? row.steps : []
      row.status = discard || steps.length === 0 ? 'discarded' : 'recorded'
      row.updated_at = '2026-11-10T01:05:00Z'
      return json(row)
    }
    const convertMatch = /^\/api\/agent\/demonstrations\/([^/]+)\/convert$/.exec(path)
    if (convertMatch && method === 'POST') {
      if (options.compileError) {
        return json(
          {
            error: {
              code: 'CUSTOM_TASK_INVALID',
              message: '自定义任务链校验失败',
              details: options.compileError,
            },
          },
          422,
        )
      }
      const row = demos.find((item) => item.id === convertMatch[1])
      if (!row) return json({ error: { code: 'DEMONSTRATION_NOT_FOUND', message: '示教不存在' } }, 404)
      const register = Boolean((body as Json | undefined)?.register)
      if (register) {
        row.status = 'converted'
        row.converted_to = {
          entry: compile.entry,
          task_names: compile.chain,
          asset_ids: (compile.chain as string[]).map((_, index) => `asset-${index}`),
          registered_at: '2026-11-10T01:06:00Z',
        }
      }
      return json({
        ...row,
        compile: {
          ...compile,
          register,
          registered: register
            ? (compile.chain as string[]).map((name, index) => ({ id: `asset-${index}`, name }))
            : [],
        },
        converted_to: row.converted_to,
      })
    }
    const gestureMatch = /^\/api\/device\/(click|swipe|long_press)$/.exec(path)
    if (gestureMatch && method === 'POST') return json({ ok: true })
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    demos,
    listCalls: () => requests.filter((item) => item.method === 'GET' && item.path === '/api/agent/demonstrations').length,
    stopCalls: () => requests.filter((item) => /\/stop$/.test(item.path)),
    convertCalls: () => requests.filter((item) => /\/convert$/.test(item.path)),
    gestureCalls: (kind: 'click' | 'swipe' | 'long_press') =>
      requests.filter((item) => item.path === `/api/device/${kind}`),
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [{ path: '/more/demonstrations', element: <DemonstrationsPage /> }],
    { initialEntries: ['/more/demonstrations'] },
  )
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
}

describe('M89 示教页面', () => {
  beforeEach(() => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:device-shot')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('starts_and_stops_recording', async () => {
    const api = installApi({ demos: [] })
    renderPage()

    expect(await screen.findByText('还没有示教')).toBeInTheDocument()
    expect(screen.getByText('在画面里点一下、滑一下，停止后就能生成自定义任务')).toBeInTheDocument()
    const startButton = screen.getByRole('button', { name: '开始示教' })
    await waitFor(() => expect(startButton).toBeEnabled())

    fireEvent.click(startButton)

    await waitFor(() => expect(api.requests.some(
      (item) => item.method === 'POST' && item.path === '/api/agent/demonstrations',
    )).toBe(true))
    expect(await screen.findByText('正在示教。这里的点击与滑动会直接操作设备，手机上同时操作会互相抢。')).toBeInTheDocument()
    expect(screen.getAllByText('录制中').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '停止示教' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '丢弃示教' })).toBeInTheDocument()

    // 录制中已经点了设备：模拟服务端在同一行追加了步骤，停止后应保存为已录制。
    const started = api.demos.find((item) => item.status === 'recording')
    if (started) started.steps = [step(1)]
    const listCallsBeforeStop = api.listCalls()
    fireEvent.click(screen.getByRole('button', { name: '停止示教' }))

    await waitFor(() => expect(api.stopCalls()).toHaveLength(1))
    expect(api.stopCalls()[0]?.body).toEqual({ discard: false })
    expect(await screen.findByText('示教已保存')).toBeInTheDocument()
    expect(screen.getAllByText('已录制').length).toBeGreaterThan(0)
    await waitFor(() => expect(api.listCalls()).toBeGreaterThan(listCallsBeforeStop))
  })

  it('preview_then_register_requires_confirmation', async () => {
    const api = installApi({
      demos: [demonstration({ id: 'demo-0002', steps: [step(1), step(2)] })],
    })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '预览任务' }))
    await waitFor(() => expect(api.convertCalls()).toHaveLength(1))
    expect(api.convertCalls()[0]?.body).toEqual({ register: false })
    // 预览只编译，不弹确认。
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(await screen.findByText('任务链')).toBeInTheDocument()
    expect(screen.getAllByText('Custom_Demo_demo0002_Done').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: '注册任务' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('注册为自定义任务？')).toBeInTheDocument()
    expect(within(dialog).getByText('会写入 3 个任务节点并重载资源')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '注册任务' }))
    await waitFor(() => expect(api.convertCalls()).toHaveLength(2))
    expect(api.convertCalls()[1]?.body).toEqual({ register: true })
    expect(await screen.findByText('已注册 3 个任务')).toBeInTheDocument()
    expect(screen.getAllByText('已转换').length).toBeGreaterThan(0)
  })

  it('maps_pointer_to_device_pixels_and_does_not_repeat_click_after_long_press', async () => {
    const api = installApi({
      demos: [demonstration({ id: 'demo-live', status: 'recording', steps: [], title: '示教 11-10 02:00' })],
    })
    mockCanvasRect()
    renderPage()

    const canvas = await screen.findByTestId('live-canvas')
    // 等设备状态与第一帧画面就绪：分辨率未知时画布不接收手势。
    await screen.findByRole('img', { name: '设备画面' })

    fireEvent(canvas, pointer('pointerdown', 640, 360))
    fireEvent(canvas, pointer('pointerup', 640, 360))
    await waitFor(() => expect(api.gestureCalls('click')).toHaveLength(1))
    expect(api.gestureCalls('click')[0]?.body).toEqual({ x: 640, y: 360, force: false })

    fireEvent(canvas, pointer('pointerdown', 100, 100))
    fireEvent(canvas, pointer('pointermove', 300, 100))
    fireEvent(canvas, pointer('pointerup', 300, 100))
    await waitFor(() => expect(api.gestureCalls('swipe')).toHaveLength(1))
    const swipe = api.gestureCalls('swipe')[0]?.body as Json
    expect(swipe.x1).toBe(100)
    expect(swipe.y1).toBe(100)
    expect(swipe.x2).toBe(300)
    expect(swipe.y2).toBe(100)
    expect(Number(swipe.duration_ms)).toBeGreaterThanOrEqual(100)
    expect(Number(swipe.duration_ms)).toBeLessThanOrEqual(2000)

    fireEvent(canvas, pointer('pointerdown', 320, 180))
    await waitFor(() => expect(api.gestureCalls('long_press')).toHaveLength(1), { timeout: 2_000 })
    const longPress = api.gestureCalls('long_press')[0]?.body as Json
    expect(longPress.x).toBe(320)
    expect(longPress.y).toBe(180)
    expect(Number(longPress.duration_ms)).toBeGreaterThanOrEqual(600)
    expect(Number(longPress.duration_ms)).toBeLessThanOrEqual(3000)
    fireEvent(canvas, pointer('pointerup', 320, 180))
    await new Promise((resolve) => setTimeout(resolve, 80))

    expect(api.gestureCalls('click')).toHaveLength(1)
  })

  it('cancels_pending_long_press_when_unmounted', async () => {
    const api = installApi({
      demos: [demonstration({ id: 'demo-live', status: 'recording', steps: [], title: '示教 11-10 02:10' })],
    })
    mockCanvasRect()
    const view = renderPage()

    const canvas = await screen.findByTestId('live-canvas')
    await screen.findByRole('img', { name: '设备画面' })
    fireEvent(canvas, pointer('pointerdown', 320, 180))
    // 长按定时器还没到点就离开页面：不能再发 long_press。
    view.unmount()
    await new Promise((resolve) => setTimeout(resolve, 800))

    expect(api.gestureCalls('long_press')).toHaveLength(0)
  })

  it('shows_convert_errors_in_preview_and_disables_register', async () => {
    installApi({
      demos: [demonstration({ id: 'demo-bad', steps: [step(1)] })],
      compileError: {
        errors: [
          { task: 'Custom_Demo_demo-bad_1', field: 'specificRect', reason: '矩形超出 1280×720' },
        ],
        chain_errors: [
          { field: 'chain', reason: '任务链没有 Stop 出口' },
        ],
      },
    })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '预览任务' }))

    expect(await screen.findByText('校验未通过')).toBeInTheDocument()
    expect(screen.getByText(/矩形超出 1280×720/)).toBeInTheDocument()
    expect(screen.getByText(/任务链没有 Stop 出口/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '注册任务' })).toBeDisabled()
  })

  it('shows_expired_screenshot_placeholder', async () => {
    installApi({
      demos: [demonstration({
        id: 'demo-expired',
        steps: [step(1, { screenshot_id: 'expired-shot', result_screenshot_id: 'shot-after-1' })],
      })],
      expired: ['expired-shot'],
    })
    renderPage()

    expect(await screen.findByText('截图已过期')).toBeInTheDocument()
    expect(screen.getByText('只保留 7 天，不影响已注册的任务')).toBeInTheDocument()
  })

  it('disables_start_when_device_is_not_connected', async () => {
    installApi({
      demos: [],
      device: { ...connectedDevice, state: 'disconnected', resolution: null },
    })
    renderPage()

    expect(await screen.findByText('设备未连接')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始示教' })).toBeDisabled()
  })
})
