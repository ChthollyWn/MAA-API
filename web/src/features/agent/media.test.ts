import { describe, expect, it } from 'vitest'
import { MEDIA_LIMITS, mediaCardsOf, mediaImageSource, parseMediaCards } from './media'
import type { ChartCardData, ImageCardData, ItemIconsCardData, StageMapCardData } from './media'

const SHA = 'a'.repeat(64)
const OTHER_SHA = 'b'.repeat(64)

function envelope(media: unknown, extra: Record<string, unknown> = {}) {
  return { result: { media, ...extra } }
}

function imageCard(overrides: Record<string, unknown> = {}) {
  return {
    type: 'image',
    screenshot_id: 'shot-1',
    sha256: SHA,
    url: `/api/images/${SHA}/full`,
    thumb_url: `/api/images/${SHA}/thumb`,
    alt: '关卡失败画面',
    caption: '第 3 步失败',
    width: 1920,
    height: 1080,
    ...overrides,
  }
}

function itemIconsCard(overrides: Record<string, unknown> = {}) {
  return {
    type: 'item_icons',
    caption: '这些材料够刷三次',
    items: [
      { item_id: '2001', name: '基础作战记录', icon_url: '/api/resources/items/icon?item_id=2001' },
      { item_id: '3003', name: '固源岩', icon_url: '/api/resources/items/icon?item_id=3003' },
    ],
    ...overrides,
  }
}

function stageMapCard(overrides: Record<string, unknown> = {}, stage: Record<string, unknown> = {}) {
  return {
    type: 'stage_map',
    caption: '1-7 的布局',
    stage: {
      code: '1-7',
      name: '暴君',
      stage_id: 'main_01-07',
      width: 3,
      height: 2,
      candidates: 2,
      tiles: [
        ['#00', '.10', 'w01'],
        ['.00', '!00', '~00'],
      ],
      start: [[0, 0]],
      end: [[2, 1]],
      legend: [{ code: '!00', label: '特殊地形' }],
      ...stage,
    },
    ...overrides,
  }
}

function chartCard(overrides: Record<string, unknown> = {}, chart: Record<string, unknown> = {}) {
  return {
    type: 'chart',
    caption: '最近一次掉落',
    chart: {
      chart_type: 'bar',
      title: '1-7 掉落',
      categories: ['固源岩', '龙门币'],
      series: [{ name: '数量', values: [12, 3] }],
      stacked: false,
      x_label: '物品',
      y_label: '个数',
      unit: '个',
      source: 'get_drop_stats(stage=1-7)',
      ...chart,
    },
    ...overrides,
  }
}

describe('mediaImageSource', () => {
  it('accepts the three same-origin image addresses', () => {
    expect(mediaImageSource(`/api/images/${SHA}/thumb`)).toEqual({ kind: 'image', sha256: SHA, size: 'thumb' })
    expect(mediaImageSource(`/api/images/${SHA}/full`)).toEqual({ kind: 'image', sha256: SHA, size: 'full' })
    expect(mediaImageSource('/api/screenshots/shot-1')).toEqual({ kind: 'screenshot', id: 'shot-1', size: 'full' })
    expect(mediaImageSource('/api/resources/items/icon?item_id=2001')).toEqual({ kind: 'resource-icon', itemId: '2001' })
  })

  it('decodes percent-encoded item ids and keeps them out of the URL', () => {
    expect(mediaImageSource('/api/resources/items/icon?item_id=%E5%9B%BA%E6%BA%90%E5%B2%A9')).toEqual({
      kind: 'resource-icon',
      itemId: '固源岩',
    })
    // 解码后带分隔符的一律拒绝，避免拼出第二个查询参数或路径。
    expect(mediaImageSource('/api/resources/items/icon?item_id=a%2Fb')).toBeNull()
    expect(mediaImageSource('/api/resources/items/icon?item_id=a&x=1')).toBeNull()
  })

  it('rejects foreign hosts, data URLs, relative paths and case mismatches', () => {
    for (const url of [
      `https://evil.example/api/images/${SHA}/thumb`,
      `//evil.example/api/images/${SHA}/thumb`,
      `data:image/png;base64,AAAA`,
      `images/${SHA}/thumb`,
      `/api/images/${SHA.toUpperCase()}/thumb`,
      `/API/IMAGES/${SHA}/thumb`,
      '/api/screenshots/',
      `/api/images/${SHA}/original`,
      '',
      null,
      42,
    ]) {
      expect(mediaImageSource(url)).toBeNull()
    }
  })
})

