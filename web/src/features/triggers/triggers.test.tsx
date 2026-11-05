import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Toaster, toast } from '@/components/ui/toaster'
import TriggersPage from './TriggersPage'

/**
 * M81 §6 的四条用例：切档位发完整内容、审阅按钮 POST 并重取、总开关 hint、
 * 两个空状态。另加恢复默认 / 删除规则与 `TRIGGER_RULE_INVALID` 的字段标记。
 */

interface RecordedRequest {
  method: string
  path: string
  body: unknown
}

type Json = Record<string, unknown>

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function pageOf(items: unknown[]): Response {
  return json({ items, total: items.length, page: 1, size: 20 })
}

const SANITY_POLICY = '理智估计已满。没有排队或运行中的流水线时 accept：建议提交一条只用现有理智的刷图流水线。'

function stats(overrides: Json = {}): Json {
  return {
    observations_30d: 0,
    reviewed_30d: 0,
    good_30d: 0,
    bad_30d: 0,
    tickets_30d: 0,
    tickets_bad_30d: 0,
    suggest_auto: false,
    suggest_observe: false,
    ...overrides,
  }
}

/** 一条生效规则；字段与 `GET /api/agent/trigger-rules` 的返回同形。 */
function rule(overrides: Json & { id: string }): Json {
  return {
    title: overrides.id,
    source: null,
    kind_pattern: '*',
    conditions: {},
    mode: 'observe',
    effective_mode: 'observe',
    worker: 'daily_ops',
    policy: '',
    cooldown_seconds: 0,
    cron: null,
    enabled: true,
    origin: 'builtin',
    modified: false,
    gated_by: null,
    next_fire_at: null,
    stats: stats(),
    ...overrides,
  }
}

function defaultRules(): Json[] {
  return [
    rule({
      id: 'sanity-full',
      title: '理智已满',
      source: 'system',
      kind_pattern: 'system.sanity_full',
      worker: 'daily_ops',
      policy: SANITY_POLICY,
      cooldown_seconds: 21600,
      stats: stats({ observations_30d: 3, reviewed_30d: 2, good_30d: 2, suggest_auto: true }),
    }),
    rule({
      id: 'diagnose-pipeline-failure',
      title: '诊断流水线失败',
      source: 'pipeline',
      kind_pattern: 'pipeline.failed',
      mode: 'auto',
      effective_mode: 'off',
      worker: 'diagnose',
      gated_by: 'agent.auto_diagnose_failures',
    }),
    rule({
      id: 'update-available',
      title: '检查更新',
      source: 'system',
      kind_pattern: 'system.update_available',
      conditions: { target: ['core', 'resource'] },
      origin: 'override',
      modified: true,
    }),
    rule({
      id: 'custom-nightly',
      title: '每晚清日常',
      source: 'web',
      kind_pattern: 'schedule.fired',
      mode: 'auto',
      effective_mode: 'auto',
      worker: 'daily_ops',
      cron: '0 4 * * *',
      origin: 'user',
      stats: stats({ tickets_30d: 6, tickets_bad_30d: 3, suggest_observe: true }),
    }),
  ]
}

function defaultSpecs(): Json[] {
  return [
    { name: 'daily_ops', title: '日常任务', kind: 'worker', description: '', model_role: 'worker', spec_version: 'v1', tools: [], delegates: [], version: 1 },
    { name: 'ops', title: '运维', kind: 'worker', description: '', model_role: 'worker', spec_version: 'v1', tools: [], delegates: [], version: 1 },
    { name: 'diagnose', title: '诊断', kind: 'worker', description: '', model_role: 'worker', spec_version: 'v1', tools: [], delegates: [], version: 1 },
    { name: 'orchestrator', title: '总控', kind: 'orchestrator', description: '', model_role: 'orchestrator', spec_version: 'v1', tools: [], delegates: [], version: 1 },
  ]
}

