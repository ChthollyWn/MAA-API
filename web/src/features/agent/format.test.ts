import { describe, expect, it } from 'vitest'
import { formatTokenCount, toolLabel } from './format'

describe('toolLabel', () => {
  it('names the tools M18 added in Chinese', () => {
    expect(toolLabel('read_knowledge')).toBe('查阅知识库')
    expect(toolLabel('wait_pipeline')).toBe('等待流水线结束')
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
