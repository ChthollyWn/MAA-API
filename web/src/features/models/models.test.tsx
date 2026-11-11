import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Toaster } from '@/components/ui/toaster'
import SettingsPage from '@/routes/settings'
import { useAuth } from '@/stores/auth'

type Json = Record<string, unknown>

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function profile(overrides: Json & { name: string }): Json {
  return {
    api_key: null,
    base_url: 'https://api.deepseek.com',
    context_window: 128000,
    created_at: '2026-11-01T00:00:00Z',
    currency: 'CNY',
    dynamic_context_position: 'tail_system',
    embedding_dim: null,
    enabled: true,
    id: '00000000-0000-0000-0000-000000000001',
    kind: 'chat',
    local_model_path: null,
    max_output_tokens: null,
    model: 'deepseek-chat',
    readonly: false,
    supports_json_schema: null,
    supports_tools: true,
    supports_vision: null,
    updated_at: '2026-11-01T00:00:00Z',
    ...overrides,
  }
}

function defaultProfile(): Json {
  return profile({
    name: 'default',
    id: null,
    model: 'deepseek-flash',
    readonly: true,
    created_at: null,
    updated_at: null,
  })
}

function defaultRoles(): Json[] {
  return [
    { role: 'orchestrator', profile: 'default', model: 'deepseek-flash', source: 'default' },
    { role: 'worker', profile: 'default', model: 'deepseek-flash', source: 'default' },
    { role: 'embedding', profile: null, model: null, source: 'default' },
  ]
}

interface ApiOptions {
  profiles?: Json[]
  roles?: Json[]
}

function installApi(options: ApiOptions = {}) {
  let profiles = (options.profiles ?? [defaultProfile(), profile({ name: 'x' })]).map((item) => ({ ...item }))
  let roles = (options.roles ?? defaultRoles()).map((item) => ({ ...item }))
  const requests: RecordedRequest[] = []

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as Json : undefined
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/agent/model-profiles' && request.method === 'GET') {
      return json({ items: profiles })
    }
    if (url.pathname === '/api/agent/model-profiles' && request.method === 'POST') {
      const created = profile({
        id: '00000000-0000-0000-0000-000000000002',
        created_at: '2026-11-02T00:00:00Z',
        updated_at: '2026-11-02T00:00:00Z',
        ...(body as Json),
        name: String(body?.name ?? ''),
      })
      profiles = [...profiles, created]
      return json(created, 201)
    }
    const updateMatch = /^\/api\/agent\/model-profiles\/([^/]+)$/.exec(url.pathname)
    if (updateMatch && request.method === 'PUT') {
      const name = decodeURIComponent(updateMatch[1]!)
      profiles = profiles.map((item) => (
        item.name === name ? { ...item, ...(body as Json) } : item
      ))
      return json(profiles.find((item) => item.name === name))
    }
    const probeMatch = /^\/api\/agent\/model-profiles\/([^/]+)\/probe$/.exec(url.pathname)
    if (probeMatch && request.method === 'POST') {
      const name = decodeURIComponent(probeMatch[1]!)
      profiles = profiles.map((item) => (
        item.name === name
          ? { ...item, supports_tools: true, supports_vision: true, supports_json_schema: true }
          : item
      ))
      return json({ latency_ms: 12, supports_tools: true, supports_vision: true, supports_json_schema: true })
    }
    if (url.pathname === '/api/agent/model-roles' && request.method === 'GET') {
      return json({ items: roles })
    }
    if (url.pathname === '/api/settings' && request.method === 'PUT') {
      const items = (body?.items ?? {}) as Record<string, unknown>
      roles = roles.map((item) => {
        const key = `llm.role_${String(item.role)}`
        return key in items ? { ...item, profile: items[key], source: 'setting' } : item
      })
      return json({ items: [] })
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })

  vi.stubGlobal('fetch', fetchMock)
  return {
    requests,
    puts: () => requests.filter((request) => request.method === 'PUT'),
    creates: () => requests.filter((request) => request.method === 'POST' && request.path === '/api/agent/model-profiles'),
    probes: () => requests.filter((request) => request.method === 'POST' && request.path.endsWith('/probe')),
  }
}

