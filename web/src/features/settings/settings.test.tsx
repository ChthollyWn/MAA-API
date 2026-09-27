import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import SettingsPage from '@/routes/settings'
import { useAuth } from '@/stores/auth'
import { THEME_STORAGE_KEY, useUiStore } from '@/stores/ui'

interface SchemaItem {
  key: string
  label: string
  group: string
  type: string
  hot_action?: string
  sensitive?: boolean
  readonly?: boolean
  minimum?: number
  maximum?: number
  enum?: string[]
}

interface ValueItem {
  key: string
  value: unknown
  source: string
}

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

const SCHEMA: SchemaItem[] = [
  { key: 'app.access_token', label: '访问密钥', group: '应用', type: 'string', sensitive: true, readonly: true, hot_action: 'restart_required' },
  { key: 'app.maa_core_path', label: 'MaaCore 路径', group: '应用', type: 'string', readonly: true, hot_action: 'restart_required' },
  { key: 'app.proxy', label: '系统代理', group: '应用', type: 'string', hot_action: 'none' },
  { key: 'updates.check_hour', label: '每日更新检查时间（小时）', group: '更新', type: 'integer', minimum: 0, maximum: 23, hot_action: 'restart_required' },
  { key: 'adb.path', label: 'ADB 路径', group: '设备', type: 'string', readonly: true, hot_action: 'restart_required' },
  { key: 'adb.address', label: '设备地址', group: '设备', type: 'string', hot_action: 'reconnect' },
  { key: 'adb.screenshot_quality', label: '截图质量', group: '设备', type: 'integer', minimum: 1, maximum: 95, hot_action: 'none' },
  { key: 'adb.connection_config', label: 'ADB 连接配置', group: '设备', type: 'string', enum: ['General', 'CompatMac', 'CompatPOSIXShell', 'Nox'], hot_action: 'reconnect' },
  { key: 'adb.common_ports', label: '模拟器常见端口', group: '设备', type: 'integer[]', minimum: 1, maximum: 65535, hot_action: 'none' },
  { key: 'log.core_min_level', label: '内核日志最低级别', group: '日志', type: 'string', enum: ['OFF', 'FAT', 'ERR', 'WRN', 'INF', 'DBG', 'TRC'], hot_action: 'none' },
  { key: 'channel.client_type', label: '默认客户端渠道', group: '渠道', type: 'string', enum: ['Official', 'Bilibili'], hot_action: 'none' },
  { key: 'llm.base_url', label: 'LLM API 地址', group: 'LLM', type: 'string', hot_action: 'none' },
  { key: 'llm.api_key', label: 'LLM API 密钥', group: 'LLM', type: 'string', sensitive: true, hot_action: 'none' },
  { key: 'llm.model', label: 'LLM 模型', group: 'LLM', type: 'string', hot_action: 'none' },
  { key: 'agent.auto_diagnose_failures', label: '自动诊断流水线与内核故障', group: 'Agent', type: 'boolean', hot_action: 'none' },
  { key: 'deployment.cors_origins', label: '附加浏览器来源白名单', group: '部署', type: 'string[]', hot_action: 'restart_required' },
]

const VALUES: ValueItem[] = [
  { key: 'app.access_token', value: '***', source: 'yaml' },
  { key: 'app.maa_core_path', value: '/opt/maa/libMaaCore.dylib', source: 'yaml' },
  { key: 'app.proxy', value: '', source: 'default' },
  { key: 'updates.check_hour', value: 9, source: 'default' },
  { key: 'adb.path', value: '/opt/homebrew/bin/adb', source: 'default' },
  { key: 'adb.address', value: '127.0.0.1:5555', source: 'db' },
  { key: 'adb.screenshot_quality', value: 25, source: 'default' },
  { key: 'adb.connection_config', value: 'General', source: 'yaml' },
  { key: 'adb.common_ports', value: [5555, 16384], source: 'default' },
  { key: 'log.core_min_level', value: 'INF', source: 'default' },
  { key: 'channel.client_type', value: 'Bilibili', source: 'default' },
  { key: 'llm.base_url', value: 'https://api.deepseek.com', source: 'default' },
  { key: 'llm.api_key', value: '***', source: 'env' },
  { key: 'llm.model', value: 'deepseek-flash', source: 'default' },
  { key: 'agent.auto_diagnose_failures', value: true, source: 'default' },
  { key: 'deployment.cors_origins', value: [], source: 'default' },
]

