import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TasksPage from '@/routes/tasks'
import { DynamicTaskForm, type JsonSchema, type TaskTypeSchema } from './index'
import taskTypesFixture from './task-types.fixture.json'

const taskTypeEnvelope = taskTypesFixture as unknown as {
  items: TaskTypeSchema[]
  total: number
  page: number
  size: number
}
const types = taskTypeEnvelope.items

const initialQueue = {
  running: { id: 'running-1', title: '正在运行', source: 'manual', priority: 0, status: 'RUNNING' },
  pending: [
    { id: 'queued-1', title: '定时任务', source: 'scheduled', priority: 2, status: 'PENDING', task_count: 1 },
    { id: 'queued-2', title: 'Agent 任务', source: 'agent', priority: 1, status: 'PENDING', task_count: 2 },
  ],
  paused: false,
  counts: { pending: 2, running: 1 },
}

interface MockOptions {
  validateStatus?: number
  validateBody?: unknown
  submitStatuses?: number[]
  historyPages?: Record<number, { items: Array<Record<string, unknown>>; total: number; page: number; size: number }>
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installFetch(options: MockOptions = {}) {
  const requests: Request[] = []
  let submitCount = 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const url = new URL(request.url)

    if (url.pathname === '/api/tasks/types') return jsonResponse(taskTypeEnvelope)
    if (url.pathname === '/api/resources/items') return jsonResponse([{ item_id: '30013', name: '固源岩', icon_url: null }])
    if (url.pathname === '/api/tasks/validate') return jsonResponse(options.validateBody ?? { items: [], total: 0, page: 1, size: 0 }, options.validateStatus ?? 200)
    if (url.pathname === '/api/pipelines' && request.method === 'POST') {
      const status = options.submitStatuses?.[submitCount++] ?? 202
      const body = status >= 400
        ? { error: { code: status === 504 ? 'CORE_COMMAND_TIMEOUT' : 'INTERNAL_ERROR', message: status === 504 ? '提交状态未知' : '提交失败' } }
        : { pipeline_id: 'pipeline-new', status: 'PENDING', reused: false }
      return jsonResponse(body, status)
    }
    if (url.pathname === '/api/pipelines/current' && request.method === 'GET') return jsonResponse({ pipeline: null })
    if (url.pathname === '/api/queue' && request.method === 'GET') return jsonResponse(initialQueue)
    if (url.pathname === '/api/queue/queued-1' && request.method === 'PATCH') return jsonResponse({ pipeline_id: 'queued-1', priority: 0, status: 'PENDING' })
    if (url.pathname === '/api/pipelines/running-1' && request.method === 'DELETE') return jsonResponse({ pipeline_id: 'running-1', status: 'cancellation_requested' }, 202)
    if (url.pathname === '/api/pipelines/queued-1' && request.method === 'DELETE') return new Response(null, { status: 204 })
    if (url.pathname === '/api/queue/pause' && request.method === 'POST') return jsonResponse({ paused: true })
    if (url.pathname === '/api/queue/resume' && request.method === 'POST') return jsonResponse({ paused: false })
    if (url.pathname === '/api/queue' && request.method === 'DELETE') return new Response(null, { status: 204 })
    if (url.pathname === '/api/pipelines' && request.method === 'GET') {
      const page = Number(url.searchParams.get('page') ?? 1)
      const envelope = options.historyPages?.[page] ?? { items: [], total: 0, page, size: 20 }
      return jsonResponse(envelope)
    }
    if (url.pathname === '/api/pipelines/history-1' && request.method === 'GET') return jsonResponse({
      id: 'history-1', title: '完成的日常任务', source: 'manual', priority: 0, status: 'COMPLETED', task_count: 1,
      tasks: [{ id: 'task-1', type_name: 'Fight', task_name: '刷理智', status: 'COMPLETED', duration_seconds: 42 }],
    })
    if (url.pathname === '/api/pipelines/history-1/logs' && request.method === 'GET') return jsonResponse({ items: [{ id: 44, source: 'task', level: 'INFO', content: '已进入关卡 1-7', ts: 1790164800, pipeline_id: 'history-1', task_id: 'task-1', logger: 'maa.task', attachment: null }], total: 1, page: 1, size: 100 })
    if (url.pathname === '/api/pipelines/history-1/screenshots' && request.method === 'GET') return jsonResponse({ items: [{ id: 'shot-1', pipeline_id: 'history-1', task_id: 'task-1', trigger: '任务完成', backend: 'adb', path: 'screenshots/record.png', format: 'jpeg', width: 1280, height: 720, size_bytes: 32000, deleted_at: null, created_at: '2026-09-23T12:05:00Z' }], total: 1 })
    if (url.pathname === '/api/screenshots/shot-1') return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } })
    return jsonResponse({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests, fetchMock }
}

function renderTasksPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><TasksPage /></QueryClientProvider>)
}

function renderTaskForm(taskType: TaskTypeSchema, onChange: (value: Record<string, unknown>) => void = () => undefined) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={queryClient}><DynamicTaskForm taskType={taskType} value={{}} onChange={onChange} /></QueryClientProvider>)
}

describe('tasks schema forms, queue and history', () => {
  beforeEach(() => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders every property from the complete nine-type schema fixture', () => {
    expect(taskTypeEnvelope.items).toHaveLength(9)
    for (const taskType of taskTypeEnvelope.items) {
      const { container } = renderTaskForm(taskType)
      const properties = (taskType.schema.properties ?? {}) as Record<string, JsonSchema>
      for (const [name, field] of Object.entries(properties)) {
        if (name === 'name') continue
        expect(container.textContent).toContain(field['x-label'])
      }
      cleanup()
    }
  })

  it('filters Fight item catalog by Chinese name and stores the selected item ID', async () => {
    installFetch()
    const onChange = vi.fn()
    renderTaskForm(types.find((type) => type.name === 'Fight')!, onChange)
    const search = await screen.findByLabelText('搜索物品名称或 ID')
    fireEvent.change(search, { target: { value: '固源' } })
    fireEvent.click(screen.getByRole('button', { name: /添加 固源岩/ }))
    expect(onChange).toHaveBeenLastCalledWith({ drops: { '30013': 1 } })
  })

  it('edits Recruit duration-map at fixed star levels and converts time to minutes', () => {
    const onChange = vi.fn()
    renderTaskForm(types.find((type) => type.name === 'Recruit')!, onChange)
    const hours = screen.getByRole('spinbutton', { name: '时长小时：3' })
    expect(hours).toHaveValue(9)
    expect(screen.getByRole('spinbutton', { name: '时长分钟：3' })).toHaveValue(0)
    expect(screen.getByRole('spinbutton', { name: '时长小时：6' })).toHaveValue(9)
    fireEvent.change(hours, { target: { value: '8' } })
    expect(onChange).toHaveBeenLastCalledWith({ recruitment_time: { '3': 480 } })
  })

  it('shows x-risk confirmation and only submits the parameter changed by the user', async () => {
    const network = installFetch()
    renderTasksPage()
    await screen.findByRole('option', { name: /刷理智/ })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'Fight' } })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.change(screen.getByLabelText('最大理智药数'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    await waitFor(() => expect(network.requests.some((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')).toBe(true))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('最大理智药数'))
    const validationRequest = network.requests.find((request) => request.url.endsWith('/api/tasks/validate'))
    expect(JSON.parse(await validationRequest!.clone().text())).toEqual({ tasks: [{ name: 'Fight', medicine: 2 }] })
    const submissionRequest = network.requests.find((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')
    expect(submissionRequest?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(await submissionRequest!.clone().text())).toEqual({ tasks: [{ name: 'Fight', medicine: 2 }] })
    expect(await screen.findByRole('status')).toHaveTextContent('流水线已加入队列')
  })

  it('maps server validate details.field onto the corresponding form field', async () => {
    const network = installFetch({
      validateStatus: 422,
      validateBody: { error: { code: 'TASK_PARAM_INVALID', message: '任务参数无效', details: { field: 'stage', message: '关卡名无效，请检查格式' } } },
    })
    renderTasksPage()
    await screen.findByRole('option', { name: /刷理智/ })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'Fight' } })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.change(screen.getByLabelText('关卡名'), { target: { value: 'unknown' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    expect(await screen.findByText('关卡名无效，请检查格式')).toHaveAttribute('role', 'alert')
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')).toBe(false)
  })

  it('maps pydantic details.fields locations onto controls', async () => {
    const network = installFetch({
      validateStatus: 422,
      validateBody: {
        error: {
          code: 'VALIDATION_ERROR',
          message: '请求参数校验失败（1 处）',
          details: {
            fields: [{
              loc: ['body', 'tasks', 0, 'FightInput', 'stage'],
              msg: 'String should have at least 1 character',
              type: 'string_too_short',
            }],
          },
        },
      },
    })
    renderTasksPage()
    await screen.findByRole('option', { name: /刷理智/ })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'Fight' } })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.change(screen.getByLabelText('关卡名'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    expect(await screen.findByText('String should have at least 1 character')).toHaveAttribute('role', 'alert')
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')).toBe(false)
  })

  it('checks status after 504 and reuses the idempotency key on explicit retry', async () => {
    const network = installFetch({ submitStatuses: [504, 202] })
    renderTasksPage()
    await screen.findByRole('option', { name: /开始唤醒/ })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('504')
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines/current'))).toBe(true)
    const first = network.requests.find((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')!
    const firstKey = first.headers.get('Idempotency-Key')
    expect(firstKey).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))
    expect(await screen.findByText('流水线已加入队列。')).toBeInTheDocument()
    const writes = network.requests.filter((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')
    expect(writes).toHaveLength(2)
    expect(writes[1]?.headers.get('Idempotency-Key')).toBe(firstKey)
  })

  it('preserves server queue order and explicitly promotes before cancelling the current pipeline', async () => {
    const network = installFetch()
    renderTasksPage()
    fireEvent.click(screen.getByRole('tab', { name: '队列' }))
    expect(await screen.findByText('定时任务')).toBeInTheDocument()
    const buttons = screen.getAllByRole('button', { name: /停止当前并执行此条/ })
    fireEvent.click(buttons[0])

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('不能保证目标紧接当前项执行'))
    await screen.findByText(/目标已调至优先级/)
    const patchIndex = network.requests.findIndex((request) => request.method === 'PATCH' && request.url.endsWith('/api/queue/queued-1'))
    const cancelIndex = network.requests.findIndex((request) => request.method === 'DELETE' && request.url.endsWith('/api/pipelines/running-1'))
    expect(patchIndex).toBeGreaterThanOrEqual(0)
    expect(cancelIndex).toBeGreaterThan(patchIndex)
    expect(JSON.parse(await network.requests[patchIndex].clone().text())).toEqual({ priority: 0 })
  })

  it('uses page, size and total to stop history pagination and shows detail status', async () => {
    const network = installFetch({ historyPages: {
      1: { items: [{ id: 'history-1', title: '完成的日常任务', source: 'manual', priority: 0, status: 'COMPLETED', task_count: 1, created_at: '2026-09-23T12:00:00Z' }], total: 21, page: 1, size: 20 },
      2: { items: [{ id: 'history-2', title: '最后一条', source: 'scheduled', priority: 2, status: 'FAILED', task_count: 2 }], total: 21, page: 2, size: 20 },
    } })
    renderTasksPage()
    fireEvent.click(screen.getByRole('tab', { name: '历史' }))
    const firstRow = await screen.findByRole('button', { name: /完成的日常任务/ })
    fireEvent.click(firstRow)
    expect(await screen.findByText('刷理智')).toBeInTheDocument()
    expect(screen.getByText('状态：已完成')).toBeInTheDocument()
    expect(await screen.findByText('已进入关卡 1-7')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: /流水线截图 1/ })).toBeInTheDocument()
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines/history-1/logs?order=asc&size=100'))).toBe(true)
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines/history-1/screenshots'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('最后一条')).toBeInTheDocument()
    await waitFor(() => expect(network.requests.some((request) => new URL(request.url).searchParams.get('page') === '2')).toBe(true))
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    const historyRequests = network.requests.filter((request) => request.url.includes('/api/pipelines?'))
    expect(historyRequests.every((request) => new URL(request.url).searchParams.get('size') === '20')).toBe(true)
  })
})
