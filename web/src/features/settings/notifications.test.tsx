import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import SettingsPage from '@/routes/settings'
import { useAuth } from '@/stores/auth'

const push = vi.hoisted(() => ({
  permission: 'default' as 'default' | 'granted' | 'denied' | 'unsupported',
  subscribed: false,
  enablePush: vi.fn(),
  disablePush: vi.fn(),
}))

vi.mock('@/pwa/push', () => ({
  PWA_AVAILABLE: true,
  PUSH_AVAILABLE: true,
  currentPermission: () => push.permission,
  hasActiveSubscription: async () => push.subscribed,
  enablePush: push.enablePush,
  disablePush: push.disablePush,
}))

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

type JsonRecord = Record<string, unknown>

const EMAIL_CONFIG = {
  server: 'smtp.qq.com',
  port: 465,
  use_tls: true,
  username: 'me@qq.com',
  password: '***',
  to: ['a@example.com'],
}

function emailChannel(): JsonRecord {
  return {
    id: 'channel-email',
    type: 'email',
    name: '值班邮箱',
    enabled: true,
    config: { ...EMAIL_CONFIG, to: [...EMAIL_CONFIG.to] },
    events: ['pipeline_failed'],
    last_status: 'success',
    last_sent_at: '2026-09-27T06:30:00Z',
    last_error: null,
  }
}

const ANDROID_SUBSCRIPTION = {
  id: 'sub-1',
  endpoint_host: 'fcm.googleapis.com',
  user_agent: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36',
  created_at: '2026-09-20T02:00:00Z',
  last_success_at: null,
  disabled: false,
}

