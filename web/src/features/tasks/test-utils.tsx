import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import TasksPage from '@/routes/tasks'
import PipelineDetailPage from '@/routes/tasks/detail'
import type { TaskTypeSchema } from './form'
import taskTypesFixture from './task-types.fixture.json'

export const taskTypeEnvelope = taskTypesFixture as unknown as {
  items: TaskTypeSchema[]
  total: number
  page: number
  size: number
}

export type Row = Record<string, unknown>
export type PageEnvelope = { items: Row[]; total: number; page: number; size: number }

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export const runningItem = { pipeline_id: 'running-1', name: '日常流水线', source: 'manual', priority: 0, status: 'running', created_at: '2026-09-23T11:58:00Z' }
export const pendingItems = [
  { id: 'queued-1', title: '夜间定时', source: 'scheduled', priority: 2, status: 'PENDING', task_count: 1 },
  { id: 'queued-2', title: 'Agent 任务', source: 'agent', priority: 1, status: 'PENDING', task_count: 2 },
]

export const completedDetail = {
  id: 'history-1', title: '完成的日常任务', source: 'manual', priority: 0, status: 'COMPLETED', task_count: 1,
  created_at: '2026-09-23T11:59:00Z', started_at: '2026-09-23T12:00:00Z', finished_at: '2026-09-23T12:00:42Z',
  tasks: [{ id: 'task-1', type_name: 'Fight', task_name: '刷理智', status: 'COMPLETED', duration_seconds: 42 }],
}

export interface TaskApiOptions {
  validateStatus?: number
  validateBody?: unknown
  submitStatuses?: number[]
  queue?: { running?: Row | null; pending?: Row[]; paused?: boolean }
  current?: unknown
  historyPages?: Record<number, PageEnvelope>
  details?: Record<string, Row>
  retryStatus?: number
  schedules?: Row[]
}

