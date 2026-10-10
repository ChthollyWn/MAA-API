import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import AccountPage from './AccountPage'
import type { AccountSummary, DepotItem, OperatorDiff, OperatorRow } from './api'

/** 账号数据页的测试夹具：按真实查询参数过滤的假后端 + 内存路由渲染。 */

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const baseRow: OperatorRow = {
  char_id: 'char_1001',
  name: '测试干员',
  rarity: 4,
  own: true,
  elite: 1,
  level: 50,
  potential: 2,
  skill_level: 4,
  masteries: { '1': 0, '2': 0, '3': 0 },
  modules: {},
  field_sources: { own: 'recognition', elite: 'recognition', level: 'recognition', potential: 'recognition' },
  recognized_at: '2026-10-02T03:00:00Z',
  updated_at: '2026-10-02T03:00:00Z',
  profession: '近卫',
}

export function operatorRow(overrides: Partial<OperatorRow> & { char_id: string; name: string }): OperatorRow {
  return { ...baseRow, ...overrides }
}

export const OPERATORS: OperatorRow[] = [
  operatorRow({
    char_id: 'char_002_amiya',
    name: '阿米娅',
    rarity: 5,
    elite: 2,
    level: 80,
    potential: 6,
    skill_level: 7,
    masteries: { '1': 3, '2': 1, '3': 0 },
    modules: { X: 2 },
    profession: '术师',
    field_sources: { own: 'recognition', elite: 'recognition', level: 'recognition', potential: 'manual', skill_level: 'manual', masteries: 'manual', modules: 'agent' },
  }),
  operatorRow({
    char_id: 'char_123_knight',
    name: '临光',
    rarity: 5,
    elite: 0,
    level: 1,
    potential: 1,
    skill_level: 1,
    profession: '重装',
  }),
  operatorRow({
    char_id: 'char_010_chen',
    name: '陈',
    rarity: 6,
    own: false,
    elite: 0,
    level: 1,
    potential: 1,
    skill_level: 1,
    profession: '近卫',
    modules: {},
  }),
]

export const DEPOT_ITEMS: DepotItem[] = [
  { item_id: '30011', name: '源岩', count: 42, category: '材料' },
  { item_id: '30012', name: '固源岩', count: 7, category: '材料' },
  { item_id: '30061', name: '聚酸酯', count: 3, category: '材料' },
  { item_id: '3003', name: '龙门币', count: 120000, category: '货币' },
]

export const OPERATOR_DIFFS: OperatorDiff[] = [
  { id: 11, char_id: 'char_002_amiya', name: '阿米娅', field: 'elite', manual_value: 1, recognized_value: 2, created_at: '2026-10-08T03:00:00Z' },
  { id: 12, char_id: 'char_002_amiya', name: '阿米娅', field: 'level', manual_value: 60, recognized_value: 80, created_at: '2026-10-08T03:00:00Z' },
]

export const SUMMARY: AccountSummary = {
  operators: { recognized: true, total: 3, elite2: 1, recognized_at: '2026-10-02T03:00:00Z', stale: false, stale_days: 2, stale_after_days: 14 },
  depot: { recognized: true, total: 4, elite2: 0, recognized_at: '2026-09-01T03:00:00Z', stale: true, stale_days: 40, stale_after_days: 14 },
  diffs: OPERATOR_DIFFS.length,
}

/**
 * 假后端按真形状应答：`/api/account/summary` 是 `{box, depot}`（`elite_two` 而不是
 * `elite2`、`pending_diffs` 而不是 `diffs`），`operator-diffs` 是 `{items, total}`。
 * 测试继续用页面内部的形状写夹具，这里做一次转写。
 */
function toRawSummary(summary: AccountSummary) {
  return {
    box: {
      total: summary.operators.total,
      elite_two: summary.operators.elite2,
      recognized_at: summary.operators.recognized ? summary.operators.recognized_at : null,
      stale: summary.operators.stale,
      stale_days: summary.operators.stale_after_days,
      pending_diffs: summary.diffs,
    },
    depot: {
      total: summary.depot.total,
      recognized_at: summary.depot.recognized ? summary.depot.recognized_at : null,
      stale: summary.depot.stale,
      stale_days: summary.depot.stale_after_days,
    },
  }
}

export interface AccountApiOptions {
  summary?: AccountSummary
  operators?: OperatorRow[]
  diffs?: OperatorDiff[]
  depot?: DepotItem[]
  categories?: string[]
  patchStatus?: number
  dismissStatus?: number
}

export interface RecordedAccountRequest {
  method: string
  path: string
  url: URL
  body: Record<string, unknown> | undefined
}

