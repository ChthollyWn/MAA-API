import { describe, expect, it } from 'vitest'
import { formatWhen, runDetail, runStatusLabel, runTone, scheduleTimeSummary } from './format'

describe('schedule run results', () => {
  it('labels run statuses in any case and keeps unknown values visible', () => {
    expect(runStatusLabel('completed')).toBe('成功')
    expect(runStatusLabel('COMPLETED')).toBe('成功')
    expect(runStatusLabel('success')).toBe('成功')
    expect(runStatusLabel('failed')).toBe('失败')
    expect(runStatusLabel('error')).toBe('失败')
    expect(runStatusLabel('running')).toBe('运行中')
    expect(runStatusLabel('pending')).toBe('排队中')
    expect(runStatusLabel('skipped')).toBe('已跳过')
    expect(runStatusLabel('cancelled')).toBe('已取消')
    expect(runStatusLabel('paused')).toBe('paused')
    expect(runStatusLabel('')).toBe('结果未知')
  })

  it('colors results by outcome', () => {
    expect(runTone('completed')).toBe('success')
    expect(runTone('failed')).toBe('danger')
    expect(runTone('running')).toBe('primary')
    expect(runTone('skipped')).toBe('warning')
    expect(runTone('pending')).toBe('neutral')
    expect(runTone('cancelled')).toBe('neutral')
  })

  it('shows the error message and explains a skipped run without one (S3)', () => {
    expect(runDetail({ status: 'failed', error: { code: 'CORE_CRASHED', message: '内核崩溃' } })).toEqual({ text: '内核崩溃', tone: 'danger' })
    expect(runDetail({ status: 'skipped', error: { message: '同一 schedule 仍有未完成流水线' } })).toEqual({ text: '同一 schedule 仍有未完成流水线', tone: 'muted' })
    expect(runDetail({ status: 'skipped', error: null })).toEqual({ text: '上一次还没执行完，这次到点跳过了。', tone: 'muted' })
    expect(runDetail({ status: 'completed', error: null })).toBeNull()
  })
})

describe('schedule times', () => {
  const now = new Date(2026, 8, 27, 16, 0)

  it('names nearby days and falls back to the date', () => {
    expect(formatWhen(new Date(2026, 8, 27, 7, 5).toISOString(), now)).toBe('今天 07:05')
    expect(formatWhen(new Date(2026, 8, 28, 7, 0).toISOString(), now)).toBe('明天 07:00')
    expect(formatWhen(new Date(2026, 8, 26, 23, 0).toISOString(), now)).toBe('昨天 23:00')
    expect(formatWhen(new Date(2026, 9, 3, 4, 0).toISOString(), now)).toBe('10月3日 04:00')
    expect(formatWhen(new Date(2027, 0, 2, 4, 0).toISOString(), now)).toBe('2027年1月2日 04:00')
    expect(formatWhen(null, now)).toBe('—')
    expect(formatWhen('not a date', now)).toBe('—')
  })

  it('summarizes weekly cron, falls back to the raw expression and adds a foreign timezone', () => {
    expect(scheduleTimeSummary({ cron: '0 4 * * 1-5', timezone: 'Asia/Shanghai' }, 'Asia/Shanghai')).toBe('工作日 04:00')
    expect(scheduleTimeSummary({ cron: '0 */2 * * *', timezone: 'Asia/Shanghai' }, 'Asia/Shanghai')).toBe('cron 0 */2 * * *')
    expect(scheduleTimeSummary({ cron: '0 4 * * 1-5', timezone: 'UTC' }, 'Asia/Shanghai')).toBe('工作日 04:00（UTC）')
  })
})
