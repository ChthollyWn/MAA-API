import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { Toaster } from '@/components/ui/toaster'
import SkillsPage from '@/routes/skills'
import SkillEditorPage from '@/routes/skills/editor'
import { routeConfig } from '@/routes/router'
import { useAuth } from '@/stores/auth'
import { NEW_SKILL_PATH, SKILLS_PATH } from './format'

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

type Json = Record<string, unknown>

const BODIES: Record<string, string> = {
  'playbook-recruit': '## 公开招募\n先读首页，再提交流水线。',
  'task-preconditions': '## 任务前置检查（改）\n确认设备与理智。',
  'my-skill': '## 我的技能\n按用户要求执行。',
  'draft-skill': '## 草稿\n还没定稿。',
}

function skill(overrides: Json & { id: string }): Json {
  return {
    title: overrides.id,
    description: '需要时',
    tools: [],
    agents: [],
    source: 'builtin',
    modified: false,
    status: 'active',
    enabled: true,
    version: 1,
    files: [],
    updated_at: '2026-09-29T02:00:00',
    ...overrides,
    body: (overrides.body as string | undefined) ?? BODIES[overrides.id] ?? '正文',
  }
}

function defaultSkills(): Json[] {
  return [
    skill({
      id: 'playbook-recruit',
      title: '公开招募实操',
      description: '用户要你自动公开招募时',
      tools: ['submit_pipeline', 'wait_pipeline'],
      agents: ['general', 'daily_ops'],
      source: 'builtin',
      modified: false,
    }),
    skill({
      id: 'task-preconditions',
      title: '任务前置检查（改）',
      description: '开始任务前确认条件时',
      source: 'builtin',
      modified: true,
      builtin_title: '任务前置检查',
      builtin_description: '开始任务前确认条件时',
      builtin_body: '## 任务前置检查（内置）\n确认设备与理智。',
    }),
    skill({
      id: 'my-skill',
      title: '我的技能',
      description: '用户需要自定义流程时',
      source: 'user',
      tools: ['get_screenshot'],
      agents: ['general'],
    }),
    skill({
      id: 'draft-skill',
      title: '草稿技能',
      description: '还没定稿',
      source: 'generated',
      status: 'draft',
    }),
  ]
}

function defaultSpecs(): Json[] {
  return [
    { name: 'general', title: '执行官', kind: 'worker', description: '执行工单' },
    { name: 'daily_ops', title: '通用执行', kind: 'worker', description: '日常执行' },
    { name: 'orchestrator', title: '总控代理', kind: 'orchestrator', description: '分派工单' },
    { name: 'researcher', title: '研究子代理', kind: 'subagent', description: '查资料' },
  ]
}

function summary(item: Json): Json {
  const copy = { ...item }
  for (const key of [
    'body',
    'builtin',
    'builtin_title',
    'builtin_description',
    'builtin_tools',
    'builtin_agents',
    'builtin_body',
  ]) {
    delete copy[key]
  }
  return { ...copy, body_chars: String(item.body ?? '').length }
}

function detail(item: Json): Json {
  const body = String(item.body ?? '')
  const builtin = item.source === 'builtin'
    ? {
        id: item.id,
        title: (item.builtin_title as string | undefined) ?? item.title,
        description: (item.builtin_description as string | undefined) ?? item.description,
        tools: (item.builtin_tools as string[] | undefined) ?? item.tools,
        agents: (item.builtin_agents as string[] | undefined) ?? item.agents,
        body: (item.builtin_body as string | undefined) ?? body,
        version: 1,
      }
    : null
  return { ...item, body, builtin }
}

interface ApiOptions {
  skills?: Json[]
  specs?: Json[]
  put?: { status: number; body: unknown }
}