describe('parseMediaCards', () => {
  it('parses all four card types out of the result envelope', () => {
    const cards = parseMediaCards(envelope([imageCard(), itemIconsCard(), stageMapCard(), chartCard()]))
    expect(cards.map((card) => card.type)).toEqual(['image', 'item_icons', 'stage_map', 'chart'])

    const image = cards[0] as ImageCardData
    expect(image.thumb).toEqual({ kind: 'image', sha256: SHA, size: 'thumb' })
    expect(image.full).toEqual({ kind: 'image', sha256: SHA, size: 'full' })
    expect(image.alt).toBe('关卡失败画面')
    expect(image.width).toBe(1920)

    const icons = cards[1] as ItemIconsCardData
    expect(icons.items.map((item) => item.itemId)).toEqual(['2001', '3003'])
    expect(icons.items[0]!.icon).toEqual({ kind: 'resource-icon', itemId: '2001' })
    expect(icons.items[0]!.name).toBe('基础作战记录')

    const stage = cards[2] as StageMapCardData
    expect(stage.width).toBe(3)
    expect(stage.tiles[0]).toEqual(['#00', '.10', 'w01'])
    expect(stage.start).toEqual([[0, 0]])
    expect(stage.candidates).toBe(2)
    expect(stage.legend).toEqual([{ code: '!00', label: '特殊地形' }])

    const chart = cards[3] as ChartCardData
    expect(chart.chartType).toBe('bar')
    expect(chart.series).toEqual([{ name: '数量', values: [12, 3] }])
    expect(chart.source).toBe('get_drop_stats(stage=1-7)')
  })

  it('keeps a screenshot-record image card that has no sha256', () => {
    const cards = parseMediaCards(envelope([imageCard({ sha256: null, url: '/api/screenshots/shot-1', thumb_url: null })]))
    const image = cards[0] as ImageCardData
    expect(image.sha256).toBeNull()
    expect(image.full).toEqual({ kind: 'screenshot', id: 'shot-1', size: 'full' })
    expect(image.thumb).toEqual({ kind: 'screenshot', id: 'shot-1', size: 'thumb' })
  })

  it('drops only the card with a foreign or data URL, not the whole batch', () => {
    const cards = parseMediaCards(envelope([
      imageCard({ url: 'https://evil.example/x.png' }),
      chartCard(),
      imageCard({ thumb_url: 'data:image/png;base64,AAAA' }),
      imageCard({ sha256: OTHER_SHA, url: `/api/images/${OTHER_SHA}/full`, thumb_url: `/api/images/${OTHER_SHA}/thumb` }),
    ]))
    expect(cards).toHaveLength(2)
    expect(cards.every((card) => card.type === 'chart' || (card as ImageCardData).sha256 === OTHER_SHA)).toBe(true)
  })

  it('ignores unknown types, missing fields and wrong types without throwing', () => {
    expect(parseMediaCards(envelope([
      { type: 'video', url: 'x' },
      null,
      'nope',
      { type: 'chart' },
      { type: 'chart', chart: { chart_type: 'radar', title: 'x', categories: ['a'], series: [{ name: 's', values: [1] }] } },
      { type: 'stage_map', stage: { width: 3, height: 2, tiles: [['#00']] } },
      { type: 'image', url: 'nope', thumb_url: null },
      itemIconsCard({ items: [{ item_id: '2001', icon_url: 'https://evil.example/i.png' }] }),
      chartCard({}, { title: '' }),
      chartCard({}, { series: [{ name: '数量', values: ['12'] }] }),
    ]))).toEqual([])
    expect(parseMediaCards(undefined)).toEqual([])
    expect(parseMediaCards({ error: { code: 'X', message: 'y' } })).toEqual([])
    expect(parseMediaCards({ result: { media: 'nope' } })).toEqual([])
  })

  it('rejects NaN and Infinity values instead of drawing a broken chart', () => {
    const withNaN = parseMediaCards(envelope([chartCard({}, { series: [{ name: '数量', values: [Number.NaN] }] })]))
    const withInfinity = parseMediaCards(envelope([chartCard({}, { series: [{ name: '数量', values: [Number.POSITIVE_INFINITY] }] })]))
    expect(withNaN).toEqual([])
    expect(withInfinity).toEqual([])
    // null 是合法断点，要留着。
    const withNull = parseMediaCards(envelope([chartCard({}, { series: [{ name: '数量', values: [null, 3] }] })])) as ChartCardData[]
    expect(withNull[0]!.series[0]!.values).toEqual([null, 3])
  })

  it('enforces the limits defensively', () => {
    const many = Array.from({ length: MEDIA_LIMITS.cards + 3 }, (_, index) => imageCard({ sha256: SHA, url: `/api/images/${SHA}/full`, thumb_url: `/api/images/${SHA}/thumb`, caption: `#${index}` }))
    expect(parseMediaCards(envelope(many))).toHaveLength(MEDIA_LIMITS.cards)

    const tooManyIcons = parseMediaCards(envelope([itemIconsCard({
      items: Array.from({ length: MEDIA_LIMITS.icons + 1 }, () => ({ item_id: '2001', icon_url: '/api/resources/items/icon?item_id=2001' })),
    })]))
    expect(tooManyIcons).toEqual([])

    const tooManySeries = parseMediaCards(envelope([chartCard({}, {
      series: Array.from({ length: MEDIA_LIMITS.series + 1 }, (_, index) => ({ name: `s${index}`, values: [1] })),
    })]))
    expect(tooManySeries).toEqual([])

    const tooManyCategories = parseMediaCards(envelope([chartCard({}, {
      categories: Array.from({ length: MEDIA_LIMITS.categories + 1 }, (_, index) => `c${index}`),
      series: [{ name: '数量', values: [1] }],
    })]))
    expect(tooManyCategories).toEqual([])

    // 文本超限是截断而不是丢卡（规格 §3.5 的长度上限）。
    const long = parseMediaCards(envelope([chartCard({ caption: 'c'.repeat(500) }, { title: 't'.repeat(200), source: 's'.repeat(400) })])) as ChartCardData[]
    expect(long).toHaveLength(1)
    expect(long[0]!.title).toHaveLength(MEDIA_LIMITS.title)
    expect(long[0]!.caption).toHaveLength(MEDIA_LIMITS.caption)
    expect(long[0]!.source).toHaveLength(MEDIA_LIMITS.source)
  })

  it('drops a stage grid whose tiles do not match width/height', () => {    expect(parseMediaCards(envelope([stageMapCard({}, { tiles: [['#00', '.10', 'w01']] })]))).toEqual([])
    expect(parseMediaCards(envelope([stageMapCard({}, { tiles: [['#00', '.10'], ['.00', '!00', '~00']] })]))).toEqual([])
    expect(parseMediaCards(envelope([stageMapCard({}, { tiles: [['#00', '.1', 'w01'], ['.00', '!00', '~00']] })]))).toEqual([])
    expect(parseMediaCards(envelope([stageMapCard({}, { width: 100, height: 100 })]))).toEqual([])
    // 越界的起终点坐标只是被忽略，不影响同一张卡的其余数据。
    const cards = parseMediaCards(envelope([stageMapCard({}, { start: [[9, 9]], end: [[2, 1]] })])) as StageMapCardData[]
    expect(cards[0]!.start).toEqual([])
    expect(cards[0]!.end).toEqual([[2, 1]])
  })

  it('keeps the partial legend codes stage_layout actually emits (1 或 2 字符)', () => {
    const cards = parseMediaCards(envelope([stageMapCard({}, {
      legend: [
        { code: '!', label: '特殊机制' },
        { code: '11', label: '可部署类型 1 · 高台' },
        { code: '', label: '空码丢弃' },
        'nope',
      ],
    })])) as StageMapCardData[]
    expect(cards[0]!.legend).toEqual([
      { code: '!', label: '特殊机制' },
      { code: '11', label: '可部署类型 1 · 高台' },
    ])
  })

  it('synthesises an image card for the pre-M23 get_screenshot payload', () => {
    const legacy = {
      result: {
        screenshot_id: 'shot-9',
        sha256: SHA,
        full_url: `/api/images/${SHA}/full`,
        thumb_url: `/api/images/${SHA}/thumb`,
        width: 1280,
        height: 720,
        format: 'jpeg',
      },
    }
    const cards = parseMediaCards(legacy, 'get_screenshot')
    expect(cards).toHaveLength(1)
    const image = cards[0] as ImageCardData
    expect(image.type).toBe('image')
    expect(image.screenshotId).toBe('shot-9')
    expect(image.full).toEqual({ kind: 'image', sha256: SHA, size: 'full' })
    // `media` 为 null 也按缺失处理，同样合成图片卡。
    expect(parseMediaCards({ result: { ...legacy.result, media: null } }, 'get_screenshot')).toHaveLength(1)
    // 其他工具的同形状结果不会被误认成图片卡。
    expect(parseMediaCards(legacy, 'get_logs')).toEqual([])
    expect(parseMediaCards(legacy)).toEqual([])
  })

  it('prefers media over the legacy payload when both exist', () => {
    const cards = parseMediaCards({ result: { screenshot_id: 'shot-9', sha256: SHA, media: [chartCard()] } }, 'get_screenshot')
    expect(cards.map((card) => card.type)).toEqual(['chart'])
  })

  it('reads cards through mediaCardsOf for both live and historical tool views', () => {
    expect(mediaCardsOf({ name: 'send_chart', result: envelope([chartCard()]) })).toHaveLength(1)
    expect(mediaCardsOf({ name: 'send_chart', result: undefined })).toEqual([])
    // 实时事件经 liveToolView 后的形状与历史一致：{result: {media: [...]}}。
    expect(mediaCardsOf({ name: 'send_chart', result: { result: { media: [imageCard()] } } })).toHaveLength(1)
  })
})
