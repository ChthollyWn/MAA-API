import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import SchedulesPage from '@/routes/schedules'
import type { components } from '@/types/api'
import type { TaskTypeSchema } from '@/features/tasks'
import taskTypesFixture from '@/features/tasks/task-types.fixture.json'
import { buildWeeklyCron, parseWeeklyCron } from './schedule-time'

type ScheduleView = components['schemas']['ScheduleView']
const taskTypes = taskTypesFixture as unknown as { items: TaskTypeSchema[]; total: number; page: number; size: number }

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function savedSchedule(overrides: Partial<ScheduleView> = {}): ScheduleView {
  return {
    id: 'schedule-1',
    name: '周末日常',
    cron: '0 7 * * 0,6',
    timezone: 'Asia/Shanghai',
    template: [{ name: 'StartUp', client_type: 'Official' }],
    enabled: true,
    priority: 2,
    next_run_at: '2026-09-26T23:00:00Z',
    last_run_at: '2026-09-20T23:00:00Z',
    last_run_result: {
      pipeline_id: null,
      status: 'skipped',
      started_at: '2026-09-20T23:00:00Z',
      finished_at: '2026-09-20T23:00:00Z',
      error: { message: '同一 schedule 仍有未完成流水线' },
    },
    recent_runs: [
      {
        pipeline_id: null,
        status: 'skipped',
        started_at: '2026-09-20T23:00:00Z',
        finished_at: '2026-09-20T23:00:00Z',
        error: { message: '同一 schedule 仍有未完成流水线' },
      },
      {
        pipeline_id: 'pipeline-older',
        status: 'completed',
        started_at: '2026-09-19T23:00:00Z',
        finished_at: '2026-09-19T23:05:00Z',
        error: null,
      },
    ],
    ...overrides,
  }
}

function installFetch(initial: ScheduleView[] = [], options: { createStatus?: number } = {}) {
  let schedules = initial
  const requests: Request[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const url = new URL(request.url)
    if (url.pathname === '/api/schedules' && request.method === 'GET') return jsonResponse({ items: schedules, total: schedules.length })
    if (url.pathname === '/api/tasks/types') return jsonResponse(taskTypes)
    if (url.pathname === '/api/schedules' && request.method === 'POST') {
      if (options.createStatus && options.createStatus >= 400) {
        return jsonResponse({ error: { code: 'SCHEDULE_NAME_CONFLICT', message: '已存在同名定时任务' } }, options.createStatus)
      }
      const body = JSON.parse(await request.clone().text()) as Record<string, unknown>
      const created = savedSchedule({ ...body, id: 'schedule-new' } as Partial<ScheduleView>)
      schedules = [created, ...schedules]
      return jsonResponse(created, 201)
    }
    if (url.pathname === '/api/schedules/schedule-1' && request.method === 'PATCH') {
      const body = JSON.parse(await request.clone().text()) as Partial<ScheduleView>
      schedules = schedules.map((item) => item.id === 'schedule-1' ? { ...item, ...body } : item)
      return jsonResponse(schedules[0])
    }
    if (url.pathname === '/api/schedules/schedule-1' && request.method === 'PUT') {
      const body = JSON.parse(await request.clone().text()) as Partial<ScheduleView>
      schedules = schedules.map((item) => item.id === 'schedule-1' ? { ...item, ...body } : item)
      return jsonResponse(schedules[0])
    }
    if (url.pathname === '/api/schedules/schedule-1/run' && request.method === 'POST') {
      return jsonResponse({ schedule_id: 'schedule-1', pipeline_id: 'pipeline-run', status: 'PENDING', priority: 2 }, 202)
    }
    if (url.pathname === '/api/schedules/schedule-1' && request.method === 'DELETE') {
      schedules = schedules.filter((item) => item.id !== 'schedule-1')
      return new Response(null, { status: 204 })
    }
    return jsonResponse({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests, fetchMock }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><SchedulesPage /></MemoryRouter></QueryClientProvider>)
}

function renderPageWithNavigationState(state: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[{ pathname: '/more/schedules', state }]}><Routes><Route path="/more/schedules" element={<SchedulesPage />} /></Routes></MemoryRouter></QueryClientProvider>)
}

