import { describe, expect, it } from 'vitest'
import taskTypesFixture from '../task-types.fixture.json'
import { schemaProperties, type JsonSchema, type TaskTypeSchema } from './schema'
import {
  UNSET,
  countModified,
  fieldState,
  isFieldModified,
  moveArrayItem,
  resetField,
  resetValueFor,
  sameValue,
  setField,
  setFieldNull,
  setObjectValue,
  updateArrayItem,
} from './values'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const typeByName = (name: string) => {
  const found = types.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}
const fightFields = schemaProperties(typeByName('Fight').schema)
const schemaOf = (name: string): JsonSchema => fightFields[name]

describe('setObjectValue', () => {
  it('sets and deletes top-level keys without mutating the input', () => {
    const input = { stage: '1-7' }
    expect(setObjectValue(input, ['medicine'], 2)).toEqual({ stage: '1-7', medicine: 2 })
    expect(setObjectValue(input, ['stage'], UNSET)).toEqual({})
    expect(setObjectValue(input, ['stage'], null)).toEqual({ stage: null })
    expect(input).toEqual({ stage: '1-7' })
    expect(setObjectValue(input, [], 1)).toBe(input)
  })

  it('updates nested objects and drops parents emptied by UNSET', () => {
    expect(setObjectValue({}, ['drops', '30013'], 2)).toEqual({ drops: { '30013': 2 } })
    expect(setObjectValue({ drops: { '30013': 2, '30012': 1 } }, ['drops', '30013'], UNSET)).toEqual({ drops: { '30012': 1 } })
    expect(setObjectValue({ drops: { '30013': 2 }, stage: '1-7' }, ['drops', '30013'], UNSET)).toEqual({ stage: '1-7' })
    expect(setObjectValue({ drops: 'broken' }, ['drops', '30013'], 1)).toEqual({ drops: { '30013': 1 } })
  })
})

describe('array helpers', () => {
  it('replaces or removes one array item', () => {
    const input = ['Mfg', 'Trade', 'Power']
    expect(updateArrayItem(input, 1, 'Dorm')).toEqual(['Mfg', 'Dorm', 'Power'])
    expect(updateArrayItem(input, 1, UNSET)).toEqual(['Mfg', 'Power'])
    expect(input).toEqual(['Mfg', 'Trade', 'Power'])
  })

  it('moves an item up or down and ignores moves past either end', () => {
    const input = ['Mfg', 'Trade', 'Power']
    expect(moveArrayItem(input, 1, -1)).toEqual(['Trade', 'Mfg', 'Power'])
    expect(moveArrayItem(input, 1, 1)).toEqual(['Mfg', 'Power', 'Trade'])
    expect(moveArrayItem(input, 0, -1)).toBe(input)
    expect(moveArrayItem(input, 2, 1)).toBe(input)
  })
})

