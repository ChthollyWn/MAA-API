import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import SchedulesPage from '@/routes/schedules'
import ScheduleEditorPage from '@/routes/schedules/editor'
import taskTypesFixture from '@/features/tasks/task-types.fixture.json'
import { materializeDefaults, type TaskTypeSchema } from '@/features/tasks/form'
import type { components } from '@/types/api'

// 全量并行时 jsdom 很忙，默认 1 秒的 findBy / waitFor 会偶发超时。
configure({ asyncUtilTimeout: 5_000 })

type ScheduleView = components['schemas']['ScheduleView']
type Entry = string | { pathname: string; state?: unknown }

const typeByName = (name: string) => {
  const found = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}
/** M17 起定时任务模板也按 schema 预填默认值：读取、编辑、保存都会带上它们。 */
const withDefaults = (name: string, params: Record<string, unknown> = {}) => ({ name, ...materializeDefaults(typeByName(name), params) })

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

const nameConflict = { status: 409, body: { error: { code: 'SCHEDULE_NAME_CONFLICT', message: '已存在同名定时任务', details: { name: '重复日常' } } } }

interface ApiOptions {
  schedules?: ScheduleView[]
  /** POST 与 PUT 都返回这个错误。 */
  saveError?: { status: number; body: unknown }
  patchStatus?: number
  runStatus?: number
}

