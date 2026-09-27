import { describe, expect, it } from 'vitest'
import {
  ANY_TIME,
  NO_FILTERS,
  asEpoch,
  filterConditions,
  matchesLevels,
  presetRange,
  removeCondition,
  sourceFromPath,
  sourcePath,
  toDateTimeLocal,
  visibleRecords,
  type LogFilters,
} from '@/features/logs/filters'
import type { LogRecord } from '@/realtime/events'

function record(id: number, patch: Partial<LogRecord> = {}): LogRecord {
  return { id, source: 'task', level: 'INFO', content: `log-${id}`, ts: id, ...patch }
}

describe('log sources', () => {
  it('maps routes to sources and back', () => {
    expect(sourceFromPath('/logs')).toBe('all')
    expect(sourceFromPath('/logs/task/')).toBe('task')
    expect(sourceFromPath('/logs/service')).toBe('service')
    expect(sourceFromPath('/logs/core')).toBe('core')
    expect(sourceFromPath('/logs/unknown')).toBe('all')
    expect(sourcePath('all')).toBe('/logs')
    expect(sourcePath('core')).toBe('/logs/core')
  })
})

describe('level matching', () => {
  it('treats no selection as all levels, WARN as WARNING and a missing level as INFO', () => {
    expect(matchesLevels(record(1, { level: 'DEBUG' }), [])).toBe(true)
    expect(matchesLevels(record(1, { level: 'warn' }), ['WARNING'])).toBe(true)
    expect(matchesLevels(record(1, { level: undefined }), ['INFO'])).toBe(true)
    expect(matchesLevels(record(1, { level: 'ERROR' }), ['WARNING', 'CRITICAL'])).toBe(false)
    expect(matchesLevels(record(1, { level: 'CRITICAL' }), ['WARNING', 'CRITICAL'])).toBe(true)
  })
})

describe('time range', () => {
  it('reads datetime-local values as local time in epoch seconds', () => {
    expect(asEpoch('')).toBeUndefined()
    expect(asEpoch('not a date')).toBeUndefined()
    expect(asEpoch('2026-09-27T10:00')).toBe(new Date(2026, 8, 27, 10, 0).getTime() / 1000)
  })

  it('formats local dates for datetime-local inputs', () => {
    expect(toDateTimeLocal(new Date(2026, 0, 5, 7, 3, 59))).toBe('2026-01-05T07:03')
  })

  it('anchors quick ranges at the moment they are chosen', () => {
    const now = new Date(2026, 8, 27, 10, 30, 45)
    expect(presetRange('last-15m', now)).toEqual({ preset: 'last-15m', since: '2026-09-27T10:15', until: '' })
    expect(presetRange('last-1h', now)).toEqual({ preset: 'last-1h', since: '2026-09-27T09:30', until: '' })
    expect(presetRange('today', now)).toEqual({ preset: 'today', since: '2026-09-27T00:00', until: '' })
  })
})

describe('active conditions', () => {
  it('lists each level in severity order and one chip per time bound', () => {
    const preset: LogFilters = { levels: ['ERROR', 'WARNING'], range: presetRange('last-1h') }
    expect(filterConditions(preset).map((item) => item.label)).toEqual(['WARNING', 'ERROR', '最近 1 小时'])

    const custom: LogFilters = { levels: [], range: { preset: null, since: '2025-03-01T08:00', until: '2025-03-02T09:30' } }
    expect(filterConditions(custom).map((item) => item.label)).toEqual(['开始 2025-03-01 08:00', '结束 2025-03-02 09:30'])
    expect(filterConditions(NO_FILTERS)).toEqual([])
  })

  it('removes a single condition and keeps the others', () => {
    const filters: LogFilters = { levels: ['ERROR', 'WARNING'], range: presetRange('today') }
    const [, error, time] = filterConditions(filters)

    const withoutError = removeCondition(filters, error!.id)
    expect(withoutError.levels).toEqual(['WARNING'])
    expect(withoutError.range).toBe(filters.range)

    const withoutTime = removeCondition(filters, time!.id)
    expect(withoutTime.levels).toBe(filters.levels)
    expect(withoutTime.range).toEqual(ANY_TIME)

    const custom: LogFilters = { levels: [], range: { preset: null, since: '2026-09-27T08:00', until: '2026-09-27T09:00' } }
    const [since] = filterConditions(custom)
    expect(removeCondition(custom, since!.id).range).toEqual({ preset: null, since: '', until: '2026-09-27T09:00' })
  })
})

describe('visible records', () => {
  it('merges history and live records by id, then filters locally and sorts ascending', () => {
    const historyRows = [record(3, { content: 'stale' }), record(1, { source: 'core' }), record(2, { level: 'ERROR' })]
    const liveRows = [record(3, { content: 'fresh' }), record(4, { content: 'Needle here', ts: 50 })]

    expect(visibleRecords(historyRows, liveRows, { source: 'all', levels: [], keyword: '' }).map((item) => item.id)).toEqual([1, 2, 3, 4])
    expect(visibleRecords(historyRows, liveRows, { source: 'all', levels: [], keyword: '' })[2]!.content).toBe('fresh')
    expect(visibleRecords(historyRows, liveRows, { source: 'task', levels: [], keyword: '' }).map((item) => item.id)).toEqual([2, 3, 4])
    expect(visibleRecords(historyRows, liveRows, { source: 'all', levels: ['ERROR'], keyword: '' }).map((item) => item.id)).toEqual([2])
    expect(visibleRecords(historyRows, liveRows, { source: 'all', levels: [], keyword: ' needle ' }).map((item) => item.id)).toEqual([4])
    expect(visibleRecords(historyRows, liveRows, { source: 'all', levels: [], keyword: '', since: 3, until: 10 }).map((item) => item.id)).toEqual([3])
  })
})
