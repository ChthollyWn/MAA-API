import { describe, expect, it } from 'vitest'
import {
  compactText,
  finiteNumber,
  formatDateTime,
  missingMaterialsText,
  parsePlanStages,
  planRunsTotal,
  planStagesText,
  planStatusLabel,
  upgradeTargetText,
} from './format'

describe('M31 养成计划的格式化', () => {
  it('compactText：字符串原样，其它走紧凑 JSON，读不出给占位符', () => {
    expect(compactText('固源岩')).toBe('固源岩')
    expect(compactText(12)).toBe('12')
    expect(compactText({ a: 1 })).toBe('{"a":1}')
    expect(compactText(null)).toBe('—')
    expect(compactText(undefined)).toBe('—')
  })

  it('compactText：循环引用也不抛错', () => {
    const loop: Record<string, unknown> = {}
    loop.self = loop
    expect(compactText(loop)).toBe('[object Object]')
  })

  it('finiteNumber：数字与数字字符串都认，其余为 null', () => {
    expect(finiteNumber(5)).toBe(5)
    expect(finiteNumber('5')).toBe(5)
    expect(finiteNumber('')).toBeNull()
    expect(finiteNumber('  ')).toBeNull()
    expect(finiteNumber(true)).toBeNull()
    expect(finiteNumber('abc')).toBeNull()
    expect(finiteNumber(null)).toBeNull()
  })

  it('upgradeTargetText：拼出精二 · 技能 7 · S2 3 · 模组 X2', () => {
    expect(
      upgradeTargetText({ elite: 2, skill_level: 7, masteries: { '2': 3 }, modules: [{ type: 'X', level: 2 }] }),
    ).toBe('精二 · 技能 7 · S2 3 · 模组 X2')
    expect(upgradeTargetText({ elite: 1 })).toBe('精一')
    expect(upgradeTargetText({})).toBe('不改变养成目标')
    expect(upgradeTargetText(null)).toBe('不改变养成目标')
    expect(upgradeTargetText('阿米娅')).toBe('不改变养成目标')
  })

  it('planStatusLabel：三种状态有中文，没见过的原样显示', () => {
    expect(planStatusLabel('active')).toBe('进行中')
    expect(planStatusLabel('completed')).toBe('已完成')
    expect(planStatusLabel('cancelled')).toBe('已取消')
    expect(planStatusLabel('paused')).toBe('paused')
    expect(planStatusLabel(null)).toBe('未知')
  })

  it('parsePlanStages：读 stage_code/runs/sanity/items，形状不认识就降级成一行', () => {
    const stages = parsePlanStages([
      { stage_id: 1, stage_code: '1-7', runs: 12, sanity: 216, items: [{ item_name: '固源岩' }, { name: '源岩' }] },
      { stage_code: 'CE-6', runs: '5' },
      'LS-6',
    ])
    expect(stages).toEqual([
      { label: '1-7', runs: 12, sanity: 216, items: ['固源岩', '源岩'] },
      { label: 'CE-6', runs: 5, sanity: null, items: [] },
      { label: 'LS-6', runs: null, sanity: null, items: [] },
    ])
    expect(parsePlanStages(null)).toEqual([])
    expect(parsePlanStages({ stage_code: '1-7' })).toEqual([])
  })

  it('planStagesText / planRunsTotal：关卡与场次汇总', () => {
    const stages = parsePlanStages([{ stage_code: '1-7', runs: 5 }, { stage_code: 'CE-6', runs: 12 }])
    expect(planStagesText(stages)).toBe('1-7 ×5、CE-6 ×12')
    expect(planRunsTotal(stages)).toBe(17)
    expect(planStagesText([])).toBeNull()
    expect(planRunsTotal([])).toBeNull()
  })

  it('missingMaterialsText：列出缺口，跳过已经备齐的', () => {
    expect(missingMaterialsText({ 固源岩: 12, 聚酸酯: 0 })).toBe('固源岩 ×12')
    expect(missingMaterialsText({ 固源岩: 3, 聚酸酯: 2 })).toBe('固源岩 ×3、聚酸酯 ×2')
    expect(missingMaterialsText({ 固源岩: 0 })).toBeNull()
    expect(missingMaterialsText(null)).toBeNull()
  })

  it('formatDateTime：本地时间，无效或缺失返回 null', () => {
    expect(formatDateTime('2026-10-10T15:54:00')).toBe('2026-10-10 15:54')
    expect(formatDateTime('not-a-date')).toBeNull()
    expect(formatDateTime(null)).toBeNull()
    expect(formatDateTime(undefined)).toBeNull()
  })
})