function installScheduleApi(options: ApiOptions = {}) {
  let schedules = [...(options.schedules ?? [])]
  const requests: Request[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const { pathname } = new URL(request.url)
    const method = request.method

    if (pathname === '/api/tasks/types') return jsonResponse(taskTypesFixture)
    if (pathname === '/api/resources/copilots') {
      return jsonResponse({ items: [{ id: 'copilot-1', name: '主线 1-7', meta: { stage_name: '1-7' } }], total: 1, page: 1, size: 200 })
    }
    if (pathname === '/api/resources/items') return jsonResponse([{ item_id: '30013', name: '固源岩', icon_url: null }])
    if (pathname === '/api/schedules' && method === 'GET') return jsonResponse({ items: schedules, total: schedules.length })
    if (pathname === '/api/schedules' && method === 'POST') {
      if (options.saveError) return jsonResponse(options.saveError.body, options.saveError.status)
      const body = await request.clone().json() as Partial<ScheduleView>
      const created = savedSchedule({ ...body, id: 'schedule-new', next_run_at: null, last_run_at: null, last_run_result: null, recent_runs: [] })
      schedules = [created, ...schedules]
      return jsonResponse(created, 201)
    }
    const run = pathname.match(/^\/api\/schedules\/([^/]+)\/run$/)
    if (run && method === 'POST') {
      if (options.runStatus && options.runStatus >= 400) {
        return jsonResponse({ error: { code: 'PIPELINE_ALREADY_RUNNING', message: '该定时任务仍有未完成流水线，完成后再立即运行' } }, options.runStatus)
      }
      return jsonResponse({ schedule_id: run[1], pipeline_id: 'pipeline-run', status: 'pending', priority: 2 }, 202)
    }
    const item = pathname.match(/^\/api\/schedules\/([^/]+)$/)
    if (item) {
      const index = schedules.findIndex((schedule) => schedule.id === decodeURIComponent(item[1]))
      if (index < 0) return jsonResponse({ error: { code: 'SCHEDULE_NOT_FOUND', message: '定时任务不存在' } }, 404)
      if (method === 'PATCH') {
        if (options.patchStatus && options.patchStatus >= 400) return jsonResponse({ error: { code: 'INTERNAL_ERROR', message: '服务暂时不可用' } }, options.patchStatus)
        schedules[index] = { ...schedules[index], ...(await request.clone().json() as Partial<ScheduleView>) }
        return jsonResponse(schedules[index])
      }
      if (method === 'PUT') {
        if (options.saveError) return jsonResponse(options.saveError.body, options.saveError.status)
        schedules[index] = { ...schedules[index], ...(await request.clone().json() as Partial<ScheduleView>) }
        return jsonResponse(schedules[index])
      }
      if (method === 'DELETE') {
        schedules = schedules.filter((_, position) => position !== index)
        return new Response(null, { status: 204 })
      }
    }
    return jsonResponse({ error: { code: 'NOT_FOUND', message: `Unexpected request ${method} ${pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const find = (method: string, path: string) => requests.filter((request) => request.method === method && new URL(request.url).pathname === path)
  const scheduleReads = () => requests.filter((request) => request.method === 'GET' && new URL(request.url).pathname.startsWith('/api/schedules/'))
  return { requests, find, scheduleReads }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderSchedules(entries: Entry[] = ['/tasks/schedules'], initialIndex?: number) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const router = createMemoryRouter([
    { path: '/tasks', element: <LocationProbe /> },
    { path: '/tasks/queue', element: <LocationProbe /> },
    { path: '/tasks/history/:pipelineId', element: <LocationProbe /> },
    { path: '/tasks/schedules', element: <SchedulesPage /> },
    { path: '/tasks/schedules/new', element: <ScheduleEditorPage /> },
    { path: '/tasks/schedules/:scheduleId', element: <ScheduleEditorPage /> },
  ], { initialEntries: entries, initialIndex })
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { router }
}

async function json(request: Request): Promise<Record<string, unknown>> {
  return request.clone().json() as Promise<Record<string, unknown>>
}

/** 从类型面板添加一个任务；添加后直接打开它的参数编辑器。 */
async function addTask(label: string) {
  fireEvent.click(await screen.findByRole('button', { name: '添加任务' }))
  const sheet = await screen.findByRole('dialog', { name: '添加任务' })
  fireEvent.click(within(sheet).getByRole('button', { name: new RegExp(`^${label}`) }))
  return screen.findByRole('dialog', { name: label })
}

async function closeEditor(editor: HTMLElement) {
  fireEvent.click(within(editor).getByRole('button', { name: '完成' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

function weekday(name: string) {
  return within(screen.getByRole('group', { name: '星期' })).getByRole('button', { name })
}

function taskRows() {
  return screen.getAllByRole('button', { name: /^第 \d+ 项：/ })
}

async function openRowMenu(name: string) {
  fireEvent.click(screen.getByRole('button', { name: `「${name}」的更多操作` }))
  return screen.findByRole('dialog', { name })
}

async function openEditorMenu(name: string) {
  fireEvent.click(screen.getByRole('button', { name: '定时任务操作' }))
  return screen.findByRole('dialog', { name })
}

describe('schedule list', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the latest skipped run with its error and runs or deletes only after confirmation (S3)', async () => {
    const network = installScheduleApi({ schedules: [savedSchedule()] })
    renderSchedules()

    const row = await screen.findByRole('link', { name: /周末日常/ })
    expect(row).toHaveAttribute('href', '/tasks/schedules/schedule-1')
    expect(row).toHaveTextContent('周末 07:00')
    expect(row).toHaveTextContent('1 个任务')
    expect(within(row).getByText('已跳过')).toBeInTheDocument()
    expect(within(row).getByText('同一 schedule 仍有未完成流水线')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '任务', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '定时' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('link', { name: '新建定时任务' })).toHaveAttribute('href', '/tasks/schedules/new')
    expect(screen.getByText(/daily_task\.json/)).toBeInTheDocument()
    expect(network.scheduleReads()).toHaveLength(0)

    fireEvent.click(within(await openRowMenu('周末日常')).getByRole('button', { name: '立即运行' }))
    let confirm = await screen.findByRole('alertdialog', { name: '立即运行「周末日常」？' })
    expect(confirm).toHaveTextContent('1 个任务')
    fireEvent.click(within(confirm).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(network.find('POST', '/api/schedules/schedule-1/run')).toHaveLength(0)

    fireEvent.click(within(await openRowMenu('周末日常')).getByRole('button', { name: '立即运行' }))
    confirm = await screen.findByRole('alertdialog', { name: '立即运行「周末日常」？' })
    fireEvent.click(within(confirm).getByRole('button', { name: '立即运行' }))
    await waitFor(() => expect(network.find('POST', '/api/schedules/schedule-1/run')).toHaveLength(1))
    expect(await json(network.find('POST', '/api/schedules/schedule-1/run')[0])).toEqual({})

    fireEvent.click(within(await openRowMenu('周末日常')).getByRole('button', { name: '删除' }))
    confirm = await screen.findByRole('alertdialog', { name: '删除「周末日常」？' })
    fireEvent.click(within(confirm).getByRole('button', { name: '删除定时任务' }))
    expect(await screen.findByText('还没有定时任务')).toBeInTheDocument()
    expect(network.find('DELETE', '/api/schedules/schedule-1')).toHaveLength(1)
  })

  it('explains a refused run on its row', async () => {
    installScheduleApi({ schedules: [savedSchedule()], runStatus: 409 })
    renderSchedules()

    await screen.findByRole('link', { name: /周末日常/ })
    fireEvent.click(within(await openRowMenu('周末日常')).getByRole('button', { name: '立即运行' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '立即运行' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('运行失败：该定时任务仍有未完成流水线，完成后再立即运行')
  })

  it('switches a schedule on and off from its row with PATCH (S8)', async () => {
    const network = installScheduleApi({ schedules: [savedSchedule()] })
    renderSchedules()

    const toggle = await screen.findByRole('switch', { name: '启用「周末日常」' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)
    await waitFor(() => expect(network.find('PATCH', '/api/schedules/schedule-1')).toHaveLength(1))
    expect(await json(network.find('PATCH', '/api/schedules/schedule-1')[0])).toEqual({ enabled: false })
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用「周末日常」' })).toHaveAttribute('aria-checked', 'false'))
    expect(screen.getByRole('link', { name: /周末日常/ })).toHaveTextContent('已停用')
  })

  it('restores the switch and explains a refused toggle in place', async () => {
    installScheduleApi({ schedules: [savedSchedule()], patchStatus: 500 })
    renderSchedules()

    fireEvent.click(await screen.findByRole('switch', { name: '启用「周末日常」' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('停用失败：服务暂时不可用')
    expect(screen.getByRole('switch', { name: '启用「周末日常」' })).toHaveAttribute('aria-checked', 'true')
  })

  it('forwards an API console handoff that lands on the list to a new schedule (S5)', async () => {
    installScheduleApi()
    const { router } = renderSchedules([{ pathname: '/tasks/schedules', state: { apiConsolePrefill: { template: [{ name: 'Fight', stage: '1-7' }] } } }])

    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules/new'))
    expect(await screen.findByRole('heading', { name: '新建定时任务', level: 1 })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /^第 1 项：\s*刷理智/ })).toHaveTextContent('1-7')
    await waitFor(() => expect(router.state.location.state).toBeNull())
  })
})

describe('schedule editor', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('creates a weekly schedule from the empty list and submits a POSIX cron with the fixed queue policy (S1)', async () => {
    const network = installScheduleApi()
    const { router } = renderSchedules()

    expect(await screen.findByText('还没有定时任务')).toBeInTheDocument()
    expect(screen.getByText(/daily_task\.json/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '新建定时任务' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules/new'))
    expect(await screen.findByRole('heading', { name: '新建定时任务', level: 1 })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: '定时' })).not.toBeInTheDocument()
    expect(screen.getByText(/定时任务以最低优先级排队/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/优先级/)).not.toBeInTheDocument()
    expect(screen.getByRole('switch', { name: '启用' })).toHaveAttribute('aria-checked', 'true')

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '每周日启动' } })
    for (const day of ['周一', '周二', '周三', '周四', '周五', '周六']) fireEvent.click(weekday(day))
    expect(weekday('周日')).toHaveAttribute('aria-pressed', 'true')
    expect(weekday('周一')).toHaveAttribute('aria-pressed', 'false')
    fireEvent.change(screen.getByLabelText('时间'), { target: { value: '19:30' } })
    expect(screen.getByText('周日 19:30 运行')).toBeInTheDocument()
    await closeEditor(await addTask('开始唤醒'))
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
    const [request] = network.find('POST', '/api/schedules')
    expect(await json(request)).toEqual({
      name: '每周日启动',
      cron: '30 19 * * 0',
      timezone: 'Asia/Shanghai',
      template: [withDefaults('StartUp')],
      enabled: true,
      priority: 2,
      misfire_grace_seconds: 300,
      catch_up: false,
      skip_if_running: true,
    })
    expect(await screen.findByRole('link', { name: /每周日启动/ })).toBeInTheDocument()
    expect(network.scheduleReads()).toHaveLength(0)
  })

  it('offers every day, workdays and the weekend as shortcuts and checks the form before sending', async () => {
    const network = installScheduleApi()
    renderSchedules(['/tasks/schedules/new'])

    await screen.findByRole('button', { name: '添加任务' })
    expect(screen.getByRole('button', { name: '每天' })).toHaveAttribute('aria-pressed', 'true')
    for (const day of ['周一', '周二', '周三', '周四', '周五', '周六', '周日']) fireEvent.click(weekday(day))
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))

    expect(screen.getByLabelText('名称')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('请填写名称。')).toBeInTheDocument()
    expect(screen.getByText('请至少选择一天。')).toBeInTheDocument()
    expect(screen.getByText('请至少添加一个任务。')).toBeInTheDocument()
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('名称')))
    expect(network.find('POST', '/api/schedules')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '工作日' }))
    expect(weekday('周一')).toHaveAttribute('aria-pressed', 'true')
    expect(weekday('周五')).toHaveAttribute('aria-pressed', 'true')
    expect(weekday('周六')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: '工作日' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('请至少选择一天。')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '周末' }))
    expect(weekday('周日')).toHaveAttribute('aria-pressed', 'true')
    expect(weekday('周一')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText('周末 07:00 运行')).toBeInTheDocument()
  })

  it('stores only the Copilot asset id and reports a missing asset on its row (S2)', async () => {
    const network = installScheduleApi()
    renderSchedules(['/tasks/schedules/new'])

    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: '每日剿灭' } })
    await closeEditor(await addTask('Copilot 作业'))
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))
    expect(await screen.findByText('第 1 个任务有 1 处错误。')).toBeInTheDocument()
    expect(taskRows()[0]).toHaveTextContent('1 处错误')
    expect(network.find('POST', '/api/schedules')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '修改' }))
    const copilot = await screen.findByRole('dialog', { name: 'Copilot 作业' })
    expect(within(copilot).getByText('请选择一个已保存的 Copilot 作业。')).toBeInTheDocument()
    const picker = await within(copilot).findByLabelText('Copilot 作业')
    await within(copilot).findByRole('option', { name: '主线 1-7 · 1-7' })
    fireEvent.change(picker, { target: { value: 'copilot-1' } })
    await closeEditor(copilot)
    expect(taskRows()[0]).toHaveTextContent('主线 1-7')
    expect(taskRows()[0]).not.toHaveTextContent('处错误')
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))

    await waitFor(() => expect(network.find('POST', '/api/schedules')).toHaveLength(1))
    const payload = await json(network.find('POST', '/api/schedules')[0])
    expect(payload.name).toBe('每日剿灭')
    expect(payload.template).toEqual([{ name: 'Copilot', asset_id: 'copilot-1', formation: false, support_unit_usage: 0 }])
  })

  it('keeps the editor open and marks the name when it is already taken (S4)', async () => {
    installScheduleApi({ saveError: nameConflict })
    const { router } = renderSchedules(['/tasks/schedules/new'])

    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: '重复日常' } })
    await closeEditor(await addTask('开始唤醒'))
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('名称已被使用')
    expect(alert).toHaveTextContent('已有同名的定时任务「重复日常」')
    expect(router.state.location.pathname).toBe('/tasks/schedules/new')
    expect(screen.getByRole('heading', { name: '新建定时任务', level: 1 })).toBeInTheDocument()
    expect(screen.getByLabelText('名称')).toHaveAttribute('aria-invalid', 'true')
    expect(taskRows()[0]).toHaveAccessibleName(/^第 1 项：\s*开始唤醒/)

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '周末日常 2' } })
    expect(screen.getByLabelText('名称')).not.toHaveAttribute('aria-invalid', 'true')
  })

  it('prefills a new schedule from an API console template once and clears the navigation state (S5)', async () => {
    const network = installScheduleApi()
    const { router } = renderSchedules([{ pathname: '/tasks/schedules/new', state: { apiConsolePrefill: { template: [{ name: 'Fight', stage: '1-7' }] } } }])

    expect(await screen.findByRole('heading', { name: '新建定时任务', level: 1 })).toBeInTheDocument()
    expect(screen.getByText('已载入 API 调试台收藏的流水线模板。设好星期和时间就能保存。')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /^第 1 项：\s*刷理智/ })).toHaveTextContent('1-7')
    await waitFor(() => expect(router.state.location.state).toBeNull())
    expect(router.state.historyAction).toBe('REPLACE')
    expect(localStorage.getItem('maa.api-console.schedule-template')).toBeNull()

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '调试台刷图' } })
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))
    await waitFor(() => expect(network.find('POST', '/api/schedules')).toHaveLength(1))
    expect((await json(network.find('POST', '/api/schedules')[0])).template).toEqual([withDefaults('Fight', { stage: '1-7' })])
  })

  it('carries the composer tasks and name from the list into a new schedule (T13)', async () => {
    const network = installScheduleApi()
    const prefill = { composerPrefill: { name: '每日刷图', template: [{ name: 'Fight', stage: '1-7', medicine: 2 }] } }
    const { router } = renderSchedules([{ pathname: '/tasks/schedules', state: prefill }])

    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules/new'))
    expect(await screen.findByLabelText('名称')).toHaveValue('每日刷图')
    expect(screen.getByText('已载入 1 个任务。设好星期和时间就能保存。')).toBeInTheDocument()
    const row = await screen.findByRole('button', { name: /^第 1 项：\s*刷理智/ })
    expect(row).toHaveTextContent('1-7')
    expect(row).toHaveTextContent('理智药 2')
    await waitFor(() => expect(router.state.location.state).toBeNull())

    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))
    await waitFor(() => expect(network.find('POST', '/api/schedules')).toHaveLength(1))
    expect(await json(network.find('POST', '/api/schedules')[0])).toMatchObject({
      name: '每日刷图',
      template: [{ name: 'Fight', stage: '1-7', medicine: 2 }],
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
  })

  it('edits a schedule on its own page, lists its recent runs and saves the whole schedule with PUT (S8)', async () => {
    const network = installScheduleApi({ schedules: [savedSchedule()] })
    const { router } = renderSchedules()

    fireEvent.click(await screen.findByRole('link', { name: /周末日常/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules/schedule-1'))
    expect(await screen.findByRole('heading', { name: '编辑定时任务', level: 1 })).toBeInTheDocument()
    expect(screen.getByLabelText('名称')).toHaveValue('周末日常')
    expect(weekday('周六')).toHaveAttribute('aria-pressed', 'true')
    expect(weekday('周日')).toHaveAttribute('aria-pressed', 'true')
    expect(weekday('周一')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByLabelText('时间')).toHaveValue('07:00')
    expect(await screen.findByRole('button', { name: /^第 1 项：\s*开始唤醒/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()

    const runs = screen.getByRole('region', { name: '最近运行' })
    expect(within(runs).getByText('已跳过')).toBeInTheDocument()
    expect(within(runs).getByText('同一 schedule 仍有未完成流水线')).toBeInTheDocument()
    expect(within(runs).getByRole('link', { name: /成功/ })).toHaveAttribute('href', '/tasks/history/pipeline-older')

    fireEvent.click(weekday('周一'))
    fireEvent.change(screen.getByLabelText('时间'), { target: { value: '08:30' } })
    expect(screen.getByText('有未保存的更改')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(network.find('PUT', '/api/schedules/schedule-1')).toHaveLength(1))
    expect(await json(network.find('PUT', '/api/schedules/schedule-1')[0])).toEqual({
      name: '周末日常',
      cron: '30 8 * * 0,1,6',
      timezone: 'Asia/Shanghai',
      template: [withDefaults('StartUp', { client_type: 'Official' })],
      enabled: true,
      priority: 2,
      misfire_grace_seconds: 300,
      catch_up: false,
      skip_if_running: true,
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
    expect(network.find('POST', '/api/schedules')).toHaveLength(0)
    expect(network.scheduleReads()).toHaveLength(0)
  })

  it('keeps an unrecognized cron as a raw expression, hides the weekly controls and saves it unchanged (S6)', async () => {
    const network = installScheduleApi({ schedules: [savedSchedule({ cron: '0 7 * * MON-FRI' })] })
    const { router } = renderSchedules()

    const row = await screen.findByRole('link', { name: /周末日常/ })
    expect(row).toHaveTextContent('cron 0 7 * * MON-FRI')
    fireEvent.click(row)
    expect(await screen.findByRole('heading', { name: '编辑定时任务', level: 1 })).toBeInTheDocument()
    expect(screen.getByLabelText('cron 表达式')).toHaveValue('0 7 * * MON-FRI')
    expect(screen.getByText('这个表达式超出按星期设置的范围，将原样保存。')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: '星期' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('时间')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '工作日早间' } })
    fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() => expect(network.find('PUT', '/api/schedules/schedule-1')).toHaveLength(1))
    expect(await json(network.find('PUT', '/api/schedules/schedule-1')[0])).toMatchObject({
      name: '工作日早间',
      cron: '0 7 * * MON-FRI',
      timezone: 'Asia/Shanghai',
      priority: 2,
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
  })

  it('maps template validation errors from the server onto the task row and its field', async () => {
    const network = installScheduleApi({
      saveError: {
        status: 422,
        body: {
          error: {
            code: 'VALIDATION_ERROR',
            message: '请求参数校验失败（1 处）',
            details: { fields: [{ loc: ['body', 'template', 1, 'Fight', 'stage'], msg: 'String should have at least 1 character', type: 'string_too_short' }] },
          },
        },
      },
    })
    renderSchedules(['/tasks/schedules/new'])

    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: '校验失败' } })
    await closeEditor(await addTask('开始唤醒'))
    await closeEditor(await addTask('刷理智'))
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))

    expect(await screen.findByText('第 2 个任务有 1 处错误。')).toBeInTheDocument()
    const [startUp, fight] = taskRows()
    expect(startUp).not.toHaveTextContent('处错误')
    expect(fight).toHaveTextContent('1 处错误')
    expect(screen.getByLabelText('名称')).not.toHaveAttribute('aria-invalid', 'true')

    fireEvent.click(screen.getByRole('button', { name: '修改' }))
    const editor = await screen.findByRole('dialog', { name: '刷理智' })
    expect(within(editor).getByText('String should have at least 1 character')).toHaveAttribute('role', 'alert')
    fireEvent.change(within(editor).getByLabelText('关卡名'), { target: { value: '1-7' } })
    expect(within(editor).queryByText('String should have at least 1 character')).not.toBeInTheDocument()
    await closeEditor(editor)
    expect(screen.queryByText(/个任务有 \d+ 处错误/)).not.toBeInTheDocument()
    expect(network.find('POST', '/api/schedules')).toHaveLength(1)
  })

  it('points an invalid timezone out inside the advanced section', async () => {
    installScheduleApi({
      saveError: {
        status: 400,
        body: { error: { code: 'SCHEDULE_CRON_INVALID', message: 'cron 必须是有效的五段标准表达式，时区须为 IANA 名称', details: { cron: '0 7 * * *', timezone: 'Mars/Olympus' } } },
      },
    })
    renderSchedules(['/tasks/schedules/new'])

    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: '火星日常' } })
    fireEvent.change(screen.getByLabelText('时区'), { target: { value: 'Mars/Olympus' } })
    await closeEditor(await addTask('开始唤醒'))
    fireEvent.click(screen.getByRole('button', { name: '新建定时任务' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('时间设置无效')
    const timezone = screen.getByLabelText('时区')
    expect(timezone).toHaveAttribute('aria-invalid', 'true')
    expect(timezone.closest('details')).toHaveAttribute('open')
  })

  it('asks before leaving an editor with unsaved changes', async () => {
    installScheduleApi({ schedules: [savedSchedule()] })
    const { router } = renderSchedules(['/tasks/schedules', '/tasks/schedules/schedule-1'], 1)

    fireEvent.change(await screen.findByLabelText('名称'), { target: { value: '周末日常（改）' } })
    const beforeUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(beforeUnload)
    expect(beforeUnload.defaultPrevented).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    let dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的更改？' })
    expect(dialog).toHaveTextContent('「周末日常」有未保存的更改')
    fireEvent.click(within(dialog).getByRole('button', { name: '继续编辑' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/tasks/schedules/schedule-1')
    expect(screen.getByLabelText('名称')).toHaveValue('周末日常（改）')

    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的更改？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '放弃更改' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
    expect(await screen.findByRole('link', { name: /周末日常/ })).toBeInTheDocument()
  })

  it('leaves an untouched editor without asking', async () => {
    installScheduleApi({ schedules: [savedSchedule()] })
    const { router } = renderSchedules(['/tasks/schedules', '/tasks/schedules/schedule-1'], 1)

    await screen.findByLabelText('名称')
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('runs and deletes from the editor menu only after confirmation, and deleting returns to the list', async () => {
    const network = installScheduleApi({ schedules: [savedSchedule()] })
    const { router } = renderSchedules(['/tasks/schedules', '/tasks/schedules/schedule-1'], 1)

    await screen.findByLabelText('名称')
    fireEvent.click(within(await openEditorMenu('周末日常')).getByRole('button', { name: '立即运行' }))
    let confirm = await screen.findByRole('alertdialog', { name: '立即运行「周末日常」？' })
    fireEvent.click(within(confirm).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(network.find('POST', '/api/schedules/schedule-1/run')).toHaveLength(0)

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '改了但没保存' } })
    fireEvent.click(within(await openEditorMenu('周末日常')).getByRole('button', { name: '立即运行' }))
    confirm = await screen.findByRole('alertdialog', { name: '立即运行「周末日常」？' })
    expect(confirm).toHaveTextContent('还没保存的更改不会生效')
    fireEvent.click(within(confirm).getByRole('button', { name: '立即运行' }))
    expect(await screen.findByText('「周末日常」已加入队列。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看队列' })).toHaveAttribute('href', '/tasks/queue')
    expect(network.find('POST', '/api/schedules/schedule-1/run')).toHaveLength(1)

    fireEvent.click(within(await openEditorMenu('周末日常')).getByRole('button', { name: '删除' }))
    confirm = await screen.findByRole('alertdialog', { name: '删除「周末日常」？' })
    fireEvent.click(within(confirm).getByRole('button', { name: '删除定时任务' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules'))
    expect(network.find('DELETE', '/api/schedules/schedule-1')).toHaveLength(1)
    expect(await screen.findByText('还没有定时任务')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog', { name: '放弃未保存的更改？' })).not.toBeInTheDocument()
  })

  it('explains a schedule that no longer exists', async () => {
    installScheduleApi({ schedules: [savedSchedule()] })
    renderSchedules(['/tasks/schedules/missing'])

    expect(await screen.findByText('找不到这个定时任务')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回定时任务' })).toHaveAttribute('href', '/tasks/schedules')
  })
})