function renderModels() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [{ path: '/more/settings/:section', element: <SettingsPage /> }],
    { initialEntries: ['/more/settings/models'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
  return { router, queryClient }
}

/** jsdom 没有 ResizeObserver；弹层与开关会用它量尺寸。 */
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

describe('model profiles settings section', () => {
  it('默认档案只读', async () => {
    installApi()
    renderModels()

    const row = await screen.findByRole('group', { name: '档案 default' })
    expect(within(row).getByText('只读')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '编辑' })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: '检测能力' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: '模型档案' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '角色' })).toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: 'default 档案说明' }))
    expect(await screen.findByText('由「LLM 与 Agent」里的设置生成，要改请到那里改。')).toBeInTheDocument()
  })

  it('新建档案后列表刷新', async () => {
    const api = installApi()
    renderModels()

    fireEvent.click(await screen.findByRole('button', { name: '新建档案' }))
    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: 'fast-chat' } })
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'deepseek-chat' } })
    fireEvent.change(screen.getByLabelText('API 地址'), { target: { value: 'https://api.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.creates()).toHaveLength(1))
    expect(api.creates()[0]?.body).toMatchObject({
      name: 'fast-chat',
      model: 'deepseek-chat',
      kind: 'chat',
      provider: 'openai_compatible',
      base_url: 'https://api.example.com',
    })
    expect(await screen.findByText('已新建档案')).toBeInTheDocument()
    expect(await screen.findByRole('group', { name: '档案 fast-chat' })).toBeInTheDocument()
  })

  it('改角色写设置', async () => {
    const api = installApi()
    renderModels()

    const select = await screen.findByLabelText('Worker')
    expect(within(select).getByRole('option', { name: 'x' })).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'x' } })

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/settings')
    expect(api.puts()[0]?.body).toEqual({ items: { 'llm.role_worker': 'x' } })
  })

  it('submits offpeak prices in create and update payloads', async () => {
    const api = installApi({
      profiles: [
        defaultProfile(),
        profile({
          name: 'x',
          price_input_offpeak: 1.2,
          price_cached_input_offpeak: 0.6,
          price_output_offpeak: 3.4,
        }),
      ],
    })
    renderModels()

    fireEvent.click(await screen.findByRole('button', { name: '新建档案' }))
    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: 'offpeak-chat' } })
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'deepseek-chat' } })
    fireEvent.change(screen.getByLabelText('空闲时段输入价（缓存未命中）'), { target: { value: '1.5' } })
    fireEvent.change(screen.getByLabelText('空闲时段输出价'), { target: { value: '2.5' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.creates()).toHaveLength(1))
    expect(api.creates()[0]?.body).toMatchObject({
      price_input_offpeak: 1.5,
      price_cached_input_offpeak: null,
      price_output_offpeak: 2.5,
    })

    const row = await screen.findByRole('group', { name: '档案 x' })
    fireEvent.click(within(row).getByRole('button', { name: '编辑' }))
    expect(await screen.findByLabelText('空闲时段输入价（缓存未命中）')).toHaveValue('1.2')
    expect(screen.getByLabelText('空闲时段输入价（缓存命中）')).toHaveValue('0.6')
    expect(screen.getByLabelText('空闲时段输出价')).toHaveValue('3.4')

    fireEvent.change(screen.getByLabelText('空闲时段输入价（缓存命中）'), { target: { value: '0.8' } })
    fireEvent.change(screen.getByLabelText('空闲时段输出价'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/model-profiles/x')
    expect(api.puts()[0]?.body).toMatchObject({
      price_input_offpeak: 1.2,
      price_cached_input_offpeak: 0.8,
      price_output_offpeak: null,
    })
  })

  it('检测能力显示结果', async () => {
    const api = installApi()
    renderModels()

    const row = await screen.findByRole('group', { name: '档案 x' })
    expect(within(row).getByText('视觉：未检测')).toBeInTheDocument()
    expect(within(row).getByText('JSON Schema：未检测')).toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: '检测能力' }))

    await waitFor(() => expect(api.probes()).toHaveLength(1))
    expect(await screen.findByText('视觉：可用')).toBeInTheDocument()
    expect(screen.getByText('JSON Schema：可用')).toBeInTheDocument()
    expect(await screen.findByText('已完成检测')).toBeInTheDocument()
  })
})