function installApi(options: ApiOptions = {}) {
  let skills = (options.skills ?? defaultSkills()).map((item) => ({ ...item }))
  const specs = options.specs ?? defaultSpecs()
  const requests: RecordedRequest[] = []
  const notFound = () => json({ error: { code: 'SKILL_NOT_FOUND', message: '技能不存在' } }, 404)
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    const body = text ? JSON.parse(text) as Json : undefined
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/agent/specs' && request.method === 'GET') return json({ items: specs })
    if (url.pathname === '/api/agent/skills' && request.method === 'GET') {
      return json({ items: skills.map(summary) })
    }
    const match = /^\/api\/agent\/skills\/([^/]+)$/.exec(url.pathname)
    if (match) {
      const id = decodeURIComponent(match[1]!)
      const found = skills.find((item) => item.id === id)
      if (request.method === 'GET') return found ? json(detail(found)) : notFound()
      if (request.method === 'PUT') {
        if (options.put) return json(options.put.body, options.put.status)
        const payload = body as Json
        if (found) {
          const updated = { ...found, ...payload, modified: found.source === 'builtin' }
          skills = skills.map((item) => (item.id === id ? updated : item))
          return json(detail(updated))
        }
        const created = skill({ ...(payload as Json), id, source: 'user' })
        skills = [...skills, created]
        return json(detail(created))
      }
      if (request.method === 'DELETE') {
        if (!found) return notFound()
        if (found.source === 'builtin') {
          if (!found.modified) return json({ error: { code: 'SKILL_BUILTIN_READONLY', message: '内置技能只读' } }, 409)
          const restored = {
            ...found,
            title: (found.builtin_title as string | undefined) ?? found.title,
            description: (found.builtin_description as string | undefined) ?? found.description,
            tools: (found.builtin_tools as string[] | undefined) ?? found.tools,
            agents: (found.builtin_agents as string[] | undefined) ?? found.agents,
            body: (found.builtin_body as string | undefined) ?? found.body,
            modified: false,
            enabled: true,
          }
          skills = skills.map((item) => (item.id === id ? restored : item))
          return new Response(null, { status: 204 })
        }
        skills = skills.filter((item) => item.id !== id)
        return new Response(null, { status: 204 })
      }
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const calls = (method: string) => requests.filter((request) => request.method === method)
  return {
    requests,
    gets: () => calls('GET'),
    puts: () => calls('PUT'),
    deletes: () => calls('DELETE'),
    list: () => skills,
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderSkills(entries: string[] = [SKILLS_PATH]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/skills', element: <SkillsPage /> },
      { path: '/more/skills/new', element: <SkillEditorPage /> },
      { path: '/more/skills/:skillId', element: <SkillEditorPage /> },
      { path: '*', element: <LocationProbe /> },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
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

describe('skills page', () => {
  it('lists builtin skills with agents and switch', async () => {
    installApi()
    renderSkills()

    expect(await screen.findByRole('heading', { level: 1, name: '技能' })).toBeInTheDocument()
    for (const heading of ['内置', '自定义', '草稿']) {
      expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    }

    const builtin = screen.getByRole('link', { name: /公开招募实操/ })
    expect(within(builtin).getByText('playbook-recruit')).toBeInTheDocument()
    expect(within(builtin).getByText('内置')).toBeInTheDocument()
    expect(within(builtin).getByText('执行官、通用执行')).toBeInTheDocument()

    const modified = screen.getByRole('link', { name: /任务前置检查（改）/ })
    expect(within(modified).getByText('已修改')).toBeInTheDocument()
    expect(within(modified).getByText('所有代理')).toBeInTheDocument()

    const custom = screen.getByRole('link', { name: /我的技能/ })
    expect(within(custom).getByText('自定义')).toBeInTheDocument()

    const draft = screen.getByRole('link', { name: /草稿技能/ })
    expect(within(draft).getByText('草稿')).toBeInTheDocument()

    expect(screen.getByRole('switch', { name: '启用「公开招募实操」' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('link', { name: '新建' })).toHaveAttribute('href', NEW_SKILL_PATH)

    fireEvent.click(screen.getByRole('button', { name: '技能 说明' }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('Agent 平时只看到技能的名字和说明，用到时才读全文。')

    const children = routeConfig.find((route) => route.path === '/')?.children ?? []
    for (const path of ['more/skills', 'more/skills/new', 'more/skills/:skillId']) {
      expect(children.some((route) => route.path === path)).toBe(true)
    }
  })

  it('toggle puts the whole skill back', async () => {
    const api = installApi()
    renderSkills()

    fireEvent.click(await screen.findByRole('switch', { name: '启用「公开招募实操」' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    const calls = api.requests.filter((request) => request.path === '/api/agent/skills/playbook-recruit')
    expect(calls.map((call) => call.method)).toEqual(['GET', 'PUT'])
    expect(api.puts()[0]?.body).toEqual({
      title: '公开招募实操',
      description: '用户要你自动公开招募时',
      body: BODIES['playbook-recruit'],
      tools: ['submit_pipeline', 'wait_pipeline'],
      agents: ['general', 'daily_ops'],
      enabled: false,
    })
    expect(await screen.findByText('已停用「公开招募实操」')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用「公开招募实操」' })).toHaveAttribute('aria-checked', 'false'))
  })
})

describe('skill editor', () => {
  it('editor saves, restores default and deletes', async () => {
    const api = installApi()
    const { router } = renderSkills([`${SKILLS_PATH}/playbook-recruit`])

    expect(await screen.findByLabelText('id')).toHaveValue('playbook-recruit')
    expect(screen.getByLabelText('id')).toHaveAttribute('readonly')
    expect(await screen.findByRole('checkbox', { name: /执行官/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /通用执行/ })).toBeChecked()
    expect(screen.queryByRole('checkbox', { name: /研究子代理/ })).not.toBeInTheDocument()
    expect(screen.getByText('内置原文')).toBeInTheDocument()
    const builtinDisclosure = screen.getByText('内置原文').closest('details')
    expect(builtinDisclosure).not.toBeNull()
    expect(within(builtinDisclosure!).getByText(/## 公开招募/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '公开招募实操（测试）' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/skills/playbook-recruit')
    expect(api.puts()[0]?.body).toEqual({
      title: '公开招募实操（测试）',
      description: '用户要你自动公开招募时',
      body: BODIES['playbook-recruit'],
      tools: ['submit_pipeline', 'wait_pipeline'],
      agents: ['general', 'daily_ops'],
      enabled: true,
    })
    expect(await screen.findByText('已保存技能')).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toBe(SKILLS_PATH))

    fireEvent.click(await screen.findByRole('link', { name: /公开招募实操（测试）/ }))
    fireEvent.click(await screen.findByRole('button', { name: '恢复默认' }))

    const restoreDialog = await screen.findByRole('alertdialog', { name: '恢复默认？' })
    expect(restoreDialog).toHaveTextContent('你的修改会被丢弃，换回内置内容。')
    fireEvent.click(within(restoreDialog).getByRole('button', { name: '恢复默认' }))

    await waitFor(() => expect(api.deletes()).toHaveLength(1))
    expect(api.deletes()[0]?.path).toBe('/api/agent/skills/playbook-recruit')
    expect(await screen.findByText('已恢复默认')).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toBe(SKILLS_PATH))

    fireEvent.click(await screen.findByRole('link', { name: /我的技能/ }))
    fireEvent.click(await screen.findByRole('button', { name: '删除技能' }))

    const deleteDialog = await screen.findByRole('alertdialog', { name: '删除技能？' })
    expect(deleteDialog).toHaveTextContent('删除后 Agent 不能再加载它，无法撤销。')
    fireEvent.click(within(deleteDialog).getByRole('button', { name: '删除技能' }))

    await waitFor(() => expect(api.deletes()).toHaveLength(2))
    expect(api.deletes()[1]?.path).toBe('/api/agent/skills/my-skill')
    expect(await screen.findByText('已删除技能')).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toBe(SKILLS_PATH))
  })

  it('editor marks invalid fields from SKILL_INVALID', async () => {
    const api = installApi({
      put: {
        status: 400,
        body: {
          error: {
            code: 'SKILL_INVALID',
            message: "技能内容不合法：未知工具 ['nope']",
            details: { skill_id: 'my-skill', field: 'tools', reason: "未知工具 ['nope']", unknown: ['nope'] },
          },
        },
      },
    })
    renderSkills([NEW_SKILL_PATH])

    fireEvent.change(await screen.findByLabelText('id'), { target: { value: 'my-skill' } })
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '我的技能' } })
    fireEvent.change(screen.getByLabelText('什么时候用'), { target: { value: '需要时' } })
    fireEvent.change(screen.getByLabelText('依赖的工具'), { target: { value: 'nope' } })
    expect(screen.getByText('0/20000')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('正文'), { target: { value: '## 正文' } })
    expect(screen.getByText('5/20000')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]?.path).toBe('/api/agent/skills/my-skill')
    expect(api.puts()[0]?.body).toEqual({
      title: '我的技能',
      description: '需要时',
      body: '## 正文',
      tools: ['nope'],
      agents: [],
      enabled: true,
    })
    expect(screen.getByLabelText('依赖的工具')).toHaveAttribute('aria-invalid', 'true')
    expect(await screen.findByText('这些工具不存在：nope')).toBeInTheDocument()
    expect(screen.getByText('技能内容不合法，请按字段提示修改。')).toBeInTheDocument()
  })
})
