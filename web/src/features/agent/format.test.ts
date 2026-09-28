import { describe, expect, it } from 'vitest'
import { formatTokenCount } from './format'

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