/**
 * 假后端：干员与仓库的筛选按查询参数处理，`PATCH` 会改内存里的那一行，
 * 这样「保存后刷新列表」能看到新值。
 */
export function installAccountApi(options: AccountApiOptions = {}) {
  const state = {
    summary: options.summary ?? SUMMARY,
    operators: (options.operators ?? OPERATORS).map((row) => ({ ...row })),
    diffs: [...(options.diffs ?? OPERATOR_DIFFS)],
    depot: [...(options.depot ?? DEPOT_ITEMS)],
    categories: options.categories ?? ['材料', '货币'],
  }
  const requests: RecordedAccountRequest[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    // 请求由 openapi-fetch 包成 Request 后调用 fetch，body 只能从 Request 上读。
    const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text()
    requests.push({
      method: request.method,
      path: url.pathname,
      url,
      body: text ? (JSON.parse(text) as Record<string, unknown>) : undefined,
    })

    if (url.pathname === '/api/account/summary') return jsonResponse(toRawSummary(state.summary))

    if (url.pathname === '/api/account/operators' && request.method === 'GET') {
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      let rows = state.operators
      if (url.searchParams.get('owned_only') === 'true') rows = rows.filter((row) => row.own)
      const rarityMin = url.searchParams.get('rarity_min')
      if (rarityMin) rows = rows.filter((row) => row.rarity >= Number(rarityMin))
      const eliteMin = url.searchParams.get('elite_min')
      if (eliteMin !== null) rows = rows.filter((row) => row.elite >= Number(eliteMin))
      const profession = url.searchParams.get('profession')
      if (profession) rows = rows.filter((row) => row.profession === profession)
      const query = url.searchParams.get('query')
      if (query) rows = rows.filter((row) => row.name.includes(query))
      // 容器键与真后端一致：`{items, total, limit, offset}`（路由的 `response_model`）。
      return jsonResponse({ items: rows.slice(offset, offset + limit), total: rows.length, limit, offset })
    }

    const patch = url.pathname.match(/^\/api\/account\/operators\/([^/]+)$/)
    if (patch && request.method === 'PATCH') {
      if (options.patchStatus && options.patchStatus >= 400) {
        return jsonResponse({ error: { code: 'VALIDATION_ERROR', message: '编辑约束不满足' } }, options.patchStatus)
      }
      const body = text ? (JSON.parse(text) as Partial<OperatorRow>) : {}
      const current = state.operators.find((row) => row.char_id === patch[1])
      if (!current) return jsonResponse({ error: { code: 'NOT_FOUND', message: '没有这名干员' } }, 404)
      const updated: OperatorRow = { ...current, ...body }
      state.operators = state.operators.map((row) => (row.char_id === patch[1] ? updated : row))
      return jsonResponse(updated)
    }

    if (url.pathname === '/api/account/operator-diffs' && request.method === 'GET') {
      // 后端返回 `{items, total}`（`web/src/features/account/api.ts` 的转写层）。
      return jsonResponse({ items: state.diffs, total: state.diffs.length })
    }
    if (url.pathname === '/api/account/operator-diffs' && request.method === 'DELETE') {
      if (options.dismissStatus && options.dismissStatus >= 400) {
        return jsonResponse({ error: { code: 'INTERNAL_ERROR', message: '标记失败' } }, options.dismissStatus)
      }
      const body = text ? (JSON.parse(text) as { ids?: number[] }) : {}
      const ids = body.ids ?? []
      const dismissed = ids.length ? state.diffs.filter((diff) => ids.includes(diff.id)).length : state.diffs.length
      state.diffs = ids.length ? state.diffs.filter((diff) => !ids.includes(diff.id)) : []
      state.summary = { ...state.summary, diffs: state.diffs.length }
      return jsonResponse({ dismissed })
    }

    if (url.pathname === '/api/account/depot') {
      const limit = Number(url.searchParams.get('limit') ?? 100)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      let items = state.depot
      const category = url.searchParams.get('category')
      if (category) items = items.filter((item) => item.category === category)
      const query = url.searchParams.get('query')
      if (query) items = items.filter((item) => item.name.includes(query) || item.item_id.includes(query))
      return jsonResponse({ items: items.slice(offset, offset + limit), total: items.length, categories: state.categories })
    }

    return jsonResponse({ error: { code: 'NOT_FOUND', message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const find = (method: string, path: string) =>
    requests.filter((request) => request.method === method && request.path === path)
  return { requests, fetchMock, state, find }
}

export function renderAccount(path = '/more/account') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/more/account', element: <AccountPage /> },
      { path: '/more/account/depot', element: <AccountPage /> },
    ],
    { initialEntries: [path] },
  )
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...view, router, queryClient }
}
