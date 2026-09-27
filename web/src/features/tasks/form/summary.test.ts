import { describe, expect, it } from 'vitest'
import taskTypesFixture from '../task-types.fixture.json'
import type { TaskTypeSchema } from './schema'
import { DEFAULT_PARAMS_SUMMARY, summarizeTaskParams } from './summary'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const typeByName = (name: string) => {
  const found = types.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}

describe('summarizeTaskParams', () => {
  it('says the task uses default parameters when nothing was changed', () => {
    expect(DEFAULT_PARAMS_SUMMARY).toBe('使用默认参数')
    expect(summarizeTaskParams(typeByName('Fight'), {})).toBe('使用默认参数')
    expect(summarizeTaskParams(typeByName('Fight'), { stage: undefined })).toBe('使用默认参数')
  })

  it('shows the stage value and compact numeric labels (Fight 1-7 · 理智药 2)', () => {
    expect(summarizeTaskParams(typeByName('Fight'), { medicine: 2, stage: '1-7' })).toBe('1-7 · 理智药 2')
    expect(summarizeTaskParams(typeByName('Fight'), { stone: 1 })).toBe('碎石 1')
    expect(summarizeTaskParams(typeByName('Fight'), { times: 3 })).toBe('指定次数 3')
  })

  it('uses Chinese enum labels, 开/关 for booleans and item counts for arrays and maps', () => {
    expect(summarizeTaskParams(typeByName('Roguelike'), { theme: 'Mizuki', mode: 1 })).toBe('水月与深蓝之树 · 刷源石锭')
    expect(summarizeTaskParams(typeByName('Fight'), { series: -1 })).toBe('禁用切换')
    expect(summarizeTaskParams(typeByName('Award'), { mail: true, recruit: false })).toBe('领取邮件奖励 开 · 领取每日免费单抽 关')
    expect(summarizeTaskParams(typeByName('Infrast'), { facility: ['Mfg', 'Trade'] })).toBe('要换班的设施 2 项')
    expect(summarizeTaskParams(typeByName('Fight'), { drops: { '30013': 5 } })).toBe('指定掉落数量 1 项')
    expect(summarizeTaskParams(typeByName('Fight'), { stage: null })).toBe('关卡名 留空')
  })

  it('keeps schema order, shows at most three fields and stays within about 24 characters', () => {
    const summary = summarizeTaskParams(typeByName('Fight'), { drops: { '30013': 5 }, stone: 1, medicine: 2, times: 3, stage: '1-7' })
    expect(summary).toBe('1-7 · 指定次数 3 · 理智药 2 +2')
    expect(Array.from(summary).length).toBeLessThanOrEqual(24)
    expect(summarizeTaskParams(typeByName('Recruit'), { select: [4, 5], confirm: [4] })).toBe('点击的 Tag 等级 2 项 +1')
  })

  it('truncates long values instead of overflowing the row', () => {
    const summary = summarizeTaskParams(typeByName('Infrast'), { filename: 'C:/Users/doctor/Documents/maa/custom_infrast_plan.json' })
    expect(summary.endsWith('…')).toBe(true)
    expect(Array.from(summary).length).toBeLessThanOrEqual(24)
  })

  it('lets callers format special values such as Copilot asset names', () => {
    const copilot = typeByName('Copilot')
    expect(summarizeTaskParams(copilot, { asset_id: 'copilot-1' }, { formatField: (field) => (field === 'asset_id' ? '主线 1-7' : undefined) })).toBe('主线 1-7')
    expect(summarizeTaskParams(copilot, { asset_id: 'copilot-1', formation: true })).toBe('copilot-1 · 重新编队 开')
  })

  it('still reports parameters that are not in the schema', () => {
    expect(summarizeTaskParams(typeByName('StartUp'), { legacy_flag: true })).toBe('legacy_flag 开')
  })
})
