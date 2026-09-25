import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { keys } from '@/api/keys'
import DashboardPage from '@/routes/dashboard'
import { useAuth } from '@/stores/auth'

const connectedHealth = {
  status: 'ok',
  version: '2.4.0',
  auth_enabled: true,
  started_at: '2026-09-24T00:00:00Z',
  core: { state: 'ready', pid: 73, generation: 4 },
  device: {
    core_id: 'default',
    state: 'connected',
    address: '127.0.0.1:5555',
    uuid: 'device-1',
    resolution: { width: 1280, height: 720 },
    last_connected_at: null,
    retry: { attempt: 0, max: 0, next_at: null },
    last_error: null,
  },
  queue: { pending: 2, running: 1, paused: false },
}

const runningPipeline = {
  pipeline: {
    id: 'pipeline-1',
    title: '日常任务',
    status: 'running',
    task_count: 2,
    progress: { total: 2, completed: 1, failed: 0 },
    tasks: [
      { id: 'task-1', type_name: 'StartUp', task_name: '启动', status: 'completed', duration_seconds: 8 },
      { id: 'task-2', type_name: 'Fight', task_name: '刷图', status: 'running', duration_seconds: 13 },
    ],
  },
}

interface MockOptions {
  health?: unknown
  pipeline?: unknown
  queue?: unknown
  healthStatus?: number
  pipelineStatus?: number
  screenshotStatus?: number
}

