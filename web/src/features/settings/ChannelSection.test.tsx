import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ChannelSection } from './ChannelSection'

interface RecordedRequest {
  method: string
  path: string
}

interface ApiOptions {
  /** 让生成绑定码的响应挂起，用来断言请求中的按钮禁用。 */
  bindCodeGate?: Promise<void>
}

const CHANNELS = [
  { platform: 'telegram', enabled: true, configured: true, connected: true },
  { platform: 'feishu', enabled: false, configured: false, connected: false },
  { platform: 'dingtalk', enabled: true, configured: false, connected: null },
  { platform: 'qq', enabled: false, configured: false, connected: null },
]

const BINDING = {
  id: 'binding-1',
  platform: 'telegram',
  external_user_id: 'tg-user-1',
  external_chat_id: 'tg-chat-1',
  display_name: '小明',
  status: 'active',
  bound_at: '2026-11-05T10:00:00Z',
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApi(options: ApiOptions = {}) {
  const requests: RecordedRequest[] = []
  let bindings = [BINDING]
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push({ method: request.method, path: url.pathname })
    if (url.pathname === '/api/agent/channels' && request.method === 'GET') return json(CHANNELS)
    if (url.pathname === '/api/agent/channels/bindings' && request.method === 'GET') return json({ items: bindings })
    if (url.pathname === '/api/agent/channels/telegram/bind-codes' && request.method === 'POST') {
      await options.bindCodeGate
      return json({ code: 'AB12CD34', expires_at: '2026-11-05T10:10:00Z' }, 201)
    }
    if (url.pathname === '/api/agent/channels/bindings/binding-1' && request.method === 'DELETE') {
      bindings = []
      return new Response(null, { status: 204 })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests }
}

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <ChannelSection />
    </QueryClientProvider>,
  )
  return queryClient
}

/** jsdom 没有 ResizeObserver；Radix 的对话框会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('IM 通道', () => {
  it('shows the four platforms with their state and the bound list', async () => {
    installApi()
    renderSection()

    expect(await screen.findByText('Telegram')).toBeInTheDocument()
    expect(screen.getByText('飞书')).toBeInTheDocument()
    expect(screen.getByText('钉钉')).toBeInTheDocument()
    expect(screen.getByText('QQ')).toBeInTheDocument()
    expect(screen.getByText('已启用，已连接')).toBeInTheDocument()
    expect(screen.getAllByText('已停用')).toHaveLength(2)
    expect(screen.getByText('已启用，缺少凭证')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '生成绑定码' })).toHaveLength(4)

    expect(await screen.findByText('小明')).toBeInTheDocument()
    expect(screen.getByText(/Telegram · tg-user-1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '解除' })).toBeInTheDocument()
  })

  it('generates a bind code and disables the buttons while the request is running', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => { release = resolve })
    const api = installApi({ bindCodeGate: gate })
    renderSection()

    await screen.findByText('已启用，已连接')
    fireEvent.click(screen.getAllByRole('button', { name: '生成绑定码' })[0])

    const buttons = screen.getAllByRole('button', { name: '生成绑定码' })
    await waitFor(() => expect(buttons.every((button) => button.hasAttribute('disabled'))).toBe(true))
    expect(buttons[0]).toHaveAttribute('aria-busy', 'true')

    release()
    expect(await screen.findByText('绑定码 AB12CD34')).toBeInTheDocument()
    expect(screen.getByText(/10 分钟内有效/)).toBeInTheDocument()
    expect(api.requests).toContainEqual({ method: 'POST', path: '/api/agent/channels/telegram/bind-codes' })
    await waitFor(() => expect(screen.getAllByRole('button', { name: '生成绑定码' }).every((button) => !button.hasAttribute('disabled'))).toBe(true))
  })

  it('revokes a binding after confirmation and refreshes the list', async () => {
    const api = installApi()
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: '解除' }))
    const dialog = await screen.findByRole('alertdialog', { name: '解除「小明」的绑定？' })
    expect(api.requests.some((request) => request.method === 'DELETE')).toBe(false)
    fireEvent.click(within(dialog).getByRole('button', { name: '解除绑定' }))

    await waitFor(() => expect(api.requests).toContainEqual({ method: 'DELETE', path: '/api/agent/channels/bindings/binding-1' }))
    expect(await screen.findByText('还没有绑定')).toBeInTheDocument()
    expect(screen.queryByText('小明')).not.toBeInTheDocument()
  })

  it('shows a retryable error when the channel list cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => json({ error: { code: 'SERVICE_UNAVAILABLE', message: '通道服务尚未启动' } }, 503)))
    renderSection()

    expect(await screen.findByText('IM 通道读取失败')).toBeInTheDocument()
    expect(screen.getAllByText('通道服务尚未启动').length).toBeGreaterThan(0)
  })
})