function defaultObservations(): Json[] {
  return [
    {
      id: 11,
      trigger_id: 101,
      rule_id: 'sanity-full',
      decision: {
        decision: 'accept',
        worker: 'daily_ops',
        title: '清理理智',
        brief: '用现有理智刷 1-7，不用理智药。',
        scope: '只用现有理智',
        reason: '没有排队任务，接下来一小时没有定时任务。',
      },
      verdict: null,
      reviewed_at: null,
      created_at: '2026-11-05T04:00:00Z',
      trigger_kind: 'system.sanity_full',
      trigger_summary: '理智估计已满（135/135）',
      thread_key: 'system',
    },
    {
      id: 12,
      trigger_id: 102,
      rule_id: 'sanity-full',
      decision: {
        decision: 'stop',
        worker: 'daily_ops',
        title: null,
        brief: null,
        scope: null,
        reason: '设备已经不可用，刷图工单应当停下。',
      },
      verdict: 'bad',
      reviewed_at: '2026-11-05T05:00:00Z',
      created_at: '2026-11-04T04:00:00Z',
      trigger_kind: 'system.sanity_full',
      trigger_summary: '理智估计已满（120/135）',
      thread_key: 'system',
    },
  ]
}

function defaultTriggers(): Json[] {
  return [
    {
      id: 345,
      source: 'pipeline',
      kind: 'pipeline.failed',
      thread_key: 'pipeline:p-1',
      sender: null,
      summary: '流水线日常刷图失败',
      payload: { pipeline_id: 'p-1' },
      dedupe_key: null,
      admission: 'accepted',
      rule_id: 'diagnose-pipeline-failure',
      eval_status: 'skipped',
      eval_reason: null,
      eval_run_id: null,
      ticket_id: 't-1',
      routed_to: 'ticket',
      created_at: '2026-11-05T04:00:00Z',
      processed_at: '2026-11-05T04:00:01Z',
    },
    {
      id: 346,
      source: 'system',
      kind: 'unknown.kind',
      thread_key: 'system',
      sender: null,
      summary: '未知事件',
      payload: {},
      dedupe_key: null,
      admission: 'rejected',
      rule_id: null,
      eval_status: 'skipped',
      eval_reason: 'unknown_kind',
      eval_run_id: null,
      ticket_id: null,
      routed_to: null,
      created_at: '2026-11-05T03:00:00Z',
      processed_at: '2026-11-05T03:00:00Z',
    },
  ]
}

interface ApiOptions {
  rules?: Json[]
  observations?: Json[]
  triggers?: Json[]
  /** 选中这个代理时 `PUT` 返回 400 `TRIGGER_RULE_INVALID`，用来测字段标记。 */
  failWorker?: string
}

