import { describe, expect, it } from 'vitest'
import taskTypesFixture from '../task-types.fixture.json'
import type { TaskTypeSchema } from './schema'
import { nonZeroRisk, riskSummary, serializeTask, type TaskDraft } from './serialize'
import { resetField, setField, setFieldNull } from './values'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items

describe('serializeTask', () => {
  it('sends only the task name when nothing was changed', () => {
    const draft: TaskDraft = { id: 'a', name: 'StartUp', params: {} }
    expect(serializeTask(draft)).toEqual({ name: 'StartUp' })
    expect(serializeTask({ name: 'Copilot', params: {} })).toEqual({ name: 'Copilot' })
  })

  it('keeps nested values and explicit nulls exactly as edited', () => {
    const draft: TaskDraft = { id: 'a', name: 'Fight', params: { stage: null, drops: { '30013': 2 } } }
    expect(serializeTask(draft)).toEqual({ name: 'Fight', stage: null, drops: { '30013': 2 } })
    expect(serializeTask({ name: 'Recruit', params: { recruitment_time: { '3': 480 }, select: [4, 5] } }))
      .toEqual({ name: 'Recruit', recruitment_time: { '3': 480 }, select: [4, 5] })
  })

  it('does not include fields that were changed and then restored', () => {
    let params: Record<string, unknown> = {}
    params = setField(params, 'stage', '1-7')
    params = setField(params, 'medicine', 2)
    params = resetField(params, 'stage')
    params = setFieldNull(params, 'client_type')
    expect(JSON.parse(JSON.stringify(serializeTask({ name: 'Fight', params })))).toEqual({ name: 'Fight', medicine: 2, client_type: null })
  })
})

describe('risk summary', () => {
  it('treats empty, zero, false and null values as harmless', () => {
    for (const value of [undefined, null, false, 0, '', [], {}]) expect(nonZeroRisk(value)).toBe(false)
    for (const value of [1, -1, true, 'x', [0], { '30013': 1 }]) expect(nonZeroRisk(value)).toBe(true)
  })

  it('lists changed x-risk fields with the task label, field label and JSON value', () => {
    const drafts: TaskDraft[] = [
      { id: '1', name: 'Fight', params: { stage: '1-7', medicine: 2, stone: 0 } },
      { id: '2', name: 'Fight', params: { expiring_medicine: null } },
      { id: '3', name: 'Recruit', params: { expedite: true, expedite_times: 3 } },
      { id: '4', name: 'Mall', params: { shopping: false } },
      { id: '5', name: 'Unknown', params: { medicine: 9 } },
    ]
    expect(riskSummary(drafts, types)).toEqual([
      '刷理智 · 最大理智药数：2',
      '自动公招 · 使用加急许可：true',
    ])
    expect(riskSummary([{ name: 'Fight', params: {} }], types)).toEqual([])
  })
})