describe('sameValue', () => {
  it('compares arrays and objects structurally, regardless of key order', () => {
    expect(sameValue([1, 4, 5], [1, 4, 5])).toBe(true)
    expect(sameValue([1, 4, 5], [1, 5, 4])).toBe(false)
    expect(sameValue({ '3': 540, '4': 540 }, { '4': 540, '3': 540 })).toBe(true)
    expect(sameValue({ '3': 540 }, { '3': 480 })).toBe(false)
    expect(sameValue({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true)
    expect(sameValue({ a: [1, { b: 2 }] }, { a: [1, { b: 3 }] })).toBe(false)
    expect(sameValue({ a: 1 }, { a: 1, b: 2 })).toBe(false)
  })

  it('keeps primitives, null and shapes apart', () => {
    expect(sameValue('', '')).toBe(true)
    expect(sameValue(0, false)).toBe(false)
    expect(sameValue('1', 1)).toBe(false)
    expect(sameValue([1], { 0: 1 })).toBe(false)
    expect(sameValue(null, undefined)).toBe(false)
    expect(sameValue(null, null)).toBe(true)
    expect(sameValue(undefined, undefined)).toBe(true)
  })
})

describe('draft field operations', () => {
  it('keeps only the fields the user touched, restores the schema default on reset and records explicit nulls', () => {
    let draft: Record<string, unknown> = {}
    expect(fieldState(draft, 'stage', schemaOf('stage'))).toBe('unset')
    expect(fieldState(draft, 'medicine', schemaOf('medicine'))).toBe('default')

    draft = setField(draft, 'stage', '1-7')
    draft = setField(draft, 'medicine', 2)
    expect(draft).toEqual({ stage: '1-7', medicine: 2 })
    expect(fieldState(draft, 'stage', schemaOf('stage'))).toBe('modified')
    expect(fieldState(draft, 'medicine', schemaOf('medicine'))).toBe('modified')

    // 关卡名没有默认值：恢复默认就是从草稿里删掉，回到「不提交」的状态。
    draft = resetField(draft, 'stage', schemaOf('stage'))
    expect(draft).toEqual({ medicine: 2 })
    expect('stage' in draft).toBe(false)

    // 最大理智药数有默认值 0：恢复默认是写回默认值，键留在草稿里（预填即下发）。
    draft = resetField(draft, 'medicine', schemaOf('medicine'))
    expect(draft).toEqual({ medicine: 0 })
    expect(fieldState(draft, 'medicine', schemaOf('medicine'))).toBe('default')
    expect(isFieldModified(draft, 'medicine', schemaOf('medicine'))).toBe(false)

    draft = setFieldNull(draft, 'stage')
    expect(draft).toEqual({ medicine: 0, stage: null })
    expect(fieldState(draft, 'stage', schemaOf('stage'))).toBe('null')
    expect(isFieldModified(draft, 'stage', schemaOf('stage'))).toBe(true)
    expect(isFieldModified(draft, 'times', schemaOf('times'))).toBe(false)
  })

  it('reports the four states against the schema default and counts modified and null fields', () => {
    // 缺键：有默认值算「默认」，没有默认值（或缺省值是 null）算「未设置」。
    expect(fieldState({}, 'medicine', schemaOf('medicine'))).toBe('default')
    expect(fieldState({}, 'times', schemaOf('times'))).toBe('unset')
    expect(fieldState({}, 'stage', schemaOf('stage'))).toBe('unset')
    // 与默认值深相等（数组与映射按结构比较）算「默认」。
    expect(fieldState({ select: [1, 4, 5] }, 'select', schemaProperties(typeByName('Recruit').schema).select)).toBe('default')
    expect(fieldState({ recruitment_time: { '4': 540, '6': 540, '3': 540, '5': 540 } }, 'recruitment_time', schemaProperties(typeByName('Recruit').schema).recruitment_time)).toBe('default')
    expect(fieldState({ select: [1, 4] }, 'select', schemaProperties(typeByName('Recruit').schema).select)).toBe('modified')
    expect(fieldState({ medicine: null }, 'medicine', schemaOf('medicine'))).toBe('null')
    // 显式 undefined 等同于缺键。
    expect(fieldState({ stage: undefined }, 'stage', schemaOf('stage'))).toBe('unset')
    expect(fieldState({ medicine: undefined }, 'medicine', schemaOf('medicine'))).toBe('default')

    const schemas = { series: schemaOf('series'), medicine: schemaOf('medicine'), times: schemaOf('times'), stage: schemaOf('stage') }
    expect(countModified({}, schemas)).toBe(0)
    expect(countModified({ series: 1, medicine: 0 }, schemas)).toBe(0)
    expect(countModified({ series: 2, medicine: null, times: undefined, stage: '1-7' }, schemas)).toBe(3)
  })

  it('hands the reset value to controls: the default when there is one, UNSET otherwise', () => {
    expect(resetValueFor(schemaOf('medicine'))).toBe(0)
    expect(resetValueFor(schemaOf('stage'))).toBe(UNSET)
    expect(resetValueFor(schemaOf('times'))).toBe(UNSET)
  })
})
