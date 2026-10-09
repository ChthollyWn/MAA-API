import { describe, expect, it } from 'vitest'
import { copyableLine, formatDateTimeLocal, formatFullTime, formatLogTime, logImage, sourceLabel } from '@/features/logs/format'
import type { LogRecord } from '@/realtime/events'

const seconds = (...parts: [number, number, number, number, number, number, number?]) => {
  const [year, month, day, hour, minute, second, ms = 0] = parts
  return new Date(year, month - 1, day, hour, minute, second, ms).getTime() / 1000
}

describe('log time formatting', () => {
  it('shows only the clock for today and adds the date otherwise', () => {
    const now = new Date(2026, 8, 27, 12, 0, 0)
    expect(formatLogTime(seconds(2026, 9, 27, 8, 5, 9), now)).toBe('08:05:09')
    expect(formatLogTime(seconds(2026, 9, 26, 23, 59, 1), now)).toBe('09-26 23:59:01')
    expect(formatLogTime(undefined, now)).toBe('时间未知')
  })

  it('formats the full local time with milliseconds', () => {
    expect(formatFullTime(seconds(2026, 9, 27, 8, 5, 9, 42))).toBe('2026-09-27 08:05:09.042')
    expect(formatFullTime('soon')).toBe('时间未知')
  })

  it('drops the year of datetime-local values in the current year', () => {
    const now = new Date(2026, 8, 27)
    expect(formatDateTimeLocal('2026-09-27T10:00', now)).toBe('09-27 10:00')
    expect(formatDateTimeLocal('2025-01-02T03:04', now)).toBe('2025-01-02 03:04')
  })
})

describe('log labels', () => {
  it('names known sources in Chinese and keeps unknown ones', () => {
    expect(sourceLabel('task')).toBe('任务')
    expect(sourceLabel('service')).toBe('服务')
    expect(sourceLabel('core')).toBe('内核')
    expect(sourceLabel('plugin')).toBe('plugin')
  })

  it('builds one copyable line with time, level, source and logger', () => {
    const line = copyableLine({ id: 1, source: 'core', level: 'warn', content: 'a\nb', ts: seconds(2026, 9, 27, 8, 5, 9), logger: 'maa.core' })
    expect(line).toBe('2026-09-27 08:05:09.000 WARNING core maa.core - a\nb')
    expect(copyableLine({ id: 2, source: 'task', content: 'data:image/png;base64,AAAA' })).toBe('时间未知 INFO task - 内嵌图片内容已省略')
  })
})

describe('screenshot attachments', () => {
  const hash = 'b'.repeat(64)

  it('accepts only same-origin image endpoints and derives them from the hash', () => {
    const fromUrls: LogRecord = { id: 1, source: 'task', attachment: { thumb_url: `/api/images/${hash}/thumb`, full_url: `/api/images/${hash}/full` } }
    expect(logImage(fromUrls)).toEqual({ thumb: `/api/images/${hash}/thumb`, full: `/api/images/${hash}/full` })

    const fromHash: LogRecord = { id: 2, source: 'task', attachment: { sha256: hash, thumb_url: 'https://evil.example/x.png' } }
    expect(logImage(fromHash)).toEqual({ thumb: `/api/images/${hash}/thumb`, full: `/api/images/${hash}/full` })

    expect(logImage({ id: 3, source: 'task', attachment: { thumb_url: 'https://evil.example/x.png' } })).toBeNull()
    expect(logImage({ id: 4, source: 'task' })).toBeNull()
  })
})
