import { describe, expect, it } from 'vitest'
import {
  SOURCE_TAG_LIMIT,
  formatFetchedAt,
  formatTokenCount,
  parseToolSources,
  sourceLinkHref,
  toolLabel,
  toolRegionNote,
} from './format'

describe('toolLabel', () => {
  it('names the tools M18 added in Chinese', () => {
    expect(toolLabel('read_knowledge')).toBe('查阅知识库')
    expect(toolLabel('wait_pipeline')).toBe('等待流水线结束')
  })

  it('names the tool M24 added in Chinese', () => {
    expect(toolLabel('get_activity_stages')).toBe('查当期活动')
  })

  it('names the three M25 knowledge tools in Chinese', () => {
    expect(toolLabel('get_stage')).toBe('查关卡情报')
    expect(toolLabel('search_prts')).toBe('搜索 PRTS')
    expect(toolLabel('get_prts_page')).toBe('读 PRTS 页面')
    // M24 的中文名不变（§5.12：改造过的工具不跟着改）。
    expect(toolLabel('get_activity_stages')).toBe('查当期活动')
  })

  it('leaves unknown tools to the raw name', () => {
    expect(toolLabel('some_future_tool')).toBeUndefined()
  })

  it('names the five media tools M23 added in Chinese', () => {
    expect(toolLabel('send_screenshot')).toBe('发送截图')
    expect(toolLabel('annotate_screenshot')).toBe('圈选截图')
    expect(toolLabel('send_item_icons')).toBe('发送物品图标')
    expect(toolLabel('send_stage_map')).toBe('发送关卡示意图')
    expect(toolLabel('send_chart')).toBe('发送图表')
  })
})

const PRTS_URL = 'https://prts.wiki/w/7-18_%E7%88%B1%E5%9B%BD%E8%80%85%E4%B9%8B%E6%AD%BB'

function source(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    kind: 'prts',
    label: 'PRTS',
    title: '7-18 爱国者之死',
    url: PRTS_URL,
    revision: 123_456,
    fetched_at: '2026-10-10T09:00:00+08:00',
    cached: true,
    stale: false,
    ...overrides,
  }
}

describe('sourceLinkHref', () => {
  it('links the six hosts the spec whitelists', () => {
    expect(sourceLinkHref('https://prts.wiki/w/7-18')).toBe('https://prts.wiki/w/7-18')
    expect(sourceLinkHref('https://prts.plus/copilot/1')).toBe('https://prts.plus/copilot/1')
    expect(sourceLinkHref('https://zoot.plus/copilot/1')).toBe('https://zoot.plus/copilot/1')
    expect(sourceLinkHref('https://penguin-stats.io/result/item/3003')).toBe('https://penguin-stats.io/result/item/3003')
    expect(sourceLinkHref('https://penguin-stats.cn/result/item/3003')).toBe('https://penguin-stats.cn/result/item/3003')
    expect(sourceLinkHref('https://github.com/Kengxxiao/ArknightsGameData')).toBe('https://github.com/Kengxxiao/ArknightsGameData')
  })

  it('accepts subdomains of a whitelisted host', () => {
    expect(sourceLinkHref('https://www.prts.wiki/w/1-7')).toBe('https://www.prts.wiki/w/1-7')
    expect(sourceLinkHref('https://raw.github.com/x/y')).toBe('https://raw.github.com/x/y')
  })

  it('refuses every other host, other schemes and junk (附录 D：前端只为白名单生成外链)', () => {
    expect(sourceLinkHref('https://example.com/x')).toBeNull()
    expect(sourceLinkHref('https://prts.wiki.evil.example/w/1-7')).toBeNull()
    expect(sourceLinkHref('https://notprts.wiki/w/1-7')).toBeNull()
    expect(sourceLinkHref('https://prts.maa.plus/copilot/1')).toBeNull()
    expect(sourceLinkHref('http://prts.wiki/w/1-7')).toBeNull()
    expect(sourceLinkHref('javascript:alert(1)')).toBeNull()
    expect(sourceLinkHref('data:text/html,<a href=x>y</a>')).toBeNull()
    expect(sourceLinkHref('/w/7-18')).toBeNull()
    expect(sourceLinkHref('')).toBeNull()
    expect(sourceLinkHref('   ')).toBeNull()
    expect(sourceLinkHref(null)).toBeNull()
    expect(sourceLinkHref(undefined)).toBeNull()
    expect(sourceLinkHref(42)).toBeNull()
  })
})

