import { describe, expect, it } from 'vitest'
import {
  buildSchedulePayload,
  emptyScheduleForm,
  hasSchedulePrefill,
  readSchedulePrefill,
  SCHEDULE_POLICY,
  ScheduleTemplateError,
  scheduleFormFromView,
  scheduleSnapshot,
  toScheduleTemplate,
  validateScheduleForm,
  type ScheduleForm,
} from './payload'

function weekly(overrides: Partial<ScheduleForm> = {}): ScheduleForm {
  return { ...emptyScheduleForm(), name: '日常', ...overrides }
}

function thrown(run: () => unknown): unknown {
  try {
    run()
  } catch (error) {
    return error
  }
  throw new Error('expected the call to throw')
}

describe('schedule template whitelist', () => {
  it('passes the ten schedulable task types through with their changed parameters', () => {
    const template = [
      { name: 'StartUp', client_type: 'Official' },
      { name: 'CloseDown' },
      { name: 'Fight', stage: '1-7', medicine: 2 },
      { name: 'Recruit', times: 3 },
      { name: 'Infrast', facility: ['Mfg'] },
      { name: 'Mall' },
      { name: 'Award' },
      { name: 'Roguelike', theme: 'Sami' },
      { name: 'Reclamation' },
    ]
    expect(toScheduleTemplate(template)).toEqual(template)
  })

  it('keeps only asset_id and formation for Copilot (S2)', () => {
    expect(toScheduleTemplate([{ name: 'Copilot', asset_id: 'copilot-1', formation: true, stage_name: '1-7', filename: 'x.json' }]))
      .toEqual([{ name: 'Copilot', asset_id: 'copilot-1', formation: true }])
    expect(toScheduleTemplate([{ name: 'Copilot', asset_id: 'copilot-1' }]))
      .toEqual([{ name: 'Copilot', asset_id: 'copilot-1', formation: false }])
  })

  it('names the task that cannot be scheduled', () => {
    const missingAsset = thrown(() => toScheduleTemplate([{ name: 'StartUp' }, { name: 'Copilot', asset_id: '' }]))
    expect(missingAsset).toBeInstanceOf(ScheduleTemplateError)
    expect(missingAsset).toMatchObject({ index: 1, field: 'asset_id', message: '请选择一个已保存的 Copilot 作业。' })

    const unknown = thrown(() => toScheduleTemplate([{ name: 'SingleStep' }]))
    expect(unknown).toBeInstanceOf(ScheduleTemplateError)
    expect(unknown).toMatchObject({ index: 0, field: undefined })
    expect((unknown as Error).message).toContain('SingleStep')
  })
})

describe('schedule payload', () => {
  it('builds a POSIX weekly cron and always sends the fixed queue policy (S1)', () => {
    const payload = buildSchedulePayload(weekly({ name: '  周末日常  ', weekdays: [6, 0], time: '07:30' }), [{ name: 'StartUp' }])
    expect(payload).toEqual({
      name: '周末日常',
      cron: '30 7 * * 0,6',
      timezone: 'Asia/Shanghai',
      template: [{ name: 'StartUp' }],
      enabled: true,
      priority: 2,
      misfire_grace_seconds: 300,
      catch_up: false,
      skip_if_running: true,
    })
    expect(SCHEDULE_POLICY).toEqual({ priority: 2, misfire_grace_seconds: 300, catch_up: false, skip_if_running: true })
  })

  it('keeps a raw cron unchanged and falls back to Asia/Shanghai for a blank timezone (S6)', () => {
    const payload = buildSchedulePayload(weekly({ mode: 'cron', cron: ' 0 7 * * MON-FRI ', timezone: '  ', enabled: false }), [{ name: 'Award' }])
    expect(payload).toMatchObject({ cron: '0 7 * * MON-FRI', timezone: 'Asia/Shanghai', enabled: false })
  })

  it('whitelists the template on the way out', () => {
    expect(buildSchedulePayload(weekly(), [{ name: 'Copilot', asset_id: 'copilot-1', stage_name: '1-7' }]).template)
      .toEqual([{ name: 'Copilot', asset_id: 'copilot-1', formation: false }])
    expect(() => buildSchedulePayload(weekly(), [{ name: 'SingleStep' }])).toThrow(ScheduleTemplateError)
  })
})

