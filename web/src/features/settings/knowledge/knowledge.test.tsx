import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import SettingsPage from '@/routes/settings'
import KnowledgeEditorPage from '@/routes/settings/knowledge-editor'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { MAX_TITLE_CHARS, NEW_KNOWLEDGE_PATH, knowledgeEntryPath } from './format'

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

type Json = Record<string, unknown>

const BODIES: Record<string, string> = {
  'account-resources': '每天先领任务，再看合成玉与至纯源石。',
  'screen-home': '首页左上角是理智，右上角是合成玉。',
  'screen-home-builtin': '内置原文：首页左上角是理智。',
  glossary: '俗'.repeat(900),
  profile: '档'.repeat(400),
  'gacha-rule': '一抽 = 600 合成玉；十连 = 6000。',
}

function entry(overrides: Json & { id: string }): Json {
  const body = BODIES[overrides.id] ?? '正文'
  return {
    kind: 'playbook',
    title: '未命名',
    when_to_use: '需要时',
    enabled: true,
    source: 'custom',
    modified: false,
    updated_at: '2026-09-29T02:00:00',
    ...overrides,
    body: (overrides.body as string | undefined) ?? body,
  }
}

/** 五条覆盖内置、覆盖过的内置与自定义的条目。 */
function defaultEntries(): Json[] {
  return [
    entry({ id: 'account-resources', kind: 'playbook', title: '查询账号资源', when_to_use: '用户问抽卡资源时', source: 'builtin', modified: false }),
    entry({ id: 'screen-home', kind: 'screen', title: '首页布局', when_to_use: '需要读首页货币时', source: 'builtin', modified: true }),
    entry({ id: 'gacha-rule', kind: 'rule', title: '抽卡换算', when_to_use: '用户问还能抽多少时', enabled: false }),
    entry({ id: 'glossary', kind: 'glossary', title: '术语表', when_to_use: '用户说俗称时', source: 'builtin', modified: false }),
    entry({ id: 'profile', kind: 'profile', title: '用户档案', when_to_use: '需要知道默认账号时' }),
  ]
}

function summary(item: Json): Json {
  const { body, ...rest } = item
  return { ...rest, body_chars: String(body ?? '').length }
}

function detail(item: Json): Json {
  const builtin = item.source === 'builtin'
    ? { id: item.id, kind: item.kind, title: item.modified ? '首页布局（内置）' : item.title, when_to_use: item.when_to_use, body: BODIES['screen-home-builtin'] }
    : null
  return { ...item, builtin }
}

interface ApiOptions {
  entries?: Json[]
  put?: { status: number; body: unknown }
  remove?: { status: number; body: unknown }
}