describe('schedule editor and API integration', () => {
  beforeEach(() => vi.spyOn(window, 'confirm').mockReturnValue(true))
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('creates a schema-driven task template with a POSIX weekly cron', async () => {
    const network = installFetch()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建第一条' }))
    fireEvent.change(screen.getByLabelText('命名任务组名称'), { target: { value: '每周日启动' } })
    for (const weekday of ['周一', '周二', '周三', '周四', '周五', '周六']) {
      fireEvent.click(screen.getByRole('button', { name: weekday }))
    }
    fireEvent.change(screen.getByLabelText('执行时间'), { target: { value: '19:30' } })
    fireEvent.change(screen.getByRole('combobox', { name: '要添加的任务类型' }), { target: { value: 'StartUp' } })
    expect(await screen.findByRole('heading', { level: 4, name: '开始唤醒' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '创建 schedule' }))

    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/schedules')).toBe(true))
    const request = network.requests.find((item) => item.method === 'POST' && new URL(item.url).pathname === '/api/schedules')!
    expect(JSON.parse(await request.clone().text())).toMatchObject({
      name: '每周日启动',
      cron: '30 19 * * 0',
      timezone: 'Asia/Shanghai',
      priority: 2,
      template: [{ name: 'StartUp' }],
    })
    expect(await screen.findByText('定时任务已创建。')).toBeInTheDocument()
    expect(network.requests.filter((item) => item.method === 'GET' && new URL(item.url).pathname.startsWith('/api/schedules/'))).toHaveLength(0)
  })

  it('renders API recent results directly, handles skipped status, and runs/deletes with confirmation', async () => {
    const network = installFetch([savedSchedule()])
    renderPage()
    expect(await screen.findByRole('heading', { name: '周末日常' })).toBeInTheDocument()
    expect(screen.getByText('已跳过（该定时任务仍有未完成流水线）')).toBeInTheDocument()
    expect(screen.getByText('同一 schedule 仍有未完成流水线')).toBeInTheDocument()
    expect(network.requests.filter((request) => request.method === 'GET' && new URL(request.url).pathname.startsWith('/api/schedules/'))).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '立即运行' }))
    await waitFor(() => expect(network.requests.some((request) => request.method === 'POST' && new URL(request.url).pathname === '/api/schedules/schedule-1/run')).toBe(true))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('立即运行'))

    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(screen.getByText('还没有定时任务')).toBeInTheDocument())
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('删除定时任务'))
    expect(network.requests.some((request) => request.method === 'DELETE' && new URL(request.url).pathname === '/api/schedules/schedule-1')).toBe(true)
  })

  it('shows server conflict feedback without hiding the editor', async () => {
    installFetch([], { createStatus: 409 })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建第一条' }))
    fireEvent.change(screen.getByLabelText('命名任务组名称'), { target: { value: '重复日常' } })
    fireEvent.change(screen.getByRole('combobox', { name: '要添加的任务类型' }), { target: { value: 'StartUp' } })
    await screen.findByRole('heading', { level: 4, name: '开始唤醒' })
    fireEvent.click(screen.getByRole('button', { name: '创建 schedule' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('名称已被使用')
    expect(screen.getByRole('heading', { name: '新建定时任务' })).toBeInTheDocument()
  })

  it('opens the schedule editor with a pipeline template passed through router state', async () => {
    installFetch()
    renderPageWithNavigationState({ apiConsolePrefill: { template: [{ name: 'Fight', stage: '1-7' }] } })

    expect(await screen.findByRole('heading', { name: '新建定时任务' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '刷理智' })).toBeInTheDocument()
    expect(localStorage.getItem('maa.api-console.schedule-template')).toBeNull()
  })

  it('keeps an unrecognized existing cron visible and saves it unchanged until the user edits it', async () => {
    const network = installFetch([savedSchedule({ cron: '0 7 * * MON-FRI' })])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))
    const rawCron = screen.getByLabelText('编辑 cron 原值')
    expect(rawCron).toHaveValue('0 7 * * MON-FRI')
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(network.requests.some((item) => item.method === 'PUT' && new URL(item.url).pathname === '/api/schedules/schedule-1')).toBe(true))
    const request = network.requests.find((item) => item.method === 'PUT' && new URL(item.url).pathname === '/api/schedules/schedule-1')!
    expect(JSON.parse(await request.clone().text())).toMatchObject({ cron: '0 7 * * MON-FRI' })
  })
})

describe('weekly schedule cron conversion', () => {
  it('uses cron Sunday 0 for a Sunday-only schedule', () => {
    expect(buildWeeklyCron([0], '19:30')).toBe('30 19 * * 0')
    expect(parseWeeklyCron('30 19 * * 0', 'Asia/Shanghai')).toEqual({
      weekdays: [0],
      time: '19:30',
      timezone: 'Asia/Shanghai',
    })
  })

  it('round trips daily schedules using wildcard weekdays', () => {
    const cron = buildWeeklyCron([0, 1, 2, 3, 4, 5, 6], '07:00')
    expect(cron).toBe('0 7 * * *')
    expect(parseWeeklyCron(cron!, 'UTC')?.weekdays).toEqual([0, 1, 2, 3, 4, 5, 6])
  })

  it('round trips multiple selected weekdays in sorted cron order', () => {
    const cron = buildWeeklyCron([5, 1, 3], '08:05')
    expect(cron).toBe('5 8 * * 1,3,5')
    expect(parseWeeklyCron(cron!)?.weekdays).toEqual([1, 3, 5])
  })

  it('round trips a single weekday and accepts the standard Sunday alias 7', () => {
    expect(buildWeeklyCron([2], '00:00')).toBe('0 0 * * 2')
    expect(parseWeeklyCron('0 0 * * 7')?.weekdays).toEqual([0])
  })

  it('keeps wall-clock timezone separate from cron and rejects invalid visual values', () => {
    const schedule = parseWeeklyCron(buildWeeklyCron([1], '23:59')!, 'Pacific/Auckland')
    expect(schedule).toEqual({ weekdays: [1], time: '23:59', timezone: 'Pacific/Auckland' })
    expect(buildWeeklyCron([], '12:00')).toBeNull()
    expect(buildWeeklyCron([1], '24:00')).toBeNull()
    expect(parseWeeklyCron('*/5 * * * *', 'UTC')).toBeNull()
  })
})