describe('schedule form', () => {
  it('starts every day at 07:00 in Asia/Shanghai, enabled', () => {
    expect(emptyScheduleForm()).toEqual({ name: '', mode: 'weekly', weekdays: [0, 1, 2, 3, 4, 5, 6], time: '07:00', cron: '', timezone: 'Asia/Shanghai', enabled: true })
    expect(emptyScheduleForm('每日刷图').name).toBe('每日刷图')
  })

  it('reads a visual cron back into weekdays and time', () => {
    expect(scheduleFormFromView({ name: '周末日常', cron: '0 7 * * 0,6', timezone: 'Asia/Tokyo', enabled: false })).toEqual({
      name: '周末日常', mode: 'weekly', weekdays: [0, 6], time: '07:00', cron: '', timezone: 'Asia/Tokyo', enabled: false,
    })
  })

  it('switches to raw cron mode for expressions outside the weekly range (S6)', () => {
    expect(scheduleFormFromView({ name: '夜间', cron: '0 7 * * MON-FRI', timezone: 'UTC', enabled: true })).toMatchObject({
      mode: 'cron', cron: '0 7 * * MON-FRI', timezone: 'UTC',
    })
  })

  it('reports a missing name, weekdays, time, cron and tasks', () => {
    expect(validateScheduleForm(weekly(), 1)).toEqual({})
    expect(validateScheduleForm(weekly({ name: '  ', weekdays: [], time: '' }), 0)).toEqual({
      name: '请填写名称。',
      weekdays: '至少选择一天。',
      time: '请填写有效的时间。',
      tasks: '至少添加一个任务。',
    })
    expect(validateScheduleForm(weekly({ mode: 'cron', cron: ' ', weekdays: [] }), 1)).toEqual({ cron: '请填写 cron 表达式。' })
  })

  it('treats reordered weekdays, padded names and new draft ids as unchanged', () => {
    const template = [{ name: 'Fight', stage: '1-7' }]
    const baseline = scheduleSnapshot(weekly({ weekdays: [0, 6] }), template)
    expect(scheduleSnapshot(weekly({ weekdays: [6, 0], name: '日常 ' }), [{ name: 'Fight', stage: '1-7' }])).toBe(baseline)
    expect(scheduleSnapshot(weekly({ weekdays: [0, 6], time: '08:00' }), template)).not.toBe(baseline)
    expect(scheduleSnapshot(weekly({ weekdays: [0, 6] }), [{ name: 'Fight', stage: '1-8' }])).not.toBe(baseline)
    expect(scheduleSnapshot(weekly({ weekdays: [0, 6], enabled: false }), template)).not.toBe(baseline)
  })
})

describe('schedule handoffs', () => {
  it('reads the composer prefill with its name and the API console prefill (S5, T13)', () => {
    expect(readSchedulePrefill({ composerPrefill: { name: ' 每日刷图 ', template: [{ name: 'Fight', stage: '1-7' }] } })).toEqual({
      source: 'composer', name: '每日刷图', template: [{ name: 'Fight', stage: '1-7' }],
    })
    expect(readSchedulePrefill({ apiConsolePrefill: { template: [{ name: 'Award' }, { stage: '1-7' }, 'Mall'] } })).toEqual({
      source: 'api-console', name: '', template: [{ name: 'Award' }],
    })
  })

  it('ignores missing or malformed prefills but still recognizes the handoff keys', () => {
    expect(readSchedulePrefill(null)).toBeNull()
    expect(readSchedulePrefill({ apiConsolePrefill: { template: { tasks: [{ name: 'Fight' }] } } })).toBeNull()
    expect(readSchedulePrefill({ composerPrefill: { name: '空', template: [] } })).toBeNull()
    expect(hasSchedulePrefill({ apiConsolePrefill: { template: { tasks: [] } } })).toBe(true)
    expect(hasSchedulePrefill({ composerPrefill: {} })).toBe(true)
    expect(hasSchedulePrefill({ other: true })).toBe(false)
    expect(hasSchedulePrefill(undefined)).toBe(false)
  })
})