function installApi(options: ApiOptions = {}) {
  let entries = (options.entries ?? defaultEntries()).map((item) => ({ ...item }))
  const requests: RecordedRequest[] = []
  const notFound = () => json({ error: { code: 'KNOWLEDGE_NOT_FOUND', message: '知识条目不存在' } }, 404)
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as Json : undefined
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/settings/schema') return json({ items: [] })
    if (url.pathname === '/api/settings') return json({ items: [], total: 0 })
    if (url.pathname === '/api/agent/knowledge' && request.method === 'GET') {
      return json({ items: entries.map(summary), total: entries.length })
    }
    const match = /^\/api\/agent\/knowledge\/([^/]+)$/.exec(url.pathname)
    if (match) {
      const id = decodeURIComponent(match[1]!)
      const found = entries.find((item) => item.id === id)
      if (request.method === 'GET') return found ? json(detail(found)) : notFound()
      if (request.method === 'PUT') {
        if (options.put) return json(options.put.body, options.put.status)
        const updated = found
          ? { ...found, ...body, modified: found.source === 'builtin' }
          : entry({ id, ...(body as Json) })
        entries = found ? entries.map((item) => (item.id === id ? updated : item)) : [...entries, updated]
        return json(detail(updated))
      }
      if (request.method === 'DELETE') {
        if (options.remove) return json(options.remove.body, options.remove.status)
        if (!found) return notFound()
        entries = entries.filter((item) => item.id !== id)
        return new Response(null, { status: 204 })
      }
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const calls = (method: string) => requests.filter((request) => request.method === method)
  return {
    requests,
    puts: () => calls('PUT'),
    deletes: () => calls('DELETE'),
    list: () => entries,
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderKnowledge(entries: string[] = ['/more/settings/knowledge']) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/settings', element: <SettingsPage /> },
      { path: '/more/settings/:section', element: <SettingsPage /> },
      { path: '/more/settings/knowledge/new', element: <KnowledgeEditorPage /> },
      { path: '/more/settings/knowledge/:entryId', element: <KnowledgeEditorPage /> },
      { path: '*', element: <LocationProbe /> },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router, queryClient }
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

describe('knowledge settings section', () => {
  it('sends both the new and the existing entry to the full-page editor', () => {
    const children = routeConfig.find((route) => route.path === '/')?.children ?? []
    expect(children.some((route) => route.path === 'more/settings/knowledge/new')).toBe(true)
    expect(children.some((route) => route.path === 'more/settings/knowledge/:entryId')).toBe(true)
  })

  it('lists the section right after the Agent section', async () => {
    installApi()
    renderKnowledge(['/more/settings'])

    const link = await screen.findByRole('link', { name: /^Agent 知识库/ })
    expect(link).toHaveAttribute('href', '/more/settings/knowledge')
    const hrefs = screen.getAllByRole('link').map((item) => item.getAttribute('href'))
    expect(hrefs.indexOf('/more/settings/knowledge')).toBe(hrefs.indexOf('/more/settings/agent') + 1)
  })

  it('groups the entries by kind and shows where each one comes from', async () => {
    installApi()
    renderKnowledge()

    expect(await screen.findByRole('heading', { level: 1, name: 'Agent 知识库' })).toBeInTheDocument()
    for (const heading of ['操作剧本', '界面知识', '规则', '术语表', '用户档案']) {
      expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    }

    const builtin = screen.getByRole('link', { name: /查询账号资源/ })
    expect(within(builtin).getByText('account-resources')).toBeInTheDocument()
    expect(within(builtin).getByText('内置')).toBeInTheDocument()

    const modified = screen.getByRole('link', { name: /首页布局/ })
    expect(within(modified).getByText('已修改')).toBeInTheDocument()

    const custom = screen.getByRole('link', { name: /抽卡换算/ })
    expect(within(custom).getByText('自定义')).toBeInTheDocument()

    // 开关按当前启用状态显示，标题与 id 都能看到。
    expect(screen.getByRole('switch', { name: '启用「查询账号资源」' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('switch', { name: '启用「抽卡换算」' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText('已启用 4 条 · 术语表与用户档案正文合计 1300 字')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '新建' })).toHaveAttribute('href', NEW_KNOWLEDGE_PATH)
    // 机制说明收进小问号，标题下不再铺解释。
    expect(screen.getByRole('button', { name: '知识条目 说明' })).toBeInTheDocument()
    expect(screen.queryByText(/Agent 每轮都会带上这份索引/)).not.toBeInTheDocument()
  })

  it('writes the whole entry back when the enable switch flips', async () => {
    const api = installApi()
    renderKnowledge()

    fireEvent.click(await screen.findByRole('switch', { name: '启用「抽卡换算」' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/knowledge/gacha-rule')
    expect(api.puts()[0]?.body).toEqual({
      kind: 'rule',
      title: '抽卡换算',
      when_to_use: '用户问还能抽多少时',
      body: BODIES['gacha-rule'],
      enabled: true,
    })
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用「抽卡换算」' })).toHaveAttribute('aria-checked', 'true'))
  })

  it('shows a readable line for a 409 from the switch', async () => {
    const api = installApi({
      put: { status: 409, body: { error: { code: 'KNOWLEDGE_BUILTIN_READONLY', message: '与当前状态冲突' } } },
    })
    renderKnowledge()

    fireEvent.click(await screen.findByRole('switch', { name: '启用「抽卡换算」' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('内置条目不能改类型')
    expect(alert).toHaveTextContent('也不能删除')
    expect(api.puts()).toHaveLength(1)
    // 失败后回到原状态，不留下假的成功界面。
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用「抽卡换算」' })).toHaveAttribute('aria-checked', 'false'))
  })

  it('opens the editor from a row', async () => {
    installApi()
    const { router } = renderKnowledge()

    fireEvent.click(await screen.findByRole('link', { name: /首页布局/ }))

    expect(await screen.findByRole('heading', { level: 1, name: '编辑知识条目' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/more/settings/knowledge/screen-home')
  })

  it('explains a missing entry', async () => {
    installApi()
    renderKnowledge(['/more/settings/knowledge/nope'])

    expect(await screen.findByText('找不到这个知识条目')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回知识库' })).toHaveAttribute('href', '/more/settings/knowledge')
  })
})

describe('knowledge editor', () => {
  it('creates a custom entry with per-field character counters', async () => {
    const api = installApi()
    const { router } = renderKnowledge([NEW_KNOWLEDGE_PATH])

    const id = await screen.findByLabelText('标识')
    const title = screen.getByLabelText('标题')
    const when = screen.getByLabelText('什么时候用')
    const body = screen.getByLabelText('正文')
    expect(title).toHaveAttribute('maxlength', String(MAX_TITLE_CHARS))
    expect(screen.getByText(`0 / ${MAX_TITLE_CHARS} 字`)).toBeInTheDocument()

    // 标识格式不合时不发请求，并就地说明。
    fireEvent.change(id, { target: { value: 'Bad_ID' } })
    fireEvent.change(title, { target: { value: '我的笔记' } })
    fireEvent.change(when, { target: { value: '用户问起时' } })
    fireEvent.change(body, { target: { value: '正文' } })
    fireEvent.click(screen.getByRole('button', { name: '创建条目' }))
    expect(await screen.findByText('标识用 2–48 位小写字母、数字或连字符，并以字母或数字开头。')).toBeInTheDocument()
    expect(api.puts()).toHaveLength(0)

    fireEvent.change(id, { target: { value: 'my-note' } })
    expect(screen.getByText(`4 / ${MAX_TITLE_CHARS} 字`)).toBeInTheDocument()
    expect(screen.getByText('- my-note｜我的笔记：用户问起时')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '创建条目' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/knowledge/my-note')
    expect(api.puts()[0]?.body).toEqual({
      kind: 'playbook',
      title: '我的笔记',
      when_to_use: '用户问起时',
      body: '正文',
      enabled: true,
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings/knowledge'))
  })

  it('creates another entry even when many are already enabled (M20)', async () => {
    const many = Array.from({ length: 50 }, (_, index) => entry({
      id: `rule-${index}`,
      kind: 'rule',
      title: `规则 ${index}`,
      source: 'custom',
    }))
    const api = installApi({ entries: many })
    renderKnowledge([NEW_KNOWLEDGE_PATH])

    fireEvent.change(await screen.findByLabelText('标题'), { target: { value: '第 51 条' } })
    fireEvent.change(screen.getByLabelText('什么时候用'), { target: { value: '需要时' } })
    fireEvent.change(screen.getByLabelText('正文'), { target: { value: '正文' } })
    fireEvent.change(screen.getByLabelText('标识'), { target: { value: 'rule-51' } })

    // 条目数不再有上限：没有拦截提示，点创建就发请求。
    expect(screen.queryByText(/不能超过/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '创建条目' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/knowledge/rule-51')
    expect(api.puts()[0]?.body).toEqual({
      kind: 'playbook',
      title: '第 51 条',
      when_to_use: '需要时',
      body: '正文',
      enabled: true,
    })
  })

  it('saves a glossary entry even when the other glossary bodies are long (M20)', async () => {
    const entries = [entry({ id: 'glossary', kind: 'glossary', title: '术语表', body: 'x'.repeat(2900), source: 'builtin' })]
    const api = installApi({ entries })
    renderKnowledge([NEW_KNOWLEDGE_PATH])

    fireEvent.change(await screen.findByLabelText('标识'), { target: { value: 'my-glossary' } })
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '补充术语' } })
    fireEvent.change(screen.getByLabelText('什么时候用'), { target: { value: '用户说俗称时' } })
    fireEvent.change(screen.getByLabelText('类型'), { target: { value: 'glossary' } })
    fireEvent.change(screen.getByLabelText('正文'), { target: { value: 'y'.repeat(200) } })

    // 全文合计不再有上限，编辑器里也没有合计计数器。
    expect(screen.queryByText(/正文合计/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '创建条目' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/knowledge/my-glossary')
    expect(api.puts()[0]?.body).toEqual({
      kind: 'glossary',
      title: '补充术语',
      when_to_use: '用户说俗称时',
      body: 'y'.repeat(200),
      enabled: true,
    })
  })

  it('saves an edited entry without changing its kind or body', async () => {
    const api = installApi()
    const { router } = renderKnowledge([knowledgeEntryPath('screen-home')])

    const title = await screen.findByLabelText('标题')
    expect(title).toHaveValue('首页布局')
    expect(screen.getByLabelText('类型')).toBeDisabled()
    expect(screen.getByLabelText('标识')).toHaveAttribute('readonly')
    expect(screen.getByText(/^上次更新：/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '索引预览 说明' })).toBeInTheDocument()

    fireEvent.change(title, { target: { value: '首页布局（改）' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.body).toEqual({
      kind: 'screen',
      title: '首页布局（改）',
      when_to_use: '需要读首页货币时',
      body: BODIES['screen-home'],
      enabled: true,
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings/knowledge'))
  })

  it('shows a readable line when the server refuses to change a builtin kind', async () => {
    const api = installApi({
      put: { status: 409, body: { error: { code: 'KNOWLEDGE_BUILTIN_READONLY', message: '与当前状态冲突' } } },
    })
    renderKnowledge([knowledgeEntryPath('screen-home')])

    fireEvent.change(await screen.findByLabelText('标题'), { target: { value: '首页布局（改）' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('内置条目不能改类型')
    expect(alert).toHaveTextContent('也不能删除')
    expect(api.puts()).toHaveLength(1)
  })

  it('marks the field the server rejected in a 422', async () => {
    installApi({
      put: {
        status: 422,
        body: {
          error: {
            code: 'VALIDATION_ERROR',
            message: '请求参数校验失败（1 处）',
            details: { fields: [{ loc: ['body', 'title'], msg: 'String should have at most 32 characters', type: 'string_too_long' }] },
          },
        },
      },
    })
    renderKnowledge([knowledgeEntryPath('screen-home')])

    fireEvent.change(await screen.findByLabelText('标题'), { target: { value: '改过的标题' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByText('服务端拒绝了「标题」：内容太长。')).toBeInTheDocument()
  })

  it('restores the builtin content only after confirmation', async () => {
    const api = installApi()
    const { router } = renderKnowledge([knowledgeEntryPath('screen-home')])

    fireEvent.click(await screen.findByRole('button', { name: '知识条目操作' }))
    const menu = await screen.findByRole('dialog', { name: '首页布局' })
    fireEvent.click(within(menu).getByRole('button', { name: '恢复默认' }))

    const dialog = await screen.findByRole('alertdialog', { name: '恢复「首页布局」的内置内容？' })
    expect(api.deletes()).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '恢复默认' }))

    await waitFor(() => expect(api.deletes()).toHaveLength(1))
    expect(api.deletes()[0]?.path).toBe('/api/agent/knowledge/screen-home')
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings/knowledge'))
  })

  it('deletes a custom entry only after confirmation', async () => {
    const api = installApi()
    const { router } = renderKnowledge([knowledgeEntryPath('gacha-rule')])

    expect(await screen.findByLabelText('标题')).toHaveValue('抽卡换算')
    fireEvent.click(screen.getByRole('button', { name: '知识条目操作' }))
    const menu = await screen.findByRole('dialog', { name: '抽卡换算' })
    fireEvent.click(within(menu).getByRole('button', { name: '删除' }))

    const dialog = await screen.findByRole('alertdialog', { name: '删除自定义条目「抽卡换算」？' })
    expect(api.deletes()).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '删除条目' }))

    await waitFor(() => expect(api.deletes()).toHaveLength(1))
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/settings/knowledge'))
  })

  it('does not offer to delete an untouched builtin entry', async () => {
    installApi()
    renderKnowledge([knowledgeEntryPath('account-resources')])

    expect(await screen.findByLabelText('标题')).toHaveValue('查询账号资源')
    expect(screen.queryByRole('button', { name: '知识条目操作' })).not.toBeInTheDocument()
  })

  it('blocks leaving with unsaved changes until the user decides', async () => {
    installApi()
    const { router } = renderKnowledge(['/more/settings/knowledge', knowledgeEntryPath('screen-home')])

    fireEvent.change(await screen.findByLabelText('标题'), { target: { value: '改过的标题' } })
    fireEvent.click(screen.getByRole('button', { name: '返回' }))

    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的更改？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '继续编辑' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/more/settings/knowledge/screen-home')
    expect(screen.getByLabelText('标题')).toHaveValue('改过的标题')
  })
})
