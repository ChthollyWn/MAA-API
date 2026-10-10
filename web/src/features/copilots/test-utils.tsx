import { vi } from 'vitest'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import CopilotsPage from './CopilotsPage'

/**
 * 作业页的测试夹具：按真实查询参数过滤的假后端 + 内存路由渲染。
 *
 * 夹具写成**后端 wire 的形状**（`meta` 用 snake_case、`attempts` 按附录 E 的
 * `copilot_attempt` 字段名），页面那一层要自己转写；这样转写层的兜底逻辑也被覆盖到。
 */

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** 写入侧可能用 `result` 或 `outcome` 承载结果（清理逻辑两个都认），两个都夹具一份。 */
export const ATTEMPT_FAILED = {
  id: 'att-1',
  result: 'failed',
  judged_by: 'core',
  counted: true,
  started_at: '2026-10-10T04:00:00Z',
  finished_at: '2026-10-10T04:03:00Z',
}

export const ATTEMPT_UNKNOWN = {
  id: 'att-2',
  outcome: 'unknown',
  judged_by: 'model',
  counted: true,
  finished_at: '2026-10-10T04:20:00Z',
  evidence: '结算界面星级看不清',
}

/** 后端 `_asset_wire` 的形状：列表不带 `content`。 */
export const AI_DRAFT_V2 = {
  id: 'cop-ai-2',
  kind: 'copilot',
  name: '1-7 三人速刷 v2',
  description: '把银灰换成棘刺',
  meta: {
    source: 'ai',
    stage_id: '1-7',
    title: '1-7 三人速刷 v2',
    parent_id: 'cop-ai-1',
    session_id: 'sess-1',
    note: '把银灰换成棘刺',
    attempts: [ATTEMPT_FAILED, ATTEMPT_UNKNOWN],
    retain: false,
  },
  enabled: true,
  checksum: 'a'.repeat(64),
  created_at: '2026-10-10T05:00:00Z',
  updated_at: '2026-10-10T05:00:00Z',
}

export const AI_DRAFT_V1 = {
  ...AI_DRAFT_V2,
  id: 'cop-ai-1',
  name: '1-7 三人速刷',
  description: null,
  meta: {
    source: 'ai',
    stage_id: '1-7',
    title: '1-7 三人速刷',
    session_id: 'sess-1',
    attempts: [],
    retain: false,
  },
  created_at: '2026-10-10T04:30:00Z',
}

export const SITE_COPY = {
  ...AI_DRAFT_V2,
  id: 'cop-site-1',
  name: '1-7 作业站 三人',
  description: null,
  meta: {
    source: 'site',
    stage_id: '1-7',
    title: '1-7 作业站 三人',
    site_id: 12345,
    site_link: 'https://prts.maa.plus/copilot/12345',
    uploader: '某博士',
    attempts: [{ id: 'att-3', outcome: 'three_star', judged_by: 'core', counted: true, finished_at: '2026-10-09T12:00:00Z' }],
    retain: true,
  },
}

/** M29 之前用 `upload_copilot` 存的老资源：`meta` 里只有 `stage_name` 与 `title`。 */
export const LEGACY_UPLOAD = {
  ...AI_DRAFT_V2,
  id: 'cop-user-1',
  name: 'CE-5 钱本 4 人',
  description: null,
  meta: { stage_name: 'CE-5', title: '钱本 4 人', attempts: [] },
  created_at: '2026-09-01T02:00:00Z',
}

export const COPILOT_ITEMS: unknown[] = [AI_DRAFT_V2, AI_DRAFT_V1, SITE_COPY, LEGACY_UPLOAD]

export const COPILOT_CONTENTS: Record<string, unknown> = {
  'cop-ai-2': { stage_name: '1-7', minimum_required: 'v4.0', doc: { title: '1-7 三人速刷 v2' }, actions: [] },
  'cop-ai-1': { stage_name: '1-7', minimum_required: 'v4.0', doc: { title: '1-7 三人速刷' }, actions: [] },
  'cop-site-1': { stage_name: '1-7', minimum_required: 'v4.0', doc: { title: '作业站 1-7' }, actions: [] },
  'cop-user-1': { stage_name: 'CE-5', minimum_required: 'v4.0', doc: { title: '钱本 4 人' }, actions: [] },
}

export interface CopilotsApiOptions {
  items?: unknown[]
  contents?: Record<string, unknown>
  listStatus?: number
  detailStatus?: number
  deleteStatus?: number
  retainStatus?: number
  revokeStatus?: number
}

