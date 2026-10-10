import type { OperatorPatch, OperatorRow } from './api'

/**
 * 编辑抽屉的前端校验（规格 §7.5），口径与后端的编辑约束一致：
 * 精英化上限按稀有度、专精只在精二且技能 7 级、模组只在精二、模组类型固定 X/Y/D/A。
 * 纯函数，页面与测试共用；保存时只把改动过的字段放进 `PATCH` 请求体。
 */

export const MODULE_TYPES = ['X', 'Y', 'D', 'A'] as const
export type ModuleType = (typeof MODULE_TYPES)[number]

export const SKILL_SLOTS = ['1', '2', '3'] as const

export const MAX_LEVEL = 90
export const MAX_POTENTIAL = 6
export const MAX_SKILL_LEVEL = 7
export const MAX_MASTERY = 3
export const MAX_MODULE_LEVEL = 3

/** 精英化上限：1–2★ 精零、3★ 精一、4–6★ 精二。 */
export function maxElite(rarity: number): number {
  if (rarity <= 2) return 0
  if (rarity === 3) return 1
  return 2
}

export function maxEliteLabel(rarity: number): string {
  return ['精零', '精一', '精二'][maxElite(rarity)] ?? '精零'
}

/** 技能数默认值：3★ 1 个、4–5★ 2 个、6★ 3 个（1–2★ 按 1 个）。 */
export function defaultSkillCount(rarity: number): number {
  if (rarity >= 6) return 3
  if (rarity >= 4) return 2
  return 1
}

export function canEditMasteries(draft: Pick<OperatorDraft, 'elite' | 'skillLevel'>): boolean {
  return draft.elite === 2 && draft.skillLevel === MAX_SKILL_LEVEL
}

export function canEditModules(draft: Pick<OperatorDraft, 'elite'>): boolean {
  return draft.elite === 2
}

export interface OperatorDraft {
  own: boolean
  elite: number
  level: number
  potential: number
  skillLevel: number
  /** 表单里显示几个专精输入（默认按稀有度给，可改）；不发给后端。 */
  skillCount: number
  /** "1"–"3" → 0–3。 */
  masteries: Record<string, number>
  /** X/Y/D/A → 1–3；没装备的类型不出现。 */
  modules: Record<string, number>
}

function masterslots(masteries: Record<string, number> | undefined): number {
  return SKILL_SLOTS.reduce((max, slot) => (Number(masteries?.[slot] ?? 0) > 0 ? Math.max(max, Number(slot)) : max), 0)
}

export function draftFromRow(row: OperatorRow): OperatorDraft {
  return {
    own: row.own,
    elite: row.elite,
    level: row.level,
    potential: row.potential,
    skillLevel: row.skill_level,
    skillCount: Math.max(defaultSkillCount(row.rarity), masterslots(row.masteries)),
    masteries: Object.fromEntries(SKILL_SLOTS.map((slot) => [slot, Number(row.masteries?.[slot] ?? 0)])),
    modules: Object.fromEntries(
      MODULE_TYPES.flatMap((type) => {
        const level = Number(row.modules?.[type] ?? 0)
        return level > 0 ? [[type, level] as const] : []
      }),
    ),
  }
}

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.min(max, Math.max(min, Math.round(value)))
}

/** 专精只保留已显示的技能槽；技能数调小后多出来的槽归零。 */
export function normalizeMasteries(draft: OperatorDraft): Record<string, number> {
  return Object.fromEntries(
    SKILL_SLOTS.map((slot) => [
      slot,
      Number(slot) <= draft.skillCount ? clampInt(Number(draft.masteries[slot] ?? 0), 0, MAX_MASTERY) : 0,
    ]),
  )
}

/** 模组只保留固定类型，等级夹在 1–3。 */
export function normalizeModules(draft: OperatorDraft): Record<string, number> {
  return Object.fromEntries(
    MODULE_TYPES.flatMap((type) => {
      const level = Number(draft.modules[type] ?? 0)
      return level > 0 ? [[type, clampInt(level, 1, MAX_MODULE_LEVEL)] as const] : []
    }),
  )
}

export type DraftField = 'own' | 'elite' | 'level' | 'potential' | 'skill_level' | 'masteries' | 'modules'

function isInt(value: number): boolean {
  return Number.isInteger(value)
}

/**
 * 逐字段校验；返回空对象表示可以保存。精英化越界、专精/模组在不允许的状态下填了非零值
 * 都会报错（抽屉里同时把这些控件禁掉，两道防线）。
 */
export function validateDraft(draft: OperatorDraft, rarity: number): Partial<Record<DraftField, string>> {
  const errors: Partial<Record<DraftField, string>> = {}
  const cap = maxElite(rarity)

  if (!isInt(draft.elite) || draft.elite < 0 || draft.elite > cap) {
    errors.elite = `${rarity}★ 干员的精英化上限是${maxEliteLabel(rarity)}`
  }
  if (!isInt(draft.level) || draft.level < 1 || draft.level > MAX_LEVEL) {
    errors.level = `等级需在 1–${MAX_LEVEL} 之间`
  }
  if (!isInt(draft.potential) || draft.potential < 1 || draft.potential > MAX_POTENTIAL) {
    errors.potential = `潜能需在 1–${MAX_POTENTIAL} 之间`
  }
  if (!isInt(draft.skillLevel) || draft.skillLevel < 1 || draft.skillLevel > MAX_SKILL_LEVEL) {
    errors.skill_level = `技能等级需在 1–${MAX_SKILL_LEVEL} 之间`
  }

  const masteries = normalizeMasteries(draft)
  const masteriesFilled = Object.values(masteries).some((level) => level > 0)
  if (masteriesFilled && !canEditMasteries(draft)) {
    errors.masteries = '专精只在精二且技能等级 7 时可填'
  }

  const modules = normalizeModules(draft)
  if (Object.keys(modules).length > 0 && !canEditModules(draft)) {
    errors.modules = '模组只在精二时可填'
  }

  return errors
}

function sameRecord(left: Record<string, number>, right: Record<string, number> | undefined): boolean {
  const keys = new Set([...Object.keys(left), ...Object.keys(right ?? {})])
  for (const key of keys) {
    if (Number(left[key] ?? 0) !== Number(right?.[key] ?? 0)) return false
  }
  return true
}

/**
 * 只把改动过的字段放进请求体。精英化降到精二以下时，原本有值的专精与模组会显式清零，
 * 免得服务端留下与精英化不一致的数据。
 */
export function buildPatch(row: OperatorRow, draft: OperatorDraft): OperatorPatch {
  const patch: OperatorPatch = {}
  if (draft.own !== row.own) patch.own = draft.own
  if (draft.elite !== row.elite) patch.elite = draft.elite
  if (draft.level !== row.level) patch.level = draft.level
  if (draft.potential !== row.potential) patch.potential = draft.potential
  if (draft.skillLevel !== row.skill_level) patch.skill_level = draft.skillLevel

  const masteries = canEditMasteries(draft) ? normalizeMasteries(draft) : zeroMasteries()
  if (!sameRecord(masteries, row.masteries)) patch.masteries = masteries

  const modules = canEditModules(draft) ? normalizeModules(draft) : {}
  if (!sameRecord(modules, row.modules)) patch.modules = modules

  return patch
}

/** 不能填专精时统一写 0（等于「没有专精」），只用于和现有值比较与清理。 */
function zeroMasteries(): Record<string, number> {
  return Object.fromEntries(SKILL_SLOTS.map((slot) => [slot, 0]))
}

export function patchIsEmpty(patch: OperatorPatch): boolean {
  return Object.keys(patch).length === 0
}
