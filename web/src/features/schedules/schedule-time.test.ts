import { describe, expect, it } from 'vitest'
import { describeSchedule, describeWeekdays } from './schedule-time'

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
