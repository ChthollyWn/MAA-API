import { describe, expect, it } from 'vitest'
import type { OperatorRow } from './api'
import {
  buildPatch,
  canEditMasteries,
  canEditModules,
  defaultSkillCount,
  draftFromRow,
  maxElite,
  patchIsEmpty,
  validateDraft,
} from './constraints'
import { KNOWN_EMPTY_ROW, OPERATORS, UNKNOWN_ALL_ROW, UNKNOWN_SKILL_ROW, operatorRow } from './test-utils'

const amiya = OPERATORS[0]

describe('M27 编辑约束', () => {
  it('精英化上限按稀有度：1–2★ 精零、3★ 精一、4–6★ 精二', () => {
    expect([1, 2, 3, 4, 5, 6].map(maxElite)).toEqual([0, 0, 1, 2, 2, 2])
  })

  it('技能数默认按稀有度：3★ 1 个、4–5★ 2 个、6★ 3 个', () => {
    expect([1, 2, 3, 4, 5, 6].map(defaultSkillCount)).toEqual([1, 1, 1, 2, 2, 3])
  })

  it('专精只在精二且技能 7 级时可填', () => {
    expect(canEditMasteries({ elite: 2, skillLevel: 7 })).toBe(true)
    expect(canEditMasteries({ elite: 2, skillLevel: 6 })).toBe(false)
    expect(canEditMasteries({ elite: 1, skillLevel: 7 })).toBe(false)
    expect(canEditMasteries({ elite: 0, skillLevel: 1 })).toBe(false)
  })

  it('模组只在精二时可填', () => {
    expect(canEditModules({ elite: 2 })).toBe(true)
    expect(canEditModules({ elite: 1 })).toBe(false)
  })

  it('抽屉初值来自这一行，技能数取稀有度默认与已有专精槽的较大值', () => {
    const draft = draftFromRow(amiya)
    expect(draft).toMatchObject({ own: true, elite: 2, level: 80, potential: 6, skillLevel: 7, skillCount: 2 })
    expect(draft.masteries).toEqual({ '1': 3, '2': 1, '3': 0 })
    expect(draft.modules).toEqual({ X: 2 })
  })

  it('精英化超过稀有度上限时报错', () => {
    const row = operatorRow({ char_id: 'char_3', name: '三星', rarity: 3, elite: 0 })
    const draft = { ...draftFromRow(row), elite: 2 }
    expect(validateDraft(draft, row.rarity).elite).toMatch(/精英化上限是精一/)
  })

  it('等级、潜能、技能等级越界时报错', () => {
    const draft = { ...draftFromRow(amiya), level: 0, potential: 9, skillLevel: 8 }
    const errors = validateDraft(draft, amiya.rarity)
    expect(errors.level).toBeTruthy()
    expect(errors.potential).toBeTruthy()
    expect(errors.skill_level).toBeTruthy()
  })

  it('精一或技能不到 7 时填了专精会报错', () => {
    const draft = { ...draftFromRow(amiya), elite: 1, masteries: { '1': 2, '2': 0, '3': 0 } }
    expect(validateDraft(draft, amiya.rarity).masteries).toMatch(/专精只在精二且技能等级 7 时可填/)
  })

  it('非精二时填了模组会报错', () => {
    const draft = { ...draftFromRow(amiya), elite: 1, modules: { X: 1 } }
    expect(validateDraft(draft, amiya.rarity).modules).toMatch(/模组只在精二时可填/)
  })

  it('没有改动时请求体为空', () => {
    expect(patchIsEmpty(buildPatch(amiya, draftFromRow(amiya)))).toBe(true)
  })

  it('只把改过的字段放进请求体', () => {
    const draft = { ...draftFromRow(amiya), level: 70 }
    expect(buildPatch(amiya, draft)).toEqual({ level: 70 })
  })

  it('技能数调小时多出来的专精槽归零', () => {
    const draft = { ...draftFromRow(amiya), skillCount: 1 }
    expect(buildPatch(amiya, draft)).toEqual({ masteries: { '1': 3, '2': 0, '3': 0 } })
  })

  it('精英化降到精二以下时显式清空专精与模组', () => {
    const draft = { ...draftFromRow(amiya), elite: 1, skillLevel: 4 }
    expect(buildPatch(amiya, draft)).toEqual({
      elite: 1,
      skill_level: 4,
      masteries: { '1': 0, '2': 0, '3': 0 },
      modules: {},
    })
  })

  it('模组等级夹在 1–3，未知类型不会写进去', () => {
    const draft = { ...draftFromRow(amiya), modules: { X: 9, Y: 0, Z: 2 } }
    expect(buildPatch(amiya, draft)).toEqual({ modules: { X: 3 } })
  })

  it('3★ 干员的精英化选项与技能数默认值都按稀有度收窄', () => {
    const row: OperatorRow = operatorRow({ char_id: 'char_3', name: '三星', rarity: 3, elite: 1, level: 55, skill_level: 7 })
    const draft = draftFromRow(row)
    expect(draft.skillCount).toBe(1)
    expect(validateDraft({ ...draft, elite: 1 }, row.rarity)).toEqual({})
  })
})