export interface RecordedCopilotRequest {
  method: string
  path: string
  url: URL
  body: Record<string, unknown> | undefined
}

export function installCopilotsApi(options: CopilotsApiOptions = {}) {
  const state = {
    items: (options.items ?? COPILOT_ITEMS).map((item) => JSON.parse(JSON.stringify(item)) as unknown),
    contents: { ...(options.contents ?? COPILOT_CONTENTS) },
    revokedSessions: [] as string[],
  }
  const requests: RecordedCopilotRequest[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    requests.push({
      method: request.method,
      path: url.pathname,
      url,
      body: text ? (JSON.parse(text) as Record<string, unknown>) : undefined,
    })

    if (url.pathname === '/api/resources/copilots' && request.method === 'GET') {
      if (options.listStatus && options.listStatus >= 400) {
        return jsonResponse({ error: { code: 'SERVICE_UNAVAILABLE', message: '资源服务不可用' } }, options.listStatus)
      }
      const size = Number(url.searchParams.get('size') ?? 50)
      const page = Number(url.searchParams.get('page') ?? 1)
      const q = url.searchParams.get('q')
      let items = state.items
      if (q) {
        items = items.filter((item) => JSON.stringify(item).includes(q))
      }
      return jsonResponse({
        items: items.slice((page - 1) * size, page * size),
        total: items.length,
        page,
        size,
      })
    }

    const detail = url.pathname.match(/^\/api\/resources\/copilots\/([^/]+)$/)
    if (detail && request.method === 'GET') {
      if (options.detailStatus && options.detailStatus >= 400) {
        return jsonResponse({ error: { code: 'RESOURCE_ASSET_NOT_FOUND', message: '作业不存在' } }, options.detailStatus)
      }
      const item = state.items.find((entry) => (entry as { id?: string }).id === detail[1])
      if (!item) return jsonResponse({ error: { code: 'RESOURCE_ASSET_NOT_FOUND', message: '作业不存在' } }, 404)
      return jsonResponse({ ...(item as Record<string, unknown>), content: state.contents[detail[1]] ?? null })
    }
    if (detail && request.method === 'DELETE') {
      if (options.deleteStatus && options.deleteStatus >= 400) {
        return jsonResponse({ error: { code: 'RESOURCE_ASSET_NOT_FOUND', message: '作业不存在' } }, options.deleteStatus)
      }
      state.items = state.items.filter((entry) => (entry as { id?: string }).id !== detail[1])
      return jsonResponse({ id: detail[1], removed: true })
    }

    const retain = url.pathname.match(/^\/api\/resources\/copilots\/([^/]+)\/retain$/)
    if (retain && request.method === 'POST') {
      if (options.retainStatus && options.retainStatus >= 400) {
        return jsonResponse({ error: { code: 'TASK_PARAM_INVALID', message: '保留失败' } }, options.retainStatus)
      }
      const body = text ? (JSON.parse(text) as { retain?: boolean }) : {}
      const item = state.items.find((entry) => (entry as { id?: string }).id === retain[1]) as
        | { meta?: Record<string, unknown> }
        | undefined
      if (!item) return jsonResponse({ error: { code: 'RESOURCE_ASSET_NOT_FOUND', message: '作业不存在' } }, 404)
      item.meta = { ...(item.meta ?? {}), retain: body.retain === true }
      return jsonResponse(item)
    }

    const revoke = url.pathname.match(/^\/api\/agent\/sessions\/([^/]+)\/copilot-grant$/)
    if (revoke && request.method === 'DELETE') {
      if (options.revokeStatus && options.revokeStatus >= 400) {
        return jsonResponse({ error: { code: 'AGENT_SESSION_NOT_FOUND', message: '会话不存在' } }, options.revokeStatus)
      }
      state.revokedSessions.push(revoke[1])
      return new Response(null, { status: 204 })
    }

    return jsonResponse({ error: { code: 'NOT_FOUND', message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const find = (method: string, path: string) =>
    requests.filter((request) => request.method === method && request.path === path)
  return { requests, fetchMock, state, find }
}

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
}

/** 通用渲染：给授权卡片 / 授权条这类不依赖路由的组件用。 */
export function renderWithQuery(ui: ReactNode) {
  const queryClient = testQueryClient()
  const view = render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
  return { ...view, queryClient }
}

export function renderCopilots(path = '/more/copilots') {
  const queryClient = testQueryClient()
  const router = createMemoryRouter([{ path: '/more/copilots', element: <CopilotsPage /> }], {
    initialEntries: [path],
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...view, router, queryClient }
}