function installApi(options: ApiOptions = {}) {
  let rules = (options.rules ?? defaultRules()).map((item) => ({ ...item }))
  const observations = (options.observations ?? defaultObservations()).map((item) => ({ ...item }))
  const triggers = (options.triggers ?? defaultTriggers()).map((item) => ({ ...item }))
  const specs = defaultSpecs()
  const requests: RecordedRequest[] = []

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    let body: unknown
    try {
      body = text ? (JSON.parse(text) as unknown) : undefined
    } catch {
      body = text
    }
    requests.push({ method: request.method, path: url.pathname, body })

    if (url.pathname === '/api/agent/trigger-rules' && request.method === 'GET') {
      return json({ items: rules, version: 'v-1' })
    }
    if (url.pathname === '/api/agent/specs' && request.method === 'GET') {
      return json({ items: specs })
    }
    if (url.pathname === '/api/agent/triggers' && request.method === 'GET') {
      return pageOf(triggers)
    }
    if (url.pathname === '/api/agent/observations' && request.method === 'GET') {
      const reviewed = url.searchParams.get('reviewed')
      const items = observations.filter((item) =>
        reviewed === null ? true : reviewed === 'true' ? item.verdict !== null : item.verdict === null,
      )
      return pageOf(items)
    }

    const verdict = /^\/api\/agent\/observations\/(\d+)\/verdict$/.exec(url.pathname)
    if (verdict && request.method === 'POST') {
      const id = Number(verdict[1])
      const item = observations.find((entry) => entry.id === id)
      if (!item) return json({ error: { code: 'OBSERVATION_NOT_FOUND', message: '没有这条观察记录' } }, 404)
      item.verdict = (body as Json | undefined)?.verdict
      item.reviewed_at = '2026-11-05T06:00:00Z'
      return json(item)
    }

    const ruleMatch = /^\/api\/agent\/trigger-rules\/([^/]+)$/.exec(url.pathname)
    if (ruleMatch) {
      const id = decodeURIComponent(ruleMatch[1]!)
      if (request.method === 'DELETE') {
        rules = rules.filter((item) => item.id !== id)
        return new Response(null, { status: 204 })
      }
      if (request.method === 'PUT') {
        const payload = (body ?? {}) as Json
        if (options.failWorker !== undefined && payload.worker === options.failWorker) {
          const message = `事件规则不合法：执行代理 ${options.failWorker} 不存在，请从列表里选一个`
          return json({ error: { code: 'TRIGGER_RULE_INVALID', message, details: { field: 'worker', reason: message } } }, 400)
        }
        const existing = rules.find((item) => item.id === id)
        const updated = existing
          ? { ...existing, ...payload, effective_mode: payload.enabled ? payload.mode : 'off' }
          : {
              id,
              ...payload,
              effective_mode: payload.mode ?? 'observe',
              origin: 'user',
              modified: false,
              gated_by: null,
              next_fire_at: null,
              stats: stats(),
            }
        rules = existing ? rules.map((item) => (item.id === id ? updated : item)) : [...rules, updated]
        return json(updated)
      }
    }

    return json({ error: { code: 'NOT_FOUND', message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })

  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    puts: () => requests.filter((item) => item.method === 'PUT'),
    deletes: () => requests.filter((item) => item.method === 'DELETE'),
    posts: () => requests.filter((item) => item.method === 'POST'),
    rule: (id: string) => rules.find((item) => item.id === id),
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/triggers', element: <TriggersPage /> },
      { path: '/tasks', element: <div>任务页</div> },
      { path: '/tickets/:ticketId', element: <div>工单页</div> },
    ],
    { initialEntries: ['/more/triggers'] },
  )
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
}

async function ruleCard(id: string): Promise<HTMLElement> {
  await waitFor(() => expect(document.querySelector(`[data-rule-id="${id}"]`)).not.toBeNull())
  return document.querySelector<HTMLElement>(`[data-rule-id="${id}"]`)!
}

async function observationCard(id: number): Promise<HTMLElement> {
  await waitFor(() => expect(document.querySelector(`[data-observation-id="${id}"]`)).not.toBeNull())
  return document.querySelector<HTMLElement>(`[data-observation-id="${id}"]`)!
}

