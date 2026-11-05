import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Toaster, toast } from '@/components/ui/toaster'
import GoalDetailPage from './GoalDetailPage'
import GoalsPage from './GoalsPage'

/**
 * M82 §6 的三条用例：新建目标的请求体与跳转、暂停 / 取消的状态写入、
 * 详情页的里程碑与规划状态。另用同一份夹具覆盖进度快照与「查看规划工单」。
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

function goalDetail(overrides: Json & { id: string }): Json {
  return {
    title: '银灰精二专三',
    status: 'active',
    description: null,
    target: { text: '月底前把银灰练到精二、三技能专三', operators: [], run_times: [] },
    deadline: '2026-11-30T14:00:00Z',
    planning_ticket_id: 't-plan',
    upgrade_plan_id: null,
    schedule_ids: [],
    review_cron: '0 22 * * *',
    next_review_at: '2026-11-21T14:00:00Z',
    last_review_at: null,
    progress: { planning: 'pending' },
    created_at: '2026-11-20T03:00:00Z',
    updated_at: '2026-11-20T03:00:00Z',
    ended_at: null,
    milestones: [],
    tickets: [],
    ...overrides,
  }
}

function summaryOf(detail: Json): Json {
  const milestones = Array.isArray(detail.milestones) ? (detail.milestones as Json[]) : []
  return {
    id: detail.id,
    title: detail.title,
    status: detail.status,
    deadline: detail.deadline ?? null,
    milestones_total: milestones.length,
    milestones_done: milestones.filter((milestone) => milestone.status === 'done').length,
    next_review_at: detail.next_review_at ?? null,
    updated_at: detail.updated_at,
  }
}

interface ApiOptions {
  details?: Json[]
}

function installApi(options: ApiOptions = {}) {
  const details = (options.details ?? [goalDetail({ id: 'g-1' })]).map((item) => ({ ...item }))
  let summaries = details.map(summaryOf)
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

    if (url.pathname === '/api/settings/schema') {
      return json({
        items: [
          {
            key: 'goals.default_review_cron',
            label: '目标默认复盘时间',
            group: 'Agent',
            type: 'string',
            description: '五段 cron，按北京时间',
            hot_action: 'none',
          },
        ],
      })
    }
    if (url.pathname === '/api/settings' && request.method === 'GET') {
      return json({
        items: [{ key: 'goals.default_review_cron', label: '目标默认复盘时间', group: 'Agent', value: '30 21 * * *', source: 'db' }],
        total: 1,
      })
    }

    if (url.pathname === '/api/agent/goals' && request.method === 'GET') {
      const wanted = url.searchParams.getAll('status')
      const items = summaries.filter((item) => wanted.length === 0 || wanted.includes(String(item.status)))
      return json({ items, total: items.length, page: 1, size: 20 })
    }

    if (url.pathname === '/api/agent/goals' && request.method === 'POST') {
      const payload = (body ?? {}) as Json
      const created = goalDetail({ ...payload, id: 'g-new' })
      details.push(created)
      summaries = [summaryOf(created), ...summaries]
      return json(created, 201)
    }

    const match = /^\/api\/agent\/goals\/([^/]+)$/.exec(url.pathname)
    if (match) {
      const id = decodeURIComponent(match[1]!)
      const current = details.find((item) => item.id === id)
      if (!current) return json({ error: { code: 'GOAL_NOT_FOUND', message: '没有这个目标' } }, 404)
      if (request.method === 'GET') return json(current)
      if (request.method === 'PUT') {
        const payload = (body ?? {}) as Json
        const next: Json = {
          ...current,
          ...payload,
          ended_at: payload.status === 'cancelled' ? '2026-11-21T02:00:00Z' : current.ended_at,
          next_review_at: payload.status === 'active' ? current.next_review_at : null,
        }
        const index = details.findIndex((item) => item.id === id)
        details[index] = next
        summaries = summaries.map((item) => (item.id === id ? { ...item, status: payload.status } : item))
        return json(next)
      }
    }

    return json({ error: { code: 'NOT_FOUND', message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })

  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    posts: () => requests.filter((item) => item.method === 'POST'),
    puts: () => requests.filter((item) => item.method === 'PUT'),
  }
}

function renderRoutes(initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/goals', element: <GoalsPage /> },
      { path: '/more/goals/:goalId', element: <GoalDetailPage /> },
      { path: '/tickets/:ticketId', element: <div>工单页</div> },
    ],
    { initialEntries: [initialEntry] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
  return router
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

describe('目标页', () => {
  it('create dialog posts goal and navigates', async () => {
    const api = installApi()
    const router = renderRoutes('/more/goals')

    expect(await screen.findByText('银灰精二专三')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '新建目标' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '新建目标' })).toBeInTheDocument()
    // 默认复盘时间取设置里的 cron 换算：30 21 * * * → 21:30。
    await waitFor(() => expect(within(dialog).getByLabelText('复盘时间')).toHaveValue('21:30'))
    expect(within(dialog).getByText('写清要达成什么，例如「银灰精二、三技能专三」')).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: '新材料目标' } })
    fireEvent.change(within(dialog).getByLabelText('目标'), { target: { value: '月底前攒够 100 个聚酸酯' } })
    fireEvent.change(within(dialog).getByLabelText('补充说明'), { target: { value: '不要用源石' } })
    fireEvent.change(within(dialog).getByLabelText('截止'), { target: { value: '2026-11-30T22:00' } })
    fireEvent.change(within(dialog).getByLabelText('复盘时间'), { target: { value: '22:15' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '新建目标' }))

    await waitFor(() => expect(api.posts()).toHaveLength(1))
    expect(api.posts()[0]!.path).toBe('/api/agent/goals')
    expect(api.posts()[0]!.body).toEqual({
      title: '新材料目标',
      description: '不要用源石',
      target: { text: '月底前攒够 100 个聚酸酯' },
      deadline: new Date('2026-11-30T22:00').toISOString(),
      review_cron: '15 22 * * *',
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/more/goals/g-new'))
    expect(await screen.findByText('已新建目标')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 1, name: '新材料目标' })).toBeInTheDocument()
  })

  it('pause and cancel send status', async () => {
    const api = installApi()
    renderRoutes('/more/goals/g-1')

    fireEvent.click(await screen.findByRole('button', { name: '暂停目标' }))
    await waitFor(() => expect(api.puts()).toHaveLength(1))
    expect(api.puts()[0]!.path).toBe('/api/agent/goals/g-1')
    expect(api.puts()[0]!.body).toEqual({ status: 'paused' })
    // 状态徽章与 Toast 都会写「已暂停」。
    expect(await screen.findByRole('button', { name: '恢复目标' })).toBeInTheDocument()
    expect((await screen.findAllByText('已暂停')).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: '取消目标' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('取消目标？')).toBeInTheDocument()
    expect(within(dialog).getByText('停用它的定时任务，并停止关联的养成计划。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '取消目标' }))

    await waitFor(() => expect(api.puts()).toHaveLength(2))
    expect(api.puts()[1]!.body).toEqual({ status: 'cancelled' })
    await waitFor(() => expect(screen.queryByRole('button', { name: '取消目标' })).not.toBeInTheDocument())
    expect((await screen.findAllByText('已取消')).length).toBeGreaterThan(0)
  })

  it('detail shows milestones and planning state', async () => {
    installApi({
      details: [
        goalDetail({
          id: 'g-1',
          last_review_at: '2026-11-20T14:00:00Z',
          milestones: [
            { id: 'm-1', ord: 1, title: '精二银灰', due_at: '2026-11-20T14:00:00Z', status: 'done', metrics: {} },
            { id: 'm-2', ord: 2, title: '三技能专三', due_at: '2026-11-28T14:00:00Z', status: 'active', metrics: {} },
          ],
          progress: {
            planning: 'pending',
            last_snapshot: { plan: { id: 'plan-1', status: 'active', remaining_stages: 2, remaining_runs: 6 } },
          },
        }),
        goalDetail({
          id: 'g-2',
          title: '攒聚酸酯',
          planning_ticket_id: 't-plan-2',
          progress: { planning: 'invalid_details', notes: ['规划结果不合法：规划结果缺里程碑'] },
        }),
      ],
    })
    const router = renderRoutes('/more/goals/g-1')

    expect(await screen.findByRole('heading', { level: 1, name: '银灰精二专三' })).toBeInTheDocument()
    const milestoneSection = screen.getByRole('region', { name: '里程碑' })
    expect(within(milestoneSection).getByText('1. 精二银灰')).toBeInTheDocument()
    expect(within(milestoneSection).getByText('2. 三技能专三')).toBeInTheDocument()
    expect(within(milestoneSection).getByText('已完成')).toBeInTheDocument()
    expect(within(milestoneSection).getByText('进行中')).toBeInTheDocument()
    expect(within(milestoneSection).getByText('规划中')).toBeInTheDocument()

    const progressSection = screen.getByRole('region', { name: '进度' })
    expect(within(progressSection).getByText('剩余 2 关 · 6 场')).toBeInTheDocument()
    expect(within(progressSection).getByText('最近复盘')).toBeInTheDocument()

    await act(async () => {
      await router.navigate('/more/goals/g-2')
    })
    expect(await screen.findByText('规划结果不合法：规划结果缺里程碑')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看规划工单' })).toHaveAttribute('href', '/tickets/t-plan-2')
  })
})