/** 恢复默认后每个键回落到的下一层。 */
const RESET_TO: Record<string, ValueItem> = {
  'adb.address': { key: 'adb.address', value: '127.0.0.1:16384', source: 'yaml' },
}

interface ApiOptions {
  extraSchema?: SchemaItem[]
  values?: ValueItem[]
  putError?: { status: number; body: unknown }
  /** 前几次读取 `/api/settings` 返回 503。 */
  failedReads?: number
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApi(options: ApiOptions = {}) {
  const values = new Map((options.values ?? VALUES).map((item) => [item.key, { ...item }]))
  const requests: RecordedRequest[] = []
  let failedReads = options.failedReads ?? 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as Record<string, unknown> : undefined
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/settings/schema') return json({ items: [...SCHEMA, ...(options.extraSchema ?? [])] })
    if (url.pathname === '/api/settings' && request.method === 'GET') {
      if (failedReads > 0) {
        failedReads -= 1
        return json({ error: { code: 'SERVICE_UNAVAILABLE', message: '设置服务尚未启动' } }, 503)
      }
      return json({ items: [...values.values()], total: values.size })
    }
    if (url.pathname === '/api/settings' && request.method === 'PUT') {
      if (options.putError) {
        if ((options.putError.body as { error?: { details?: { applied?: boolean } } }).error?.details?.applied) {
          for (const [key, value] of Object.entries(body?.items as Record<string, unknown>)) values.set(key, { key, value, source: 'db' })
        }
        return json(options.putError.body, options.putError.status)
      }
      for (const [key, value] of Object.entries(body?.items as Record<string, unknown>)) values.set(key, { key, value, source: 'db' })
      return json({ items: [...values.values()], total: values.size })
    }
    if (url.pathname === '/api/settings/reset') {
      for (const key of body?.keys as string[]) {
        const next = RESET_TO[key]
        if (next) values.set(key, { ...next })
      }
      return json({ items: [...values.values()], total: values.size })
    }
    if (url.pathname === '/api/agent/capabilities/check') {
      return json({
        model: 'deepseek-flash',
        tool_calling: true,
        vision: false,
        ready: true,
        status: 'vision_degraded',
        degraded_reason: '模型暂不支持图片输入',
      })
    }
    if (url.pathname === '/api/system/health') return json({ status: 'ok', auth_enabled: true, version: 'test' })
    if (url.pathname === '/api/notifications/channels') return json({ items: [], total: 0 })
    if (url.pathname === '/api/notifications/push-subscriptions') return json({ items: [], total: 0 })
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    requests,
    puts: () => requests.filter((request) => request.method === 'PUT' && request.path === '/api/settings'),
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderSettings(entries: string[] = ['/more/settings']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createMemoryRouter(
    [
      { path: '/more/settings', element: <SettingsPage /> },
      { path: '/more/settings/:section', element: <SettingsPage /> },
      { path: '*', element: <LocationProbe /> },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router }
}

/** jsdom 没有 ResizeObserver；表单里的 Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  localStorage.clear()
  useAuth.getState().clearToken()
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('settings index', () => {
  it('lists the sections as one row each and opens a section', async () => {
    const api = installApi()
    const { router } = renderSettings()

    expect(screen.getByRole('heading', { name: '设置', level: 1 })).toBeInTheDocument()
    const rows: Array<[RegExp, string]> = [
      [/^外观/, 'appearance'],
      [/^访问令牌/, 'access'],
      [/^设备与渠道/, 'device'],
      [/^LLM 与 Agent/, 'agent'],
      [/^通知/, 'notifications'],
      [/^更新与日志/, 'maintenance'],
      [/^应用与部署/, 'system'],
    ]
    for (const [name, section] of rows) {
      expect(screen.getByRole('link', { name })).toHaveAttribute('href', `/more/settings/${section}`)
    }
    await waitFor(() => expect(api.requests.filter((request) => request.path.startsWith('/api/settings'))).toHaveLength(2))
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(screen.queryByRole('link', { name: /^其他/ })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: /^设备与渠道/ }))
    expect(await screen.findByRole('heading', { name: '设备与渠道', level: 1 })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/more/settings/device')
    expect(await screen.findByLabelText('设备地址')).toBeInTheDocument()
  })

  it('shows the other section only when the schema has groups outside the known sections', async () => {
    installApi({
      extraSchema: [{ key: 'lab.preview', label: '实验功能预览', group: '实验', type: 'boolean', hot_action: 'none' }],
      values: [...VALUES, { key: 'lab.preview', value: false, source: 'default' }],
    })
    renderSettings()

    const other = await screen.findByRole('link', { name: /^其他/ })
    expect(other).toHaveAttribute('href', '/more/settings/other')
    fireEvent.click(other)
    expect(await screen.findByRole('switch', { name: '实验功能预览' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '实验' })).toBeInTheDocument()
  })

  it('explains an unknown section and links back to the index', async () => {
    installApi()
    const { router } = renderSettings(['/more/settings/nope'])

    expect(screen.getByText('找不到这个设置分区')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '返回设置' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings'))
  })
})

describe('schema-driven sections', () => {
  it('shows the device address with its source and how it takes effect, without raw keys (P1)', async () => {
    installApi()
    renderSettings(['/more/settings/device'])

    const address = await screen.findByLabelText('设备地址')
    expect(address).toHaveValue('127.0.0.1:5555')
    expect(screen.getByText('来源：数据库覆盖')).toBeInTheDocument()
    expect(screen.getAllByText('保存后会重新连接设备').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '设备' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '渠道' })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('adb.address')
    expect(screen.queryByLabelText('LLM API 密钥')).not.toBeInTheDocument()
  })

  it('keeps the LLM API key masked, empty and out of the page even if the server leaks it (P2)', async () => {
    installApi({ values: VALUES.map((item) => item.key === 'llm.api_key' ? { ...item, value: 'fixture-real-secret' } : item) })
    renderSettings(['/more/settings/agent'])

    const secret = await screen.findByLabelText('LLM API 密钥')
    expect(secret).toHaveAttribute('type', 'password')
    expect(secret).toHaveValue('')
    expect(screen.getByText('已配置，页面已隐藏')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('fixture-real-secret')
    cleanup()

    installApi()
    renderSettings(['/more/settings/agent'])
    expect(await screen.findByLabelText('LLM API 密钥')).toHaveValue('')
    expect(screen.getByText('已配置，已脱敏')).toBeInTheDocument()
  })

  it('saves only the edited device address and refreshes its source in place (P3)', async () => {
    const api = installApi()
    renderSettings(['/more/settings/device'])

    const address = await screen.findByLabelText('设备地址')
    fireEvent.change(address, { target: { value: '127.0.0.1:5556' } })
    expect(screen.getByText('有 1 项未保存的更改')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.body).toEqual({ items: { 'adb.address': '127.0.0.1:5556' } })
    await waitFor(() => expect(screen.queryByText('有 1 项未保存的更改')).not.toBeInTheDocument())
    expect(screen.getByLabelText('设备地址')).toHaveValue('127.0.0.1:5556')

    fireEvent.change(screen.getByLabelText('截图质量'), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(api.puts()).toHaveLength(2))
    expect(api.puts()[1]?.body).toEqual({ items: { 'adb.screenshot_quality': 40 } })
    await waitFor(() => expect(screen.getAllByText('来源：数据库覆盖')).toHaveLength(2))
  })

  it('turns the default-on automatic diagnosis off and saves false on its own (P4)', async () => {
    const api = installApi()
    renderSettings(['/more/settings/agent'])

    const toggle = await screen.findByRole('switch', { name: '自动诊断流水线与内核故障' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.body).toEqual({ items: { 'agent.auto_diagnose_failures': false } })
  })

  it('leaves the unchanged masked key out of the request when another LLM setting changes', async () => {
    const api = installApi()
    renderSettings(['/more/settings/agent'])

    fireEvent.change(await screen.findByLabelText('LLM 模型'), { target: { value: 'deepseek-chat' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.body).toEqual({ items: { 'llm.model': 'deepseek-chat' } })
  })

  it('checks tool-calling and vision capabilities from the agent section (P5)', async () => {
    const api = installApi()
    renderSettings(['/more/settings/agent'])

    fireEvent.click(await screen.findByRole('button', { name: '检查模型能力' }))

    expect(await screen.findByText('工具调用：可用')).toBeInTheDocument()
    expect(screen.getByText('视觉输入：不可用')).toBeInTheDocument()
    expect(screen.getByText('模型暂不支持图片输入')).toBeInTheDocument()
    expect(api.requests.some((request) => request.method === 'POST' && request.path === '/api/agent/capabilities/check')).toBe(true)
  })

  it('shows a retryable error when the settings cannot be read', async () => {
    installApi({ failedReads: 1 })
    renderSettings(['/more/settings/device'])

    expect(await screen.findByText('设置读取失败')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('设置服务尚未启动')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByLabelText('设备地址')).toHaveValue('127.0.0.1:5555')
  })

  it('validates numbers before saving and keeps the request from being sent', async () => {
    const api = installApi()
    renderSettings(['/more/settings/device'])

    fireEvent.change(await screen.findByLabelText('截图质量'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByText('不能小于 1。')).toBeInTheDocument()
    expect(screen.getByLabelText('截图质量')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('范围 1 ~ 95')).toBeInTheDocument()
    expect(api.puts()).toHaveLength(0)
  })

  it('edits integer and string lists as chips and submits them as arrays', async () => {
    const api = installApi()
    const { router } = renderSettings(['/more/settings/device'])

    const ports = await screen.findByLabelText('模拟器常见端口')
    fireEvent.change(ports, { target: { value: '5555' } })
    fireEvent.keyDown(ports, { key: 'Enter' })
    expect(await screen.findByText('已经添加过这个值。')).toBeInTheDocument()
    fireEvent.change(ports, { target: { value: '70000' } })
    fireEvent.click(screen.getByRole('button', { name: '添加模拟器常见端口' }))
    expect(await screen.findByText('每个值不能大于 65535。')).toBeInTheDocument()
    fireEvent.change(ports, { target: { value: '7555' } })
    fireEvent.keyDown(ports, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: '移除 16384' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.body).toEqual({ items: { 'adb.common_ports': [5555, 7555] } })

    await act(async () => router.navigate('/more/settings/system'))
    const origins = await screen.findByLabelText('附加浏览器来源白名单')
    fireEvent.change(origins, { target: { value: 'https://maa.tailnet.ts.net' } })
    fireEvent.keyDown(origins, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(2))
    expect(api.puts()[1]?.body).toEqual({ items: { 'deployment.cors_origins': ['https://maa.tailnet.ts.net'] } })
  })

  it('shows read-only values without inputs and secrets only as configured', async () => {
    installApi()
    renderSettings(['/more/settings/system'])

    expect(await screen.findByText('/opt/maa/libMaaCore.dylib')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'MaaCore 路径' })).queryByRole('textbox')).not.toBeInTheDocument()
    const token = screen.getByRole('group', { name: '访问密钥' })
    expect(within(token).getByText('已配置')).toBeInTheDocument()
    expect(within(token).queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByLabelText('系统代理')).toBeInTheDocument()
  })

  it('reports a saved-but-not-applied change in place and guides to the device status', async () => {
    installApi({
      putError: {
        status: 500,
        body: { error: { code: 'SETTING_APPLY_FAILED', message: '配置已保存，但设备重连失败', details: { applied: true } } },
      },
    })
    renderSettings(['/more/settings/device'])

    fireEvent.change(await screen.findByLabelText('设备地址'), { target: { value: '10.0.0.9:5555' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('已保存但生效失败')
    expect(alert).toHaveTextContent('配置已保留，请检查设备连接或相关进程。')
    expect(within(alert).getByRole('link', { name: '查看设备状态' })).toHaveAttribute('href', '/')
    await waitFor(() => expect(screen.queryByText('有 1 项未保存的更改')).not.toBeInTheDocument())
    expect(screen.getByLabelText('设备地址')).toHaveValue('10.0.0.9:5555')
  })

  it('blocks leaving a section with unsaved changes until the user decides (P6)', async () => {
    installApi()
    const { router } = renderSettings(['/more/settings', '/more/settings/device'])

    const address = await screen.findByLabelText('设备地址')
    fireEvent.change(address, { target: { value: '127.0.0.1:5556' } })
    const beforeUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(beforeUnload)
    expect(beforeUnload.defaultPrevented).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    let dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的更改？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '继续编辑' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/more/settings/device')
    expect(screen.getByLabelText('设备地址')).toHaveValue('127.0.0.1:5556')

    await act(async () => router.navigate('/more'))
    dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的更改？' })
    expect(router.state.location.pathname).toBe('/more/settings/device')
    fireEvent.click(within(dialog).getByRole('button', { name: '放弃更改' }))
    expect(await screen.findByTestId('location')).toHaveTextContent('/more')
  })

  it('goes back once the user confirms discarding, without asking twice (P6)', async () => {
    installApi()
    const { router } = renderSettings(['/more/settings', '/more/settings/device'])

    fireEvent.change(await screen.findByLabelText('设备地址'), { target: { value: '127.0.0.1:5556' } })
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的更改？' })
    expect(dialog).toHaveTextContent('「设备与渠道」有 1 项更改还没有保存')
    fireEvent.click(within(dialog).getByRole('button', { name: '放弃更改' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings'))
    expect(await screen.findByRole('heading', { name: '设置', level: 1 })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('lets the user leave freely after discarding the changes', async () => {
    installApi()
    const { router } = renderSettings(['/more/settings', '/more/settings/device'])

    fireEvent.change(await screen.findByLabelText('设备地址'), { target: { value: '127.0.0.1:5556' } })
    fireEvent.click(screen.getByRole('button', { name: '放弃更改' }))
    expect(screen.getByLabelText('设备地址')).toHaveValue('127.0.0.1:5555')
    fireEvent.click(screen.getByRole('button', { name: '返回' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('restores the section defaults only after confirmation (P8)', async () => {
    const api = installApi()
    renderSettings(['/more/settings/device'])

    await screen.findByLabelText('设备地址')
    fireEvent.click(screen.getByRole('button', { name: /恢复默认/ }))
    const dialog = await screen.findByRole('alertdialog', { name: '恢复「设备与渠道」的默认设置？' })
    expect(api.requests.some((request) => request.path === '/api/settings/reset')).toBe(false)
    fireEvent.click(within(dialog).getByRole('button', { name: '恢复默认' }))

    await waitFor(() => expect(api.requests.some((request) => request.path === '/api/settings/reset')).toBe(true))
    const reset = api.requests.find((request) => request.path === '/api/settings/reset')
    expect(reset?.method).toBe('POST')
    expect(reset?.body).toEqual({
      keys: ['adb.address', 'adb.screenshot_quality', 'adb.connection_config', 'adb.common_ports', 'channel.client_type'],
    })
    await waitFor(() => expect(screen.getByLabelText('设备地址')).toHaveValue('127.0.0.1:16384'))
    expect(screen.queryByText('来源：数据库覆盖')).not.toBeInTheDocument()
  })
})

describe('local sections', () => {
  it('switches the theme from the appearance section', async () => {
    installApi()
    useUiStore.getState().setTheme('dark')
    renderSettings(['/more/settings/appearance'])

    fireEvent.click(screen.getByRole('radio', { name: '浅色' }))

    expect(useUiStore.getState().theme).toBe('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(screen.getByRole('radio', { name: '浅色' })).toHaveAttribute('aria-checked', 'true')
  })

  it('replaces the local access token without echoing it and clears it after confirmation (P8)', async () => {
    installApi()
    useAuth.getState().setToken('old-token')
    renderSettings(['/more/settings/access'])

    expect(screen.getByText('已保存')).toBeInTheDocument()
    const input = screen.getByLabelText('新的访问 token')
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveValue('')
    fireEvent.change(input, { target: { value: 'new-token' } })
    fireEvent.click(screen.getByRole('button', { name: '保存 token' }))

    expect(useAuth.getState().token).toBe('new-token')
    expect(screen.getByLabelText('新的访问 token')).toHaveValue('')
    expect(document.body.textContent).not.toContain('new-token')
    expect(document.body.textContent).not.toContain('old-token')

    fireEvent.click(screen.getByRole('button', { name: /清除此设备的 token/ }))
    const dialog = await screen.findByRole('alertdialog', { name: '清除此设备保存的访问 token？' })
    expect(useAuth.getState().token).toBe('new-token')
    fireEvent.click(within(dialog).getByRole('button', { name: '清除 token' }))

    await waitFor(() => expect(useAuth.getState().token).toBeNull())
    expect(screen.getByText('未保存')).toBeInTheDocument()
  })

  it('only explains Web Push outside a secure context and still lists notification channels', async () => {
    installApi()
    renderSettings(['/more/settings/notifications'])

    expect(screen.getByText(/推送需要通过 HTTPS 访问/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '启用推送通知' })).not.toBeInTheDocument()
    expect(await screen.findByText('还没有通知通道')).toBeInTheDocument()
  })
})