/** jsdom 没有 ResizeObserver；Radix 的弹层会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  toast.dismiss()
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('事件规则页', () => {
  it('switching mode sends full rule', async () => {
    const api = installApi()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: '事件规则' })).toBeInTheDocument()
    const card = await ruleCard('sanity-full')
    expect(within(card).getByText('内置')).toBeInTheDocument()
    expect(within(card).getByText('可以改为自动')).toBeInTheDocument()

    const modes = within(card).getByRole('radiogroup', { name: '「理智已满」的档位' })
    fireEvent.click(within(modes).getByRole('radio', { name: '自动' }))

    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]!.path).toBe('/api/agent/trigger-rules/sanity-full')
    expect(api.puts()[0]!.body).toEqual({
      title: '理智已满',
      source: 'system',
      kind_pattern: 'system.sanity_full',
      conditions: {},
      mode: 'auto',
      worker: 'daily_ops',
      policy: SANITY_POLICY,
      cooldown_seconds: 21600,
      cron: null,
      enabled: true,
    })
    expect(await screen.findByText('已保存规则')).toBeInTheDocument()
  })

  it('verdict buttons post and refresh', async () => {
    const api = installApi()
    renderPage()

    fireEvent.click(await screen.findByRole('radio', { name: '待审阅' }))
    const card = await observationCard(11)
    expect(within(card).getByText('理智估计已满（135/135）')).toBeInTheDocument()
    expect(within(card).getByText(/清理理智/)).toBeInTheDocument()
    expect(within(card).getByText(/研判理由：/)).toBeInTheDocument()

    fireEvent.click(within(card).getByRole('button', { name: '判断对了' }))

    await waitFor(() => expect(api.posts()).toHaveLength(1))
    expect(api.posts()[0]!.path).toBe('/api/agent/observations/11/verdict')
    expect(api.posts()[0]!.body).toEqual({ verdict: 'good' })
    expect(await screen.findByText('已标记')).toBeInTheDocument()

    // 标记后整片失效：待审阅列表重取，已经判过的那条不再出现在待审阅里。
    await waitFor(() => {
      const gets = api.requests.filter(
        (item) => item.method === 'GET' && item.path === '/api/agent/observations',
      )
      expect(gets.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows gate hint for diagnose rules', async () => {
    installApi()
    renderPage()

    const card = await ruleCard('diagnose-pipeline-failure')
    expect(
      within(card).getByText('受「自动诊断流水线与内核故障」设置控制'),
    ).toBeInTheDocument()
    // 没有总开关的规则不显示这句。
    const sanity = await ruleCard('sanity-full')
    expect(
      within(sanity).queryByText('受「自动诊断流水线与内核故障」设置控制'),
    ).not.toBeInTheDocument()
  })

  it('empty states', async () => {
    installApi({ rules: [], observations: [], triggers: [] })
    renderPage()

    expect(await screen.findByText('还没有规则')).toBeInTheDocument()

    fireEvent.click(await screen.findByRole('radio', { name: '待审阅' }))
    expect(await screen.findByText('还没有观察记录')).toBeInTheDocument()
    expect(screen.getByText('把规则切到观察档就会开始记录')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看规则' }))
    expect(await screen.findByRole('button', { name: /新建规则/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: '事件记录' }))
    expect(await screen.findByText('还没有事件')).toBeInTheDocument()
    expect(screen.getByText('流水线结束、设备断开后会按时间列出')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去提交任务' })).toHaveAttribute('href', '/tasks')
  })

  it('restores overrides and deletes user rules', async () => {
    const api = installApi()
    renderPage()

    const override = await ruleCard('update-available')
    expect(within(override).getByText('已修改')).toBeInTheDocument()
    fireEvent.click(within(override).getByRole('button', { name: '恢复默认' }))

    await waitFor(() => expect(api.deletes()).toHaveLength(1))
    expect(api.deletes()[0]!.path).toBe('/api/agent/trigger-rules/update-available')
    expect(await screen.findByText('已恢复默认')).toBeInTheDocument()

    const userRule = await ruleCard('custom-nightly')
    expect(within(userRule).getByText('自定义')).toBeInTheDocument()
    expect(within(userRule).getByText('建议改回观察')).toBeInTheDocument()
    fireEvent.click(within(userRule).getByRole('button', { name: '删除规则' }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('删除规则？')).toBeInTheDocument()
    expect(within(dialog).getByText('已经建的工单不受影响。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(api.deletes()).toHaveLength(2))
    expect(api.deletes()[1]!.path).toBe('/api/agent/trigger-rules/custom-nightly')
    expect(await screen.findByText('已删除规则')).toBeInTheDocument()
  })

  it('marks the field named by TRIGGER_RULE_INVALID details', async () => {
    const api = installApi({ failWorker: 'diagnose' })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /新建规则/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('id'), { target: { value: 'custom-test' } })
    fireEvent.change(within(dialog).getByLabelText('名称'), { target: { value: '测试规则' } })
    fireEvent.change(within(dialog).getByLabelText('事件种类'), { target: { value: 'pipeline.failed' } })
    fireEvent.change(within(dialog).getByLabelText('执行代理'), { target: { value: 'diagnose' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(
      await screen.findByText('事件规则不合法：执行代理 diagnose 不存在，请从列表里选一个'),
    ).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(api.puts()).toHaveLength(1)

    fireEvent.change(within(dialog).getByLabelText('执行代理'), { target: { value: 'ops' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.puts()).toHaveLength(2))
    expect(api.puts()[1]!.path).toBe('/api/agent/trigger-rules/custom-test')
    expect(api.puts()[1]!.body).toEqual({
      title: '测试规则',
      source: null,
      kind_pattern: 'pipeline.failed',
      conditions: {},
      mode: 'observe',
      worker: 'ops',
      policy: '',
      cooldown_seconds: 0,
      cron: null,
      enabled: true,
    })
    expect(await screen.findByText('已保存规则')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.rule('custom-test')).toBeDefined()
  })
})
