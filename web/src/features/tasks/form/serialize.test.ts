import { describe, expect, it } from 'vitest'
import taskTypesFixture from '../task-types.fixture.json'
import { schemaProperties, type JsonSchema, type TaskTypeSchema } from './schema'
import { nonZeroRisk, riskSummary, serializeTask, type TaskDraft } from './serialize'
import { resetField, setField, setFieldNull } from './values'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const typeByName = (name: string) => {
  const found = types.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}
/** 恢复默认需要 schema 才知道是「写回默认值」还是「删键」（M17）。 */
const schemaOf = (field: string): JsonSchema => schemaProperties(typeByName('Fight').schema)[field]

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

  it('drops a restored field without a default and writes the default back for one that has it', () => {
    let params: Record<string, unknown> = {}
    params = setField(params, 'stage', '1-7')
    params = setField(params, 'medicine', 2)
    params = resetField(params, 'stage', schemaOf('stage'))
    // 有默认值的字段恢复默认是写回默认值（预填即下发），没有默认值的字段才是删键。
    expect('stage' in params).toBe(false)
    params = resetField(params, 'medicine', schemaOf('medicine'))
    params = setFieldNull(params, 'client_type')
    expect(JSON.parse(JSON.stringify(serializeTask({ name: 'Fight', params })))).toEqual({ name: 'Fight', medicine: 0, client_type: null })
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

  it('ignores risk fields that still hold their schema default', () => {
    // M17：草稿会预填默认值，`Mall.shopping` / `Roguelike.investment_enabled` 的内核默认就是 true，
    // 什么都不改的采购/肉鸽任务不应该弹「确认消耗资源」。
    const drafts: TaskDraft[] = [
      { id: '1', name: 'Mall', params: { shopping: true, buy_first: [] } },
      { id: '2', name: 'Roguelike', params: { investment_enabled: true } },
      { id: '3', name: 'Fight', params: { medicine: 0, stone: 0, expiring_medicine: 0 } },
    ]
    expect(riskSummary(drafts, types)).toEqual([])

    const raised: TaskDraft[] = [{ id: '4', name: 'Fight', params: { medicine: 1 } }]
    expect(riskSummary(raised, types)).toEqual(['刷理智 · 最大理智药数：1'])
  })
})
