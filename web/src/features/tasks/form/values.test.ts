import { describe, expect, it } from 'vitest'
import {
  UNSET,
  countModified,
  fieldState,
  isFieldModified,
  moveArrayItem,
  resetField,
  setField,
  setFieldNull,
  setObjectValue,
  updateArrayItem,
} from './values'

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

describe('draft field operations', () => {
  it('keeps only the fields the user touched', () => {
    let draft: Record<string, unknown> = {}
    expect(fieldState(draft, 'stage')).toBe('default')

    draft = setField(draft, 'stage', '1-7')
    draft = setField(draft, 'medicine', 2)
    expect(draft).toEqual({ stage: '1-7', medicine: 2 })
    expect(fieldState(draft, 'stage')).toBe('modified')

    draft = resetField(draft, 'stage')
    expect(draft).toEqual({ medicine: 2 })
    expect('stage' in draft).toBe(false)

    draft = setFieldNull(draft, 'stage')
    expect(draft).toEqual({ medicine: 2, stage: null })
    expect(fieldState(draft, 'stage')).toBe('null')
    expect(isFieldModified(draft, 'stage')).toBe(true)
    expect(isFieldModified(draft, 'times')).toBe(false)
  })

  it('treats an explicit undefined as untouched and counts modified fields', () => {
    expect(fieldState({ stage: undefined }, 'stage')).toBe('default')
    expect(countModified({ stage: '1-7', medicine: null, times: undefined }, ['stage', 'medicine', 'times', 'stone'])).toBe(2)
  })
})