interface ApiOptions {
  channels?: JsonRecord[]
  subscriptions?: JsonRecord[]
  testFailure?: boolean
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApi(options: ApiOptions = {}) {
  let channels = options.channels ?? [emailChannel()]
  let subscriptions = options.subscriptions ?? []
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as JsonRecord : undefined
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/notifications/channels' && request.method === 'GET') return json({ items: channels, total: channels.length })
    if (url.pathname === '/api/notifications/channels' && request.method === 'POST') {
      const created = { id: 'channel-new', last_status: null, last_sent_at: null, last_error: null, ...body }
      channels = [...channels, created]
      return json(created, 201)
    }
    const channelMatch = url.pathname.match(/^\/api\/notifications\/channels\/([^/]+)(\/test)?$/)
    if (channelMatch) {
      const [, id, test] = channelMatch
      if (test) {
        if (options.testFailure) {
          return json({ error: { code: 'NOTIFY_SEND_FAILED', message: '通知发送失败', details: { channel_id: id, upstream: 'HTTP 400: bad key' } } }, 502)
        }
        return json({ sent: true, event: 'pipeline_completed' })
      }
      if (request.method === 'PUT') {
        channels = channels.map((channel) => channel.id === id ? { ...channel, ...body } : channel)
        return json(channels.find((channel) => channel.id === id))
      }
      if (request.method === 'DELETE') {
        channels = channels.filter((channel) => channel.id !== id)
        return new Response(null, { status: 204 })
      }
    }
    if (url.pathname === '/api/notifications/push-subscriptions') return json({ items: subscriptions, total: subscriptions.length })
    const subscriptionMatch = url.pathname.match(/^\/api\/notifications\/push-subscriptions\/([^/]+)$/)
    if (subscriptionMatch && request.method === 'DELETE') {
      subscriptions = subscriptions.filter((item) => item.id !== subscriptionMatch[1])
      return new Response(null, { status: 204 })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    requests,
    find: (method: string, path: string) => requests.filter((request) => request.method === method && request.path === path),
  }
}

function renderNotifications() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter(
    [
      { path: '/more/settings', element: <SettingsPage /> },
      { path: '/more/settings/:section', element: <SettingsPage /> },
    ],
    { initialEntries: ['/more/settings/notifications'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

async function openChannelMenu(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: `「${name}」的更多操作` }))
  return screen.findByRole('dialog', { name })
}

/** jsdom 没有 ResizeObserver；表单里的 Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  push.permission = 'default'
  push.subscribed = false
  push.enablePush.mockReset()
  push.disablePush.mockReset()
  useAuth.getState().clearToken()
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Web Push', () => {
  it('shows the permission state, enables push from the button and unregisters this device (P8)', async () => {
    const api = installApi()
    push.enablePush.mockImplementation(async () => {
      push.permission = 'granted'
      push.subscribed = true
    })
    push.disablePush.mockImplementation(async () => {
      push.subscribed = false
    })
    renderNotifications()

    expect(await screen.findByText('尚未授权')).toBeInTheDocument()
    expect(await screen.findByText('未订阅')).toBeInTheDocument()
    expect(push.enablePush).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '启用推送通知' }))
    await waitFor(() => expect(push.enablePush).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('已授权')).toBeInTheDocument()
    expect(await screen.findByText('已订阅')).toBeInTheDocument()
    await waitFor(() => expect(api.find('GET', '/api/notifications/push-subscriptions').length).toBeGreaterThanOrEqual(2))

    fireEvent.click(screen.getByRole('button', { name: '注销订阅' }))
    await waitFor(() => expect(push.disablePush).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('未订阅')).toBeInTheDocument()
  })

  it('explains how to re-enable a permission the browser denied', async () => {
    installApi()
    push.permission = 'denied'
    renderNotifications()

    expect(await screen.findByText('已被浏览器拒绝')).toBeInTheDocument()
    expect(screen.getByText(/在浏览器的网站设置里把「通知」改为允许/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '启用推送通知' })).toBeDisabled()
  })

  it('shows why enabling failed next to the controls', async () => {
    installApi()
    push.enablePush.mockRejectedValue(new Error('服务端尚未配置 VAPID 密钥，推送不可用。'))
    renderNotifications()

    fireEvent.click(await screen.findByRole('button', { name: '启用推送通知' }))

    expect(await screen.findByText('服务端尚未配置 VAPID 密钥，推送不可用。')).toBeInTheDocument()
  })

  it('lists registered devices and deletes one only after confirmation (P8)', async () => {
    const api = installApi({ subscriptions: [ANDROID_SUBSCRIPTION] })
    renderNotifications()

    expect(await screen.findByText('fcm.googleapis.com')).toBeInTheDocument()
    expect(screen.getByText(/Android · Chrome/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /删除推送订阅：fcm\.googleapis\.com/ }))
    const dialog = await screen.findByRole('alertdialog', { name: '删除这台设备的推送订阅？' })
    expect(api.find('DELETE', '/api/notifications/push-subscriptions/sub-1')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '删除订阅' }))

    await waitFor(() => expect(api.find('DELETE', '/api/notifications/push-subscriptions/sub-1')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByText('fcm.googleapis.com')).not.toBeInTheDocument())
  })
})

describe('notification channels', () => {
  it('creates a Bark channel from the typed form (P7)', async () => {
    const api = installApi({ channels: [] })
    renderNotifications()

    fireEvent.click(await screen.findByRole('button', { name: '添加通道' }))
    const dialog = await screen.findByRole('dialog', { name: '添加通知通道' })
    fireEvent.change(within(dialog).getByLabelText('类型'), { target: { value: 'bark' } })
    fireEvent.change(within(dialog).getByLabelText('名称'), { target: { value: '手机' } })
    expect(within(dialog).getByLabelText('服务器')).toHaveValue('https://api.day.app')
    fireEvent.change(within(dialog).getByLabelText('设备密钥'), { target: { value: 'abc123' } })
    expect(within(dialog).getByRole('button', { name: '流水线完成' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(dialog).getByRole('button', { name: '流水线失败' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.find('POST', '/api/notifications/channels')).toHaveLength(1))
    expect(api.find('POST', '/api/notifications/channels')[0]?.body).toEqual({
      type: 'bark',
      name: '手机',
      config: { server: 'https://api.day.app', device_key: 'abc123' },
      events: ['pipeline_completed', 'pipeline_failed'],
      enabled: true,
    })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '添加通知通道' })).not.toBeInTheDocument())
    expect(await screen.findByText('手机')).toBeInTheDocument()
  })

  it('checks required fields of the typed form before sending', async () => {
    const api = installApi({ channels: [] })
    renderNotifications()

    fireEvent.click(await screen.findByRole('button', { name: '添加通道' }))
    const dialog = await screen.findByRole('dialog', { name: '添加通知通道' })
    fireEvent.change(within(dialog).getByLabelText('类型'), { target: { value: 'email' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(await within(dialog).findByText('请填写通道名称。')).toBeInTheDocument()
    expect(within(dialog).getByText('请填写 SMTP 服务器。')).toBeInTheDocument()
    expect(within(dialog).getByText('请至少添加一个收件人。')).toBeInTheDocument()
    expect(api.find('POST', '/api/notifications/channels')).toHaveLength(0)
  })

  it('edits an email channel and keeps the saved password masked for the server to merge (P7)', async () => {
    const api = installApi()
    renderNotifications()

    fireEvent.click(await screen.findByRole('button', { name: /^值班邮箱/ }))
    const dialog = await screen.findByRole('dialog', { name: '编辑通知通道' })
    expect(within(dialog).getByLabelText('类型')).toHaveValue('email')
    const password = within(dialog).getByLabelText('密码或授权码')
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveValue('')
    expect(within(dialog).getByText('已保存，留空则保留原值')).toBeInTheDocument()
    const recipients = within(dialog).getByLabelText('收件人')
    fireEvent.change(recipients, { target: { value: 'b@example.com' } })
    fireEvent.keyDown(recipients, { key: 'Enter' })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.find('PUT', '/api/notifications/channels/channel-email')).toHaveLength(1))
    expect(api.find('PUT', '/api/notifications/channels/channel-email')[0]?.body).toEqual({
      type: 'email',
      name: '值班邮箱',
      config: { ...EMAIL_CONFIG, to: ['a@example.com', 'b@example.com'] },
      events: ['pipeline_failed'],
      enabled: true,
    })
  })

  it('edits the raw config as JSON, keeps both modes in sync and saves it (P7)', async () => {
    const api = installApi()
    renderNotifications()

    fireEvent.click(await screen.findByRole('button', { name: /^值班邮箱/ }))
    const dialog = await screen.findByRole('dialog', { name: '编辑通知通道' })
    fireEvent.click(within(dialog).getByRole('button', { name: '以 JSON 编辑' }))
    const editor = within(dialog).getByLabelText('配置 JSON')
    expect(JSON.parse((editor as HTMLTextAreaElement).value)).toEqual(EMAIL_CONFIG)

    const edited = { ...EMAIL_CONFIG, port: 587, use_tls: false }
    fireEvent.change(editor, { target: { value: JSON.stringify(edited, null, 2) } })
    fireEvent.click(within(dialog).getByRole('button', { name: '使用表单编辑' }))
    expect(within(dialog).getByLabelText('端口')).toHaveValue('587')
    expect(within(dialog).getByRole('switch', { name: '使用 SSL/TLS 连接' })).toHaveAttribute('aria-checked', 'false')
    expect(within(dialog).getByLabelText('密码或授权码')).toHaveValue('')

    fireEvent.click(within(dialog).getByRole('button', { name: '以 JSON 编辑' }))
    const again = within(dialog).getByLabelText('配置 JSON')
    expect(JSON.parse((again as HTMLTextAreaElement).value)).toEqual(edited)
    fireEvent.change(again, { target: { value: '{"server": ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '使用表单编辑' }))
    expect(within(dialog).getByText('请输入有效的 JSON，检查括号、引号和逗号。')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('配置 JSON')).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('配置 JSON'), { target: { value: JSON.stringify({ ...edited, to: ['ops@example.com'] }) } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.find('PUT', '/api/notifications/channels/channel-email')).toHaveLength(1))
    expect(api.find('PUT', '/api/notifications/channels/channel-email')[0]?.body).toMatchObject({
      type: 'email',
      config: { ...edited, to: ['ops@example.com'] },
    })
  })

  it('collects the explanation in the section hint instead of the subtitle', async () => {
    installApi()
    renderNotifications()

    // 解释性副标题收进小问号，正文只留标题与操作。
    expect(await screen.findByRole('button', { name: '通知通道 说明' })).toBeInTheDocument()
    expect(screen.queryByText('流水线完成、失败或发现更新时发送消息。')).not.toBeInTheDocument()
  })

  it('explains masked credentials with one line in the form and in the raw JSON (P7)', async () => {
    installApi({
      channels: [{
        id: 'channel-hook',
        type: 'webhook',
        name: '值班机器人',
        enabled: true,
        config: { url: 'https://example.com/hook?token=***', method: 'POST', headers: {}, body_template: '{title}' },
        events: ['pipeline_completed'],
        last_status: null,
        last_sent_at: null,
        last_error: null,
      }],
    })
    renderNotifications()

    fireEvent.click(await screen.findByRole('button', { name: /^值班机器人/ }))
    const dialog = await screen.findByRole('dialog', { name: '编辑通知通道' })
    // 请求地址里的凭据和 JSON 模式共用同一句说明。
    expect(within(dialog).getByText('保持 *** 不变即沿用已保存的凭据')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '以 JSON 编辑' }))
    expect(within(dialog).getByText('保持 *** 不变即沿用已保存的凭据')).toBeInTheDocument()
  })

  it('sends a test notification and deletes a channel only after confirmation (P8)', async () => {
    const api = installApi()
    renderNotifications()

    let menu = await openChannelMenu('值班邮箱')
    fireEvent.click(within(menu).getByRole('button', { name: '发送测试通知' }))
    await waitFor(() => expect(api.find('POST', '/api/notifications/channels/channel-email/test')).toHaveLength(1))
    expect(api.find('POST', '/api/notifications/channels/channel-email/test')[0]?.body).toEqual({ event: 'pipeline_completed' })

    menu = await openChannelMenu('值班邮箱')
    fireEvent.click(within(menu).getByRole('button', { name: '删除' }))
    const dialog = await screen.findByRole('alertdialog', { name: '删除通知通道「值班邮箱」？' })
    expect(api.find('DELETE', '/api/notifications/channels/channel-email')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '删除通道' }))

    await waitFor(() => expect(api.find('DELETE', '/api/notifications/channels/channel-email')).toHaveLength(1))
    expect(await screen.findByText('还没有通知通道')).toBeInTheDocument()
  })

  it('shows why a test notification failed', async () => {
    installApi({ testFailure: true })
    renderNotifications()

    const menu = await openChannelMenu('值班邮箱')
    fireEvent.click(within(menu).getByRole('button', { name: '发送测试通知' }))

    expect(await screen.findByText(/HTTP 400: bad key/)).toBeInTheDocument()
  })

  it('toggles a channel from the list and resubmits its masked config unchanged', async () => {
    const api = installApi()
    renderNotifications()

    const toggle = await screen.findByRole('switch', { name: '启用「值班邮箱」' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)

    await waitFor(() => expect(api.find('PUT', '/api/notifications/channels/channel-email')).toHaveLength(1))
    expect(api.find('PUT', '/api/notifications/channels/channel-email')[0]?.body).toEqual({
      type: 'email',
      name: '值班邮箱',
      config: EMAIL_CONFIG,
      events: ['pipeline_failed'],
      enabled: false,
    })
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用「值班邮箱」' })).toHaveAttribute('aria-checked', 'false'))
  })
})