function installFetch(options: MockOptions = {}) {
  let health: unknown = options.health ?? connectedHealth
  let pipeline: unknown = options.pipeline ?? { pipeline: null }
  let healthStatus = options.healthStatus ?? 200
  const requests: Request[] = []
  let screenshotCalls = 0
  let pipelineCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request)
    const url = new URL(request.url)

    if (url.pathname === '/api/system/health') {
      if (healthStatus >= 400) {
        return new Response(JSON.stringify({ error: { code: 'SERVICE_UNAVAILABLE', message: '服务暂不可用' } }), {
          status: healthStatus,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify(health), { headers: { 'Content-Type': 'application/json' } })
    }
    if (url.pathname === '/api/pipelines/current') {
      pipelineCalls += 1
      return new Response(JSON.stringify(typeof pipeline === 'function' ? pipeline(pipelineCalls) : pipeline), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.pathname === '/api/queue') {
      const queue = options.queue ?? {
        running: { pipeline_id: 'pipeline-1' },
        pending: [{ pipeline_id: 'pipeline-2' }, { pipeline_id: 'pipeline-3' }],
        counts: { pending: 2, running: 1 },
        paused: false,
      }
      return new Response(JSON.stringify(queue), { headers: { 'Content-Type': 'application/json' } })
    }
    if (url.pathname === '/api/confirmations' && request.method === 'GET') {
      return new Response(JSON.stringify({ items: [], total: 0, page: 1, size: 20 }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.pathname === '/api/device/reconnect') {
      health = {
        ...connectedHealth,
        ...(typeof health === 'object' && health !== null ? health : {}),
        device: {
          ...(typeof health === 'object' && health !== null && 'device' in health
            ? (health as { device: object }).device
            : {}),
          state: 'connecting',
        },
      }
      return new Response(JSON.stringify({ state: 'connecting' }), { status: 202, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.pathname === '/api/device/screenshot') {
      screenshotCalls += 1
      if ((options.screenshotStatus ?? 200) >= 400) {
        return new Response(JSON.stringify({ error: { code: 'DEVICE_NOT_CONNECTED', message: '设备未连接' } }), {
          status: options.screenshotStatus,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(new Blob(['jpeg-bytes'], { type: 'image/jpeg' }), {
        headers: { 'Content-Type': 'image/jpeg' },
      })
    }
    return new Response(null, { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    requests,
    fetchMock,
    screenshotCount: () => screenshotCalls,
    setHealth(value: unknown) { health = value },
    setHealthStatus(value: number) { healthStatus = value },
    setPipeline(value: unknown) { pipeline = value },
  }
}

function renderDashboard(initial?: { health: unknown; pipeline: unknown; queue: unknown }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  if (initial) {
    queryClient.setQueryData<unknown>(keys.system.health(), initial.health)
    queryClient.setQueryData<unknown>(keys.pipelines.current(), initial.pipeline)
    queryClient.setQueryData<unknown>(keys.queue.all(), initial.queue)
  }
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('dashboard route', () => {
  beforeEach(() => {
    useAuth.getState().clearToken()
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:device-shot')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows service, core, device and queue details and allows a manual screenshot refresh while idle', async () => {
    const network = installFetch()
    renderDashboard()

    expect(await screen.findByText('版本 2.4.0')).toBeInTheDocument()
    expect(screen.getByText('PID 73 · 世代 4')).toBeInTheDocument()
    expect(screen.getByText('127.0.0.1:5555')).toBeInTheDocument()
    expect(screen.getByText('待处理 2 条 · 正在运行 1 条')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: '当前设备画面' })).toBeInTheDocument()
    expect(network.screenshotCount()).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: '刷新设备截图' }))
    expect(await screen.findByRole('img', { name: '当前设备画面' })).toHaveAttribute('src', 'blob:device-shot')
    expect(network.screenshotCount()).toBe(2)
    const screenshotRequest = network.requests.find((request) => new URL(request.url).pathname === '/api/device/screenshot')
    expect(screenshotRequest?.url).toContain('backend=adb&format=jpeg&size=mobile')
    expect(screenshotRequest?.credentials).toBe('same-origin')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
    cleanup()
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })

  it('renders pipeline steps and an explicit empty state when no pipeline is active', async () => {
    installFetch({ pipeline: runningPipeline })
    renderDashboard()

    expect(await screen.findByText('日常任务')).toBeInTheDocument()
    expect(screen.getByText('启动')).toBeInTheDocument()
    expect(screen.getByText('刷图')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '流水线任务进度' })).toHaveAttribute('aria-valuenow', '1')

    cleanup()
    installFetch({ pipeline: { pipeline: null } })
    renderDashboard()
    expect(await screen.findByText('当前没有运行中的流水线')).toBeInTheDocument()
  })

  it('shows a disconnected screenshot placeholder and lets the user retry device connection', async () => {
    const disconnected = {
      ...connectedHealth,
      device: { ...connectedHealth.device, state: 'disconnected' },
    }
    const network = installFetch({ health: disconnected })
    useAuth.getState().setToken('test-token')
    renderDashboard()

    expect(await screen.findByText('设备尚未连接')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '重新连接设备' })).toBeInTheDocument()
    expect(network.screenshotCount()).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '重新连接设备' }))
    await waitFor(() => expect(network.requests.some((request) => request.url.endsWith('/api/device/reconnect'))).toBe(true))
    const reconnectRequest = network.requests.find((request) => request.url.endsWith('/api/device/reconnect'))
    expect(reconnectRequest?.method).toBe('POST')
    expect(reconnectRequest?.headers.get('X-Token')).toBe('test-token')
    expect(await screen.findByRole('status')).toHaveTextContent('已发送重连请求')
  })

  it('shows a service error with a retry action and recovers when health becomes available', async () => {
    const network = installFetch({ healthStatus: 503 })
    renderDashboard()

    expect(await screen.findByText(/无法连接服务：服务暂不可用/)).toBeInTheDocument()
    network.setHealthStatus(200)
    fireEvent.click(screen.getByRole('button', { name: '重试连接' }))

    await waitFor(() => expect(network.requests.filter((request) => request.url.endsWith('/api/system/health'))).toHaveLength(2))
    expect(await screen.findByText('版本 2.4.0')).toBeInTheDocument()
  })

  it('refreshes screenshots at five second intervals only while the current pipeline is running', async () => {
    vi.useFakeTimers()
    const network = installFetch({ pipeline: runningPipeline })
    renderDashboard({
      health: connectedHealth,
      pipeline: runningPipeline,
      queue: { counts: { pending: 0, running: 1 }, pending: [], running: {}, paused: false },
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
      for (let index = 0; index < 30; index += 1) await Promise.resolve()
    })
    expect(network.screenshotCount()).toBe(1)
    expect(screen.getByText('流水线运行中，每 5 秒更新')).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(network.screenshotCount()).toBe(2)

    network.setPipeline({ pipeline: null })
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(network.screenshotCount()).toBe(3)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(network.screenshotCount()).toBe(3)
    expect(screen.getByText('手动刷新以获取当前画面')).toBeInTheDocument()
  })

  it('takes one initial screenshot while idle and does not poll it every five seconds', async () => {
    vi.useFakeTimers()
    const network = installFetch()
    renderDashboard({
      health: connectedHealth,
      pipeline: { pipeline: null },
      queue: { counts: { pending: 0, running: 0 }, pending: [], running: null, paused: false },
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
      for (let index = 0; index < 30; index += 1) await Promise.resolve()
    })
    expect(network.screenshotCount()).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(network.screenshotCount()).toBe(1)
  })
})
