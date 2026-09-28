import { describe, expect, it } from 'vitest'
import taskTypesFixture from '../task-types.fixture.json'
import { schemaProperties, serializeTask, type TaskTypeSchema } from '../form'
import { createDraft, draftsFromTemplate, duplicateDraft, materializeDrafts, templateFromDrafts } from './drafts'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const typeByName = (name: string) => {
  const found = types.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}
const facility = ['Mfg', 'Trade', 'Power', 'Control', 'Reception', 'Office', 'Dorm']

describe('createDraft', () => {
  it('prefills the schema defaults for a new task (新建)', () => {
    const draft = createDraft(typeByName('Fight'))
    expect(draft.name).toBe('Fight')
    expect(draft.params).toEqual({
      enable: true,
      series: 1,
      medicine: 0,
      expiring_medicine: 0,
      stone: 0,
      DrGrandet: false,
      server: 'CN',
      client_type: 'Bilibili',
      report_to_penguin: false,
      drops: {},
    })
    // 没有默认值的字段一个都不碰；判别字段 name 不进参数。
    for (const name of ['stage', 'times', 'penguin_id', 'name']) expect(name in draft.params).toBe(false)
  })

  it('keeps the params the caller passed and fills the rest', () => {
    const draft = createDraft(typeByName('StartUp'), { client_type: 'Official', account_name: 'doctor' })
    expect(draft.params).toEqual({ client_type: 'Official', account_name: 'doctor', enable: true, start_game_enabled: false })
    expect(createDraft(typeByName('Infrast'), { facility: ['Mfg'] }).params).toMatchObject({ facility: ['Mfg'], mode: 0, threshold: 0.3 })
  })

  it('copies array and object defaults instead of sharing them with the schema', () => {
    const draft = createDraft(typeByName('Infrast'))
    expect(draft.params.facility).toEqual(facility)
    ;(draft.params.facility as string[]).push('Trade')
    expect(createDraft(typeByName('Infrast')).params.facility).toEqual(facility)
    expect(schemaProperties(typeByName('Infrast').schema).facility.default).toEqual(facility)
  })
})

describe('duplicateDraft', () => {
  it('materializes a draft that predates the defaults (复制)', () => {
    const copy = duplicateDraft({ id: 'a', name: 'Fight', params: { stage: '1-7' } }, typeByName('Fight'))
    expect(copy.id).not.toBe('a')
    expect(copy.params).toMatchObject({ stage: '1-7', enable: true, series: 1, medicine: 0, server: 'CN', client_type: 'Bilibili' })
    expect(copy.params.drops).toEqual({})
  })

  it('deep-copies the params so the copy is independent', () => {
    const source = { id: 'a', name: 'Fight', params: { drops: { '30013': 5 } } }
    const copy = duplicateDraft(source, typeByName('Fight'))
    ;(copy.params.drops as Record<string, number>)['30013'] = 9
    expect(source.params.drops).toEqual({ '30013': 5 })
  })

  it('keeps a draft whose type the catalog does not know', () => {
    const copy = duplicateDraft({ id: 'a', name: 'Nope', params: { x: 1 } }, undefined)
    expect(copy.id).not.toBe('a')
    expect(copy).toMatchObject({ name: 'Nope', params: { x: 1 } })
  })
})

describe('draftsFromTemplate', () => {
  it('materializes the defaults when loading a saved template (从模板加载)', () => {
    const [draft] = draftsFromTemplate([{ name: 'Infrast', mode: 10000 }], types)
    expect(draft.name).toBe('Infrast')
    expect(draft.params).toMatchObject({ mode: 10000, enable: true, facility, threshold: 0.3 })
  })

  it('keeps tasks whose type the server no longer offers and drops entries without a name', () => {
    const drafts = draftsFromTemplate([{ name: 'Nope', x: 1 }, { bad: true }, 'nope'], types)
    expect(drafts).toHaveLength(1)
    expect(drafts[0]).toMatchObject({ name: 'Nope', params: { x: 1 } })
  })

  it('sends the prefilled defaults with the submission body', () => {
    const template = templateFromDrafts(draftsFromTemplate([{ name: 'StartUp' }], types))
    expect(template).toEqual([{ name: 'StartUp', enable: true, client_type: 'Bilibili', start_game_enabled: false }])
    expect(serializeTask(draftsFromTemplate([{ name: 'Fight', stage: '1-7', medicine: 2 }], types)[0])).toMatchObject({
      name: 'Fight',
      stage: '1-7',
      medicine: 2,
      series: 1,
      server: 'CN',
    })
  })
})

describe('materializeDrafts', () => {
  it('fills the defaults of an old draft and leaves materialized ones untouched', () => {
    const old = { id: 'a', name: 'Fight', params: { stage: '1-7' } }
    const [filled] = materializeDrafts([old], types)
    expect(filled).not.toBe(old)
    expect(filled.params).toMatchObject({ stage: '1-7', medicine: 0, series: 1 })

    const ready = createDraft(typeByName('Fight'))
    expect(materializeDrafts([ready], types)[0]).toBe(ready)
    const unknown = { id: 'b', name: 'Nope', params: {} }
    expect(materializeDrafts([unknown], types)[0]).toBe(unknown)
  })
})