describe('M35 未识别练度', () => {
  it('未识别的列读进草稿仍是 null，不折算成 0 / 1 / 空表', () => {
    expect(draftFromRow(UNKNOWN_ALL_ROW)).toMatchObject({
      elite: null,
      level: null,
      potential: null,
      skillLevel: null,
      masteries: null,
      modules: null,
    })
    expect(draftFromRow(UNKNOWN_ALL_ROW).skillCount).toBe(defaultSkillCount(UNKNOWN_ALL_ROW.rarity))

    // 识别给得出的三列有值，技能 / 专精 / 模组三列未识别——M35 的真实现场。
    expect(draftFromRow(UNKNOWN_SKILL_ROW)).toMatchObject({
      elite: 2,
      level: 90,
      potential: 1,
      skillLevel: null,
      masteries: null,
      modules: null,
    })
    expect(draftFromRow(UNKNOWN_SKILL_ROW).skillCount).toBe(3)
  })

  it('未知不拦专精与模组的补录（与后端「约束只在已知时判」同口径）', () => {
    expect(canEditMasteries({ elite: null, skillLevel: null })).toBe(true)
    expect(canEditMasteries({ elite: null, skillLevel: 4 })).toBe(true)
    expect(canEditMasteries({ elite: 0, skillLevel: null })).toBe(true)
    expect(canEditModules({ elite: null })).toBe(true)

    const draft = {
      ...draftFromRow(UNKNOWN_SKILL_ROW),
      masteries: { '1': 3, '2': 0, '3': 0 },
      modules: { X: 1 },
    }
    expect(validateDraft(draft, UNKNOWN_SKILL_ROW.rarity)).toEqual({})
  })

  it('已知的非法组合照旧拦：技能不到 7 时填了专精仍报错', () => {
    const draft = {
      ...draftFromRow(UNKNOWN_SKILL_ROW),
      skillLevel: 4,
      masteries: { '1': 3, '2': 0, '3': 0 },
    }
    expect(validateDraft(draft, UNKNOWN_SKILL_ROW.rarity).masteries).toMatch(/专精只在精二且技能等级 7 时可填/)
  })

  it('未识别的列不写进 PATCH：留空就是留空，不会被兜成 0 / 空表', () => {
    expect(patchIsEmpty(buildPatch(UNKNOWN_ALL_ROW, draftFromRow(UNKNOWN_ALL_ROW)))).toBe(true)
    expect(patchIsEmpty(buildPatch(UNKNOWN_SKILL_ROW, draftFromRow(UNKNOWN_SKILL_ROW)))).toBe(true)

    // 只补等级：技能 / 专精 / 模组仍未识别，不跟着写。
    const draft = { ...draftFromRow(UNKNOWN_SKILL_ROW), level: 85 }
    expect(buildPatch(UNKNOWN_SKILL_ROW, draft)).toEqual({ level: 85 })
  })

  it('已知的空专精 / 空模组仍是具体值，不会被当成未识别', () => {
    const draft = draftFromRow(KNOWN_EMPTY_ROW)
    expect(draft.masteries).toEqual({ '1': 0, '2': 0, '3': 0 })
    expect(draft.modules).toEqual({})
    expect(patchIsEmpty(buildPatch(KNOWN_EMPTY_ROW, draft))).toBe(true)
  })
})