describe('parseToolSources', () => {
  it('reads the source envelope of a tool result', () => {
    expect(parseToolSources({ result: { sources: [source()], untrusted: true, unavailable: [], region_note: null } })).toEqual([
      {
        kind: 'prts',
        label: 'PRTS',
        title: '7-18 爱国者之死',
        url: PRTS_URL,
        fetchedAt: '2026-10-10T09:00:00+08:00',
        cached: true,
        stale: false,
      },
    ])
  })

  it('drops the url of a host outside the whitelist but keeps the title', () => {
    const [parsed] = parseToolSources({ result: { sources: [source({ url: 'https://example.com/7-18' })] } })
    expect(parsed).toMatchObject({ title: '7-18 爱国者之死', url: null })
  })

  it('keeps a source that only names the kind and skips entries without either name', () => {
    const parsed = parseToolSources({ sources: [source({ label: undefined, title: undefined }), { label: '  ' }, 'nope', null, 7] })
    expect(parsed).toEqual([{ kind: 'prts', label: 'prts', title: '', url: PRTS_URL, fetchedAt: '2026-10-10T09:00:00+08:00', cached: true, stale: false }])
  })

  it('reads flags and times defensively', () => {
    const [parsed] = parseToolSources({ sources: [source({ cached: 'yes', stale: 1, fetched_at: null })] })
    expect(parsed).toMatchObject({ cached: false, stale: false, fetchedAt: '' })
  })

  it('caps the tag count and ignores results without a usable envelope', () => {
    const many = Array.from({ length: SOURCE_TAG_LIMIT + 4 }, (_, index) => source({ title: `页面 ${index}` }))
    expect(parseToolSources({ result: { sources: many } })).toHaveLength(SOURCE_TAG_LIMIT)
    expect(parseToolSources(undefined)).toEqual([])
    expect(parseToolSources({ error: { code: 'EXTERNAL_SOURCE_UNAVAILABLE', message: '取不到' } })).toEqual([])
    expect(parseToolSources({ result: { sources: 'nope' } })).toEqual([])
  })
})

describe('toolRegionNote', () => {
  it('returns the region note of a result and treats blanks as absent', () => {
    expect(toolRegionNote({ result: { region_note: '国服数据，外服可能尚未实装或数值不同' } })).toBe('国服数据，外服可能尚未实装或数值不同')
    expect(toolRegionNote({ region_note: '  国服数据  ' })).toBe('国服数据')
    expect(toolRegionNote({ result: { region_note: '  ' } })).toBeNull()
    expect(toolRegionNote({ result: { region_note: null } })).toBeNull()
    expect(toolRegionNote({ result: {} })).toBeNull()
    expect(toolRegionNote(undefined)).toBeNull()
  })
})

describe('formatFetchedAt', () => {
  it('localizes the fetch time down to the minute', () => {
    const text = formatFetchedAt('2026-10-10T09:00:00+08:00')
    expect(text).toMatch(/^\d{4}\/\d{1,2}\/\d{1,2} \d{1,2}:\d{2}$/)
    expect(text).not.toMatch(/:\d{2}:\d{2}/)
  })

  it('leaves an unparsable time alone instead of inventing one', () => {
    expect(formatFetchedAt('刚刚')).toBe('刚刚')
    expect(formatFetchedAt('')).toBe('')
  })
})

describe('formatTokenCount', () => {
  it('switches to the million unit from 0.1M up, as the header asks for', () => {
    expect(formatTokenCount(0)).toBe('0')
    expect(formatTokenCount(999)).toBe('999')
    expect(formatTokenCount(1_000)).toBe('1K')
    expect(formatTokenCount(1_250)).toBe('1.3K')
    expect(formatTokenCount(99_999)).toBe('100K')
    // 恰好在上限边界：100000 已是 0.1M。
    expect(formatTokenCount(100_000)).toBe('0.1M')
  })

  it('renders the counts this deployment actually accumulates in millions', () => {
    // 用户截图里的那个数字。
    expect(formatTokenCount(510_195)).toBe('0.5M')
    expect(formatTokenCount(1_240_000)).toBe('1.2M')
    expect(formatTokenCount(12_000_000)).toBe('12M')
    expect(formatTokenCount(240_000_000)).toBe('240M')
  })

  it('drops trailing zeros and never shows a negative count', () => {
    expect(formatTokenCount(2_000_000)).toBe('2M')
    expect(formatTokenCount(5_000_000)).toBe('5M')
    expect(formatTokenCount(-5)).toBe('0')
  })

  it('treats missing or non-numeric values as zero', () => {
    expect(formatTokenCount(undefined)).toBe('0')
    expect(formatTokenCount(null)).toBe('0')
    expect(formatTokenCount('510195')).toBe('0')
    expect(formatTokenCount(Number.NaN)).toBe('0')
  })
})
