import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchDepot, fetchOperators } from './api'
import { jsonResponse } from './test-utils'

/**
 * 请求层的契约对照（M34）。
 *
 * 假后端 `installAccountApi` 与页面测试各自自洽，M33 的真机事故就是两边都没发现
 * 「后端给 `items`、前端读 `operators`」。这里用一个**独立的**响应字面量钉住形状：
 * 以后谁改动容器键，这一组会红。类型层还有一道 `pnpm typecheck` 的护栏（响应模型
 * 进了 `web/src/types/api.d.ts`），两条一起才够。
 */

function recordCalls() {
  const calls: string[] = []
  return { calls, fetchMock: vi.fn<typeof fetch>() }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('账号请求层的容器键', () => {
  it('干员列表按路由的响应模型读 items：未识别练度保持 null，不兜成 0 / 空表', async () => {
    const { calls, fetchMock } = recordCalls()
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init)
      const url = new URL(request.url)
      calls.push(`${request.method} ${url.pathname}${url.search}`)
      return jsonResponse({
        items: [
          {
            char_id: 'char_002_amiya',
            name: '阿米娅',
            rarity: 5,
            profession: '术师',
            own: true,
            elite: 2,
            level: 80,
            potential: 6,
            skill_level: 7,
            masteries: { '1': 3 },
            modules: { X: 1 },
            field_sources: { elite: 'recognition' },
            recognized_at: '2026-10-10T12:08:40Z',
            updated_at: '2026-10-10T12:08:40Z',
          },
          // 未识别（M35）：只有 `all_opers` 条目的干员只带 `own`，练度六列读出来是 null。
          { char_id: 'char_9999', own: false },
        ],
        total: 383,
        limit: 50,
        offset: 0,
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchOperators({ owned_only: true, limit: 50, offset: 0 })

    expect(calls[0]).toContain('/api/account/operators?')
    expect(calls[0]).toContain('owned_only=true')
    expect(payload.total).toBe(383)
    expect(payload.limit).toBe(50)
    expect(payload.items.map((row) => row.char_id)).toEqual(['char_002_amiya', 'char_9999'])
    expect(payload.items[0].name).toBe('阿米娅')
    // 有值的练度原样透传。
    expect(payload.items[0]).toMatchObject({
      elite: 2,
      level: 80,
      potential: 6,
      skill_level: 7,
      masteries: { '1': 3 },
      modules: { X: 1 },
    })
    // 未识别的练度**不许**兜成 0 / 1 / 空表（M35 的护栏：这里写 `masteries: {}` 就会红）。
    expect(payload.items[1]).toEqual({
      char_id: 'char_9999',
      name: '',
      rarity: 0,
      profession: null,
      own: false,
      elite: null,
      level: null,
      potential: null,
      skill_level: null,
      masteries: null,
      modules: null,
      // M43 新增的三列同样不许兜：不知道就是 null。
      favor_percent: null,
      skill_ids: null,
      module_details: null,
      field_sources: {},
      recognized_at: null,
      updated_at: null,
    })
  })

  it('旧键 operators 不再被接受——这正是 M34 空列表的现场', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ operators: [{ char_id: 'char_002_amiya', own: true }], total: 1, limit: 50, offset: 0 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    // 运行时不再猜键：拿不到就是空。护栏在类型层（`data.items` 写错过不了 typecheck），
    // 这条用例把「读错键＝空列表」的现场写进测试，避免有人靠 `??` 再兜一次。
    const payload = await fetchOperators({ limit: 50, offset: 0 })
    expect(payload.items).toEqual([])
    expect(payload.total).toBe(1)
  })

  it('仓库列表读 items 与后端按整份快照给的 categories', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        items: [
          {
            item_id: '30012',
            name: '聚酸酯组',
            count: 3,
            category: '材料',
            recognized_at: '2026-10-10T12:08:40Z',
          },
          // 映射不到的物品：名字为空，请求层用 item_id 兜底。
          { item_id: '99999', count: 1, category: null, recognized_at: null },
        ],
        total: 2,
        limit: 100,
        offset: 0,
        categories: ['技巧概要', '材料'],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchDepot({ limit: 100, offset: 0 })

    expect(payload.items).toEqual([
      { item_id: '30012', name: '聚酸酯组', count: 3, category: '材料' },
      { item_id: '99999', name: '99999', count: 1, category: null },
    ])
    expect(payload.categories).toEqual(['技巧概要', '材料'])
    expect(payload.total).toBe(2)
  })

  it('数量为 null 时保留 null（防御性），不兜底成 0', async () => {
    // 后端只返回有明确数量的行，所以这是异常形状；真出现时页面显示「—」而不是 0。
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        items: [{ item_id: '7003', name: '寻访凭证', count: null, category: '凭证' }],
        total: 1,
        limit: 100,
        offset: 0,
        categories: ['凭证'],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchDepot({ limit: 100, offset: 0 })

    expect(payload.items).toEqual([
      { item_id: '7003', name: '寻访凭证', count: null, category: '凭证' },
    ])
  })

  it('后端少给 items / categories 时容器给安全默认值', async () => {
    // 老版本的后端（或代理截断）少给一项：页面不该因此炸掉，缺的那项按空数组处理。
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ total: 1, limit: 100, offset: 0 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchDepot({ limit: 100, offset: 0 })

    expect(payload.items).toEqual([])
    expect(payload.categories).toEqual([])
    expect(payload.total).toBe(1)
  })
})
