import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import AccountPage from './AccountPage'
import type {
  AccountSummary,
  DepotItem,
  OperatorRow,
  SklandQrPoll,
  SklandQrSession,
  SklandRole,
  SklandStatus,
  SklandSyncSummary,
} from './api'

/** 账号数据页的测试夹具：按真实查询参数过滤的假后端 + 内存路由渲染。 */

export function jsonResponse(body: unknown, status = 200, headers?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
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
  // M43：卡片要用的两列。夹具默认给「不知道」（`null`），需要图标/标签的用例自己覆盖。
  favor_percent: null,
  skill_ids: null,
  module_details: null,
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
    skill_ids: ['skchr_amiya_2', 'skchr_amiya_3', 'skcom_magic_rage[3]'],
    module_details: [
      {
        type: 'X',
        level: 2,
        equip_id: 'uniequip_002_amiya',
        branch: 'ccr',
        label: 'CCR-X',
        type_icon: 'ccr-x',
      },
    ],
    favor_percent: 200,
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

/**
 * M35 未识别练度夹具。
 *
 * `UNKNOWN_SKILL_ROW`：同步给到了精英化 / 等级 / 潜能，但技能等级、专精、模组三列是
 * `null` ＝ **未识别**——这正是 M35 之前被渲染成「技能 1 级 / 无专精 / 无模组」的现场。
 * `UNKNOWN_ALL_ROW`：行里只有身份、没有练度详情，六列全未识别。
 */
export const UNKNOWN_SKILL_ROW: OperatorRow = operatorRow({
  char_id: 'char_4093_frston',
  name: 'Friston-3',
  rarity: 6,
  elite: 2,
  level: 90,
  potential: 1,
  skill_level: null,
  masteries: null,
  modules: null,
  profession: '术师',
  field_sources: { own: 'recognition', elite: 'recognition', level: 'recognition', potential: 'recognition' },
})

export const UNKNOWN_ALL_ROW: OperatorRow = operatorRow({
  char_id: 'char_9999_unknown',
  name: '未识别练度',
  rarity: 3,
  elite: null,
  level: null,
  potential: null,
  skill_level: null,
  masteries: null,
  modules: null,
  profession: '近卫',
  field_sources: { own: 'recognition' },
})

/** 已知但「空」的练度：全 0 专精槽与空模组是「无专精 / 无模组」，不是未识别。 */
export const KNOWN_EMPTY_ROW: OperatorRow = operatorRow({
  char_id: 'char_102_texas',
  name: '德克萨斯',
  rarity: 5,
  elite: 0,
  level: 1,
  potential: 1,
  skill_level: 1,
  masteries: { '1': 0, '2': 0, '3': 0 },
  modules: {},
  profession: '先锋',
  field_sources: { own: 'recognition', elite: 'recognition', level: 'recognition', potential: 'recognition', skill_level: 'manual', masteries: 'manual', modules: 'manual' },
})

/**
 * 仓库夹具（M27 + M44 §4.4）。
 *
 * `4001` 是**龙门币**（顶部资源栏那三件之一）：识别值只到千位，页面要标「约」——所以
 * 这里的数量故意写成 `4797000` 这种整千的数，与真机上的现场一致。
 * `99999` 是 `item_index.json` 里**没有**的 id（识别到了但索引里查不到），拿它验占位图。
 *
 * 每一行都**有数量**：后端（用户 2026-10-13 裁决）只返回有明确数量的物品，M45 自识别
 * 写下的 `count = NULL` 行读侧不出现（自识别 2026-10-14 已连实现一起删除），页面也不再
 * 有来源标签与覆盖说明（见 `docs/13` §8.68）。
 */
export const DEPOT_ITEMS: DepotItem[] = [
  { item_id: '30011', name: '源岩', count: 42, category: '材料' },
  { item_id: '30012', name: '固源岩', count: 7, category: '材料' },
  { item_id: '30061', name: '聚酸酯', count: 3, category: '材料' },
  { item_id: '4001', name: '龙门币', count: 4797000, category: '货币' },
  { item_id: '99999', name: '99999', count: 3, category: null },
]

/**
 * 数量缺失的一行（防御性用例专用）。
 *
 * 生产的仓库列表不会有它；留在夹具里是为了钉住「真拿到 `null` 时显示『—』而不是 0」
 * 这条前端行为（后端那一列仍可空，迁移 `0021` 不回退）。
 */
export const DEPOT_ITEM_WITHOUT_COUNT: DepotItem = {
  item_id: '7003',
  name: '寻访凭证',
  count: null,
  category: '凭证',
}

export const SUMMARY: AccountSummary = {
  operators: { recognized: true, total: 3, elite2: 1, recognized_at: '2026-10-02T03:00:00Z', stale: false, stale_days: 2, stale_after_days: 14 },
  depot: { recognized: true, total: 4, elite2: 0, recognized_at: '2026-09-01T03:00:00Z', stale: true, stale_days: 40, stale_after_days: 14 },
}

/**
 * 森空岛（M42 §4.6）的夹具。
 *
 * `SKLAND_UNBOUND`：默认状态——没绑定、密钥配好了，卡片走扫码那一条路。
 * `SKLAND_KEY_MISSING`：`config.yaml` 里没配 `skland.credential_key`，此时连二维码都不该有。
 * `SKLAND_BOUND`：已绑定，带一个默认角色与上次同步摘要，用来验已绑定视图。
 */
export const SKLAND_ROLE: SklandRole = {
  uid: 'uid-1001',
  app_code: 'arknights',
  channel_master_id: null,
  nickname: '罗德岛博士',
  level: 114,
  is_default: true,
  synced_at: '2026-10-12T02:30:00Z',
}

export const SKLAND_ROLE_ALT: SklandRole = {
  uid: 'uid-1002',
  app_code: 'arknights',
  channel_master_id: '1',
  nickname: '小号',
  level: 42,
  is_default: false,
  synced_at: null,
}

export const SKLAND_SYNC_SUMMARY: SklandSyncSummary = {
  status: 'succeeded',
  role: SKLAND_ROLE,
  synced_at: '2026-10-12T02:30:00Z',
  total: 383,
  created: 12,
  updated: 47,
  unchanged: 324,
  ownership_cleared: 3,
  dropped_invalid: 0,
  duration_ms: 820,
}

export const SKLAND_UNBOUND: SklandStatus = {
  bound: false,
  key_configured: true,
  account: null,
  roles: [],
  last_sync_at: null,
  last_sync: null,
  last_error: null,
  min_sync_interval_seconds: 60,
  min_chars: 200,
  auto_sync_enabled: false,
  auto_sync_interval_minutes: 0,
}

export const SKLAND_KEY_MISSING: SklandStatus = { ...SKLAND_UNBOUND, key_configured: false }

export const SKLAND_BOUND: SklandStatus = {
  ...SKLAND_UNBOUND,
  bound: true,
  account: SKLAND_ROLE,
  roles: [SKLAND_ROLE, SKLAND_ROLE_ALT],
  last_sync_at: SKLAND_SYNC_SUMMARY.synced_at,
  last_sync: SKLAND_SYNC_SUMMARY,
}

/**
 * 二维码会话：`expires_at` 按夹具生成时刻往后推 3 分钟，倒计时用例不会一渲染就过期
 * （假计时器用 `shouldAdvanceTime: true`，走的是真实时间）。
 */
export const SKLAND_QR_SESSION: SklandQrSession = {
  session_id: 'qr-1',
  qr_svg: '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#000"/></svg>',
  payload: 'https://www.skland.com/hypergryph/login?qrcode=abc',
  expires_at: new Date(Date.now() + 180_000).toISOString(),
  poll_interval_ms: 2_000,
}

export const SKLAND_QR_WAITING: SklandQrPoll = { state: 'waiting', message: null, account: null, roles: [] }
export const SKLAND_QR_SUCCEEDED: SklandQrPoll = { state: 'succeeded', message: null, account: SKLAND_ROLE, roles: [SKLAND_ROLE] }
export const SKLAND_QR_EXPIRED: SklandQrPoll = { state: 'expired', message: null, account: null, roles: [] }
export const SKLAND_QR_FAILED: SklandQrPoll = { state: 'failed', message: '森空岛拒绝了这次登录', account: null, roles: [] }

/**
 * 假后端按真形状应答：`/api/account/summary` 是 `{box, depot}`（`elite_two` 而不是
 * `elite2`）。测试继续用页面内部的形状写夹具，这里做一次转写。
 */
function toRawSummary(summary: AccountSummary) {
  return {
    box: {
      total: summary.operators.total,
      elite_two: summary.operators.elite2,
      recognized_at: summary.operators.recognized ? summary.operators.recognized_at : null,
      stale: summary.operators.stale,
      stale_days: summary.operators.stale_after_days,
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
  depot?: DepotItem[]
  categories?: string[]
  /** `GET /api/skland/status` 的响应；默认未绑定（`SKLAND_UNBOUND`）。 */
  skland?: Partial<SklandStatus>
  /** `POST /api/skland/login/qrcode` 的 200 响应；默认 `SKLAND_QR_SESSION`。 */
  sklandQr?: Partial<SklandQrSession>
  /** 非 0 且 ≥400 时，生成二维码回这个状态码（409 ＝ 没配 `skland.credential_key`）。 */
  sklandQrStatus?: number
  /** 轮询 `GET /api/skland/login/qrcode/{session_id}` 的响应；默认 `waiting`。 */
  sklandQrPoll?: Partial<SklandQrPoll>
  /**
   * 扫码成功（或手动绑定成功）之后，`GET /status` 再取的响应。演这一段时记得带上
   * `bound: true` 与 `account`，否则状态还是「未绑定」。
   */
  sklandAfterBind?: Partial<SklandStatus>
  /** 非 0 且 ≥400 时，`POST /api/skland/login/manual` 回这个状态码（401 ＝ 凭据不对）。 */
  sklandManualStatus?: number
  /** 非 0 且 ≥400 时，`POST /api/skland/sync` 回这个状态码（429 ＝ 冷却中、409 ＝ 忙）。 */
  sklandSyncStatus?: number
  /** `sklandSyncStatus` 为 429 时回给前端的 `Retry-After` 秒数。 */
  sklandSyncRetryAfter?: number
  /** `POST /api/skland/sync` 的 200 响应；默认 `SKLAND_SYNC_SUMMARY`。 */
  sklandSyncSummary?: Partial<SklandSyncSummary>
  /**
   * 把 `POST /api/skland/sync` 的响应挂住不落地，直到用例调用返回的 `releaseSklandSync()`：
   * 「同步按钮走 loading」这条断言需要一个**看得见**的进行中状态。
   */
  holdSklandSync?: boolean
  /** 非 0 且 ≥400 时，`DELETE /api/skland/binding` 回这个状态码。 */
  sklandUnbindStatus?: number
  /**
   * 取图接口 `/api/account/assets/**` 里回 404 的路径（后缀匹配），用来演「这张图取不到」
   * 的占位。默认所有取图都回一张真 PNG。
   */
  assetFailures?: string[]
}

export interface RecordedAccountRequest {
  method: string
  path: string
  url: URL
  body: Record<string, unknown> | undefined
}

/** 一张最小 PNG（1×1，真实可解码）——取图接口的夹具用它，别拿 JSON 冒充图片。 */
export const TINY_PNG = Uint8Array.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,
  0x89, 0x00, 0x00, 0x00, 0x0a, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x00, 0x01, 0x00, 0x00,
  0x05, 0x00, 0x01, 0x0d, 0x0a, 0x2d, 0xb4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae,
  0x42, 0x60, 0x82,
])

function pngResponse(body: Uint8Array = TINY_PNG): Response {
  return new Response(new Blob([new Uint8Array(body)], { type: 'image/png' }), {
    status: 200,
    headers: { 'Content-Type': 'image/png' },
  })
}

/**
 * 假后端：干员与仓库的筛选按查询参数处理（M47 起干员表只读，没有写路径）。
 */
export function installAccountApi(options: AccountApiOptions = {}) {
  const state = {
    summary: options.summary ?? SUMMARY,
    operators: (options.operators ?? OPERATORS).map((row) => ({ ...row })),
    depot: [...(options.depot ?? DEPOT_ITEMS)],
    categories: options.categories ?? ['材料', '货币'],
  }
  // 取图（M43/M44）：`/api/account/assets/**` 的响应是 PNG、不是 JSON，而且**不计入
  // `requests`**——既有用例拿 `find('GET', path)` 数的是账号数据端点，图片请求混进去会
  // 把「请求了几次列表」这类断言搅乱。`assetFailures` 里的路径回 404（占位图用例）。
  const assetRequests: string[] = []
  const assetFailures = options.assetFailures ?? []
  // 森空岛状态（M42 §4.6）：扫码 / 手动绑定成功后改口说「已绑定」并带上 `sklandAfterBind`，
  // 解绑之后再改回「未绑定」；同步成功会把摘要写进后续的 `GET /status`。
  const sklandBase: SklandStatus = { ...SKLAND_UNBOUND, ...options.skland }
  let sklandBoundByLogin = false
  let sklandUnbound = false
  let sklandLastSync: SklandSyncSummary | null = sklandBase.last_sync
  let releaseSklandSync: (() => void) | null = null
  const heldSync = options.holdSklandSync
    ? new Promise<void>((resolve) => { releaseSklandSync = resolve })
    : null
  const sklandState = (): SklandStatus => {
    const base = sklandUnbound
      ? { ...SKLAND_UNBOUND, key_configured: sklandBase.key_configured }
      : sklandBoundByLogin ? { ...sklandBase, ...options.sklandAfterBind } : { ...sklandBase }
    return {
      ...base,
      last_sync: sklandLastSync,
      last_sync_at: sklandLastSync?.synced_at ?? base.last_sync_at,
    }
  }
  const requests: RecordedAccountRequest[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)

    // 取图先拦：它不该进 `requests`，响应体也不是 JSON（见上面 assetRequests 的注释）。
    if (url.pathname.startsWith('/api/account/assets/')) {
      assetRequests.push(url.pathname)
      if (assetFailures.some((path) => url.pathname.endsWith(path))) {
        return new Response('nope', { status: 404 })
      }
      return pngResponse()
    }

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
      // 未识别的精英化不命中「最低精英化」（M35 规格 §4.4）：SQL 的三值逻辑就是这个结果。
      if (eliteMin !== null) rows = rows.filter((row) => row.elite !== null && row.elite >= Number(eliteMin))
      const profession = url.searchParams.get('profession')
      if (profession) rows = rows.filter((row) => row.profession === profession)
      const query = url.searchParams.get('query')
      if (query) rows = rows.filter((row) => row.name.includes(query))
      // 容器键与真后端一致：`{items, total, limit, offset}`（路由的 `response_model`）。
      return jsonResponse({ items: rows.slice(offset, offset + limit), total: rows.length, limit, offset })
    }

    if (url.pathname === '/api/account/depot') {
      const limit = Number(url.searchParams.get('limit') ?? 100)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      let items = state.depot
      const category = url.searchParams.get('category')
      if (category) items = items.filter((item) => item.category === category)
      const query = url.searchParams.get('query')
      if (query) items = items.filter((item) => item.name.includes(query) || item.item_id.includes(query))
      return jsonResponse({
        items: items.slice(offset, offset + limit),
        total: items.length,
        categories: state.categories,
      })
    }

    // 森空岛（M42 §4.6）：状态、二维码、轮询、手动绑定、同步、解绑六个端点。
    if (url.pathname === '/api/skland/status' && request.method === 'GET') return jsonResponse(sklandState())

    if (url.pathname === '/api/skland/login/qrcode' && request.method === 'POST') {
      if (options.sklandQrStatus && options.sklandQrStatus >= 400) {
        return jsonResponse(
          {
            error: {
              code: 'SKLAND_ENCRYPTION_KEY_INVALID',
              message: '森空岛凭据加密密钥缺失或不是合法 Fernet key：请在 config.yaml 配 skland.credential_key',
            },
          },
          options.sklandQrStatus,
        )
      }
      return jsonResponse({ ...SKLAND_QR_SESSION, ...options.sklandQr })
    }

    const qrPoll = url.pathname.match(/^\/api\/skland\/login\/qrcode\/([^/]+)$/)
    if (qrPoll && request.method === 'GET') {
      const poll: SklandQrPoll = { ...SKLAND_QR_WAITING, ...options.sklandQrPoll }
      // 扫到了：后续 `GET /status` 按 `sklandAfterBind` 改口说「已绑定」。
      if (poll.state === 'succeeded') sklandBoundByLogin = true
      return jsonResponse(poll)
    }

    if (url.pathname === '/api/skland/login/manual' && request.method === 'POST') {
      if (options.sklandManualStatus && options.sklandManualStatus >= 400) {
        return jsonResponse(
          { error: { code: 'SKLAND_LOGIN_FAILED', message: '凭据校验失败，请重新获取 cred 与 token' } },
          options.sklandManualStatus,
        )
      }
      sklandBoundByLogin = true
      return jsonResponse(sklandState())
    }

    if (url.pathname === '/api/skland/sync' && request.method === 'POST') {
      if (options.sklandSyncStatus && options.sklandSyncStatus >= 400) {
        const rateLimited = options.sklandSyncStatus === 429
        return jsonResponse(
          {
            error: {
              code: rateLimited ? 'RATE_LIMITED' : 'SKLAND_SYNC_BUSY',
              message: rateLimited ? '同步太频繁，请稍后再试' : '已有同步在跑，先等它结束',
            },
          },
          options.sklandSyncStatus,
          options.sklandSyncRetryAfter ? { 'Retry-After': String(options.sklandSyncRetryAfter) } : undefined,
        )
      }
      const summary: SklandSyncSummary = { ...SKLAND_SYNC_SUMMARY, ...options.sklandSyncSummary }
      if (heldSync) await heldSync
      // 同步成功后，`GET /status` 的 `last_sync` 也跟着变（页面同步后本来就会失效重取）。
      sklandLastSync = summary
      return jsonResponse(summary)
    }

    if (url.pathname === '/api/skland/binding' && request.method === 'DELETE') {
      if (options.sklandUnbindStatus && options.sklandUnbindStatus >= 400) {
        return jsonResponse({ error: { code: 'INTERNAL_ERROR', message: '解绑失败' } }, options.sklandUnbindStatus)
      }
      sklandBoundByLogin = false
      sklandUnbound = true
      return jsonResponse({ bound: false })
    }

    return jsonResponse({ error: { code: 'NOT_FOUND', message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const find = (method: string, path: string) =>
    requests.filter((request) => request.method === method && request.path === path)
  return {
    requests,
    fetchMock,
    state,
    find,
    /** 取图请求的路径（按发生顺序），用来断言「图标走的是本机代理」。 */
    assetRequests,
    releaseSklandSync: () => releaseSklandSync?.(),
  }
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