export function installTaskApi(options: TaskApiOptions = {}) {
  const requests: Request[] = []
  const state = {
    paused: options.queue?.paused ?? false,
    running: options.queue?.running === undefined ? runningItem as Row : options.queue.running,
    pending: [...(options.queue?.pending ?? pendingItems)] as Row[],
  }
  const details: Record<string, Row> = { 'history-1': completedDetail, ...options.details }
  let submitCount = 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const url = new URL(request.url)
    const { pathname } = url
    const method = request.method

    if (pathname === '/api/tasks/types') return jsonResponse(taskTypeEnvelope)
    if (pathname === '/api/schedules' && method === 'GET') return jsonResponse({ items: options.schedules ?? [], total: options.schedules?.length ?? 0 })
    if (pathname === '/api/resources/items') return jsonResponse([{ item_id: '30013', name: '固源岩', icon_url: null }])
    if (pathname === '/api/resources/copilots') {
      return jsonResponse({ items: [{ id: 'copilot-1', name: '主线 1-7', meta: { stage_name: '1-7', title: '剿灭行动' } }], total: 1, page: 1, size: 200 })
    }
    if (pathname === '/api/tasks/validate') {
      return jsonResponse(options.validateBody ?? { items: [], total: 0, page: 1, size: 0 }, options.validateStatus ?? 200)
    }
    if (pathname === '/api/pipelines' && method === 'POST') {
      const status = options.submitStatuses?.[submitCount++] ?? 202
      const body = status >= 400
        ? { error: { code: status === 504 ? 'CORE_COMMAND_TIMEOUT' : 'INTERNAL_ERROR', message: status === 504 ? '提交状态未知' : '提交失败' } }
        : { pipeline_id: 'pipeline-new', status: 'pending', reused: false }
      return jsonResponse(body, status)
    }
    if (pathname === '/api/pipelines/current' && method === 'GET') return jsonResponse(options.current ?? { pipeline: null })
    if (pathname === '/api/queue' && method === 'GET') {
      return jsonResponse({
        running: state.running,
        pending: state.pending,
        paused: state.paused,
        counts: { pending: state.pending.length, running: state.running ? 1 : 0 },
      })
    }
    if (pathname === '/api/queue' && method === 'DELETE') {
      state.pending = []
      return new Response(null, { status: 204 })
    }
    if (pathname === '/api/queue/pause' && method === 'POST') {
      state.paused = true
      return jsonResponse({ paused: true })
    }
    if (pathname === '/api/queue/resume' && method === 'POST') {
      state.paused = false
      return jsonResponse({ paused: false })
    }
    const queueItem = pathname.match(/^\/api\/queue\/([^/]+)$/)
    if (queueItem && method === 'PATCH') return jsonResponse({ pipeline_id: queueItem[1], priority: 0, status: 'pending' })
    if (pathname === '/api/pipelines' && method === 'GET') {
      const page = Number(url.searchParams.get('page') ?? 1)
      return jsonResponse(options.historyPages?.[page] ?? { items: [], total: 0, page, size: 20 })
    }
    const retry = pathname.match(/^\/api\/pipelines\/([^/]+)\/retry$/)
    if (retry && method === 'POST') {
      if ((options.retryStatus ?? 202) >= 400) return jsonResponse({ error: { code: 'QUEUE_PAUSED', message: '队列已暂停' } }, options.retryStatus)
      return jsonResponse({ pipeline_id: 'pipeline-retry', status: 'pending', retry_of_id: retry[1], reused: false }, 202)
    }
    const logs = pathname.match(/^\/api\/pipelines\/([^/]+)\/logs$/)
    if (logs && method === 'GET') {
      if (logs[1] !== 'history-1') return jsonResponse({ items: [], total: 0, page: 1, size: 100 })
      return jsonResponse({ items: [{ id: 44, source: 'task', level: 'INFO', content: '已进入关卡 1-7', ts: 1790164800, pipeline_id: 'history-1', task_id: 'task-1', logger: 'maa.task', attachment: null }], total: 1, page: 1, size: 100 })
    }
    const screenshots = pathname.match(/^\/api\/pipelines\/([^/]+)\/screenshots$/)
    if (screenshots && method === 'GET') {
      if (screenshots[1] !== 'history-1') return jsonResponse({ items: [], total: 0 })
      return jsonResponse({ items: [{ id: 'shot-1', pipeline_id: 'history-1', task_id: 'task-1', trigger: 'task_snapshot', backend: 'adb', path: 'screenshots/record.png', format: 'jpeg', width: 1280, height: 720, size_bytes: 32000, deleted_at: null, created_at: '2026-09-23T12:05:00Z' }], total: 1 })
    }
    if (pathname === '/api/screenshots/shot-1') return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } })
    const pipeline = pathname.match(/^\/api\/pipelines\/([^/]+)$/)
    if (pipeline && method === 'DELETE') return jsonResponse({ pipeline_id: pipeline[1], status: 'cancellation_requested' }, 202)
    if (pipeline && method === 'GET' && details[pipeline[1]]) return jsonResponse(details[pipeline[1]])
    return jsonResponse({ error: { code: 'NOT_FOUND', message: `Unexpected request ${method} ${pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const find = (method: string, path: string) => requests.filter((request) => request.method === method && new URL(request.url).pathname === path)
  return { requests, fetchMock, state, find }
}

export function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

export function renderTaskArea(path = '/tasks') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const router = createMemoryRouter([
    { path: '/tasks', element: <TasksPage /> },
    { path: '/tasks/queue', element: <TasksPage /> },
    { path: '/tasks/history', element: <TasksPage /> },
    { path: '/tasks/history/:pipelineId', element: <PipelineDetailPage /> },
    { path: '/tasks/schedules', element: <LocationProbe /> },
    { path: '/tasks/schedules/new', element: <LocationProbe /> },
    { path: '/logs/*', element: <LocationProbe /> },
  ], { initialEntries: [path] })
  const view = render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>)
  return { ...view, router, queryClient }
}
