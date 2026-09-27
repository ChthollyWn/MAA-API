import { describe, expect, it } from 'vitest'
import { buildWeeklyCron, describeSchedule, describeWeekdays, parseWeeklyCron, sameWeekdays, WEEKDAY_PRESETS } from './schedule-time'

describe('weekly schedule cron conversion (S7)', () => {
  it('uses cron Sunday 0 for a Sunday-only schedule', () => {
    expect(buildWeeklyCron([0], '19:30')).toBe('30 19 * * 0')
    expect(parseWeeklyCron('30 19 * * 0', 'Asia/Shanghai')).toEqual({
      weekdays: [0],
      time: '19:30',
      timezone: 'Asia/Shanghai',
    })
  })

  it('round trips daily schedules using wildcard weekdays', () => {
    const cron = buildWeeklyCron([0, 1, 2, 3, 4, 5, 6], '07:00')
    expect(cron).toBe('0 7 * * *')
    expect(parseWeeklyCron(cron!, 'UTC')?.weekdays).toEqual([0, 1, 2, 3, 4, 5, 6])
  })

  it('round trips multiple selected weekdays in sorted cron order', () => {
    const cron = buildWeeklyCron([5, 1, 3], '08:05')
    expect(cron).toBe('5 8 * * 1,3,5')
    expect(parseWeeklyCron(cron!)?.weekdays).toEqual([1, 3, 5])
  })

  it('round trips a single weekday and accepts the standard Sunday alias 7', () => {
    expect(buildWeeklyCron([2], '00:00')).toBe('0 0 * * 2')
    expect(parseWeeklyCron('0 0 * * 7')?.weekdays).toEqual([0])
  })

  it('keeps wall-clock timezone separate from cron and rejects invalid visual values', () => {
    const schedule = parseWeeklyCron(buildWeeklyCron([1], '23:59')!, 'Pacific/Auckland')
    expect(schedule).toEqual({ weekdays: [1], time: '23:59', timezone: 'Pacific/Auckland' })
    expect(buildWeeklyCron([], '12:00')).toBeNull()
    expect(buildWeeklyCron([1], '24:00')).toBeNull()
    expect(parseWeeklyCron('*/5 * * * *', 'UTC')).toBeNull()
  })
})

describe('schedule descriptions', () => {
  it('names the common weekday groups', () => {
    expect(describeWeekdays([0, 1, 2, 3, 4, 5, 6])).toBe('每天')
    expect(describeWeekdays([1, 2, 3, 4, 5])).toBe('工作日')
    expect(describeWeekdays([6, 0])).toBe('周末')
  })

  it('collapses consecutive days into a range in Monday-first order', () => {
    expect(describeWeekdays([1, 2, 3])).toBe('周一至周三')
    expect(describeWeekdays([5, 6, 0])).toBe('周五至周日')
  })

  it('lists scattered days individually', () => {
    expect(describeWeekdays([1, 3, 5])).toBe('周一、周三、周五')
    expect(describeWeekdays([2])).toBe('周二')
    expect(describeWeekdays([1, 2])).toBe('周一、周二')
  })

  it('describes a visual weekly cron with its time and keeps unknown expressions unexplained', () => {
    expect(describeSchedule('0 4 * * 1-5')).toBe('工作日 04:00')
    expect(describeSchedule('30 7 * * *')).toBe('每天 07:30')
    expect(describeSchedule('0 */2 * * *')).toBeNull()
  })
})

describe('weekday shortcuts', () => {
  it('offers every day, workdays and the weekend', () => {
    expect(WEEKDAY_PRESETS.map((preset) => [preset.label, [...preset.weekdays]])).toEqual([
      ['每天', [0, 1, 2, 3, 4, 5, 6]],
      ['工作日', [1, 2, 3, 4, 5]],
      ['周末', [0, 6]],
    ])
  })

  it('compares weekday sets regardless of order and duplicates', () => {
    expect(sameWeekdays([6, 0], [0, 6])).toBe(true)
    expect(sameWeekdays([1, 1, 2], [2, 1])).toBe(true)
    expect(sameWeekdays([1, 2], [1, 2, 3])).toBe(false)
  })
})
