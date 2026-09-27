import { describe, expect, it } from 'vitest'
import taskTypesFixture from '../task-types.fixture.json'
import {
  arrayItemOptions,
  dependencyHint,
  dependencyState,
  enumLabels,
  enumOptions,
  fieldLabel,
  getType,
  groupTaskFields,
  isRuntimeImmutable,
  normalizeSchema,
  requiredFields,
  resolveReference,
  resolveWidget,
  riskKind,
  riskLabel,
  scalarEnumValue,
  schemaDefault,
  schemaProperties,
  taskFields,
  taskTypeSummary,
  type JsonSchema,
  type TaskTypeSchema,
} from './schema'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const typeByName = (name: string) => {
  const found = types.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}
const property = (typeName: string, field: string) => schemaProperties(typeByName(typeName).schema)[field]
const widgetOf = (typeName: string, field: string) => resolveWidget(property(typeName, field), typeByName(typeName).schema, [typeName, field])

function syntheticType(properties: Record<string, JsonSchema>, groups: string[] = []): TaskTypeSchema {
  return { name: 'Synthetic', label: '合成', description: '', schema: { type: 'object', properties }, groups, runtime_immutable: [] }
}

describe('normalizeSchema', () => {
  it('strips null from anyOf, keeps property-level keywords and marks the field nullable', () => {
    const result = normalizeSchema(property('Fight', 'medicine'), typeByName('Fight').schema)
    expect(result.nullable).toBe(true)
    expect(result.complex).toBe(false)
    expect(result.schema.type).toBe('integer')
    expect(result.schema.minimum).toBe(0)
    expect(result.schema['x-label']).toBe('最大理智药数')
    expect(result.schema['x-risk']).toBe('consume')
    expect(result.schema.anyOf).toBeUndefined()
  })

  it('keeps required non-null fields non-nullable', () => {
    const result = normalizeSchema(property('Copilot', 'asset_id'), typeByName('Copilot').schema)
    expect(result).toMatchObject({ nullable: false, complex: false })
    expect(result.schema.type).toBe('string')
  })

  it('recognises oneOf, const null, enum [null], type arrays and the nullable keyword', () => {
    const root: JsonSchema = {}
    expect(normalizeSchema({ oneOf: [{ type: 'string' }, { const: null }] }, root)).toMatchObject({ nullable: true, complex: false, schema: { type: 'string' } })
    expect(normalizeSchema({ anyOf: [{ enum: [null] }, { type: 'boolean' }] }, root)).toMatchObject({ nullable: true, complex: false, schema: { type: 'boolean' } })
    expect(normalizeSchema({ type: ['integer', 'null'] }, root)).toMatchObject({ nullable: true, complex: false, schema: { type: 'integer' } })
    expect(normalizeSchema({ type: 'string', nullable: true }, root)).toMatchObject({ nullable: true, complex: false })
    expect(normalizeSchema({ type: 'string', nullable: true }, root).schema.nullable).toBeUndefined()
  })

  it('treats several non-null branches as a complex structure', () => {
    const root: JsonSchema = {}
    expect(normalizeSchema({ anyOf: [{ type: 'string' }, { type: 'integer' }, { type: 'null' }] }, root)).toMatchObject({ nullable: true, complex: true })
    expect(normalizeSchema({ type: ['string', 'integer'] }, root)).toMatchObject({ complex: true })
    expect(normalizeSchema({ type: ['null'] }, root)).toMatchObject({ nullable: true, complex: true })
    expect(normalizeSchema({ anyOf: ['not-a-schema'] }, root)).toMatchObject({ complex: true })
  })

  it('resolves $ref at the top level and inside union branches', () => {
    const root: JsonSchema = {
      $defs: {
        Stage: { type: 'string', title: 'Stage' },
        'a/b': { type: 'integer', minimum: 1 },
      },
    }
    const top = normalizeSchema({ $ref: '#/$defs/Stage', 'x-label': '关卡' }, root)
    expect(top.schema).toMatchObject({ type: 'string', title: 'Stage', 'x-label': '关卡' })
    const branch = normalizeSchema({ anyOf: [{ $ref: '#/$defs/a~1b' }, { type: 'null' }], 'x-label': '次数' }, root)
    expect(branch).toMatchObject({ nullable: true, complex: false, schema: { type: 'integer', minimum: 1, 'x-label': '次数' } })
    expect(resolveReference({ $ref: '#/$defs/missing' }, root)).toEqual({ $ref: '#/$defs/missing' })
    expect(resolveReference({ $ref: 'https://example.com/schema' }, root)).toEqual({ $ref: 'https://example.com/schema' })
  })

  it('gives up on self-referencing schemas instead of recursing forever', () => {
    const root: JsonSchema = { $defs: { Loop: { anyOf: [{ $ref: '#/$defs/Loop' }, { type: 'null' }] } } }
    expect(normalizeSchema({ $ref: '#/$defs/Loop' }, root).complex).toBe(true)
  })
})

describe('schema helpers', () => {
  it('infers the type from type, properties, additionalProperties and items', () => {
    expect(getType({ type: 'string' })).toBe('string')
    expect(getType({ properties: {} })).toBe('object')
    expect(getType({ additionalProperties: { type: 'integer' } })).toBe('object')
    expect(getType({ items: {} })).toBe('array')
    expect(getType({})).toBeUndefined()
  })

  it('reads labels from x-label, then title, then the key', () => {
    expect(fieldLabel({ 'x-label': '关卡名', title: 'Stage' }, 'stage')).toBe('关卡名')
    expect(fieldLabel({ title: 'Stage' }, 'stage')).toBe('Stage')
    expect(fieldLabel({}, 'stage')).toBe('stage')
  })

  it('parses serialized enum values and keeps plain strings', () => {
    expect(scalarEnumValue('1')).toBe(1)
    expect(scalarEnumValue('"CN"')).toBe('CN')
    expect(scalarEnumValue('CN')).toBe('CN')
  })

  it('lists enum options with x-enum-labels', () => {
    const server = normalizeSchema(property('Fight', 'server'), typeByName('Fight').schema).schema
    expect(enumLabels(server)).toMatchObject({ CN: '国服' })
    expect(enumOptions(server)).toEqual([
      { key: '"CN"', value: 'CN', label: '国服' },
      { key: '"US"', value: 'US', label: '美服' },
      { key: '"JP"', value: 'JP', label: '日服' },
      { key: '"KR"', value: 'KR', label: '韩服' },
    ])
    const infrastMode = normalizeSchema(property('Infrast', 'mode'), typeByName('Infrast').schema).schema
    expect(enumOptions(infrastMode).map((option) => option.value)).toEqual([0, 10000, 20000])
    expect(enumOptions({ type: 'string' })).toEqual([])
    expect(enumLabels({ type: 'string' })).toEqual({})
  })

  it('derives select options from x-enum-labels when the schema has no enum (Fight.series)', () => {
    const series = normalizeSchema(property('Fight', 'series'), typeByName('Fight').schema).schema
    const options = enumOptions(series)
    expect(options.map((option) => option.value)).toEqual([-1, 0, 1, 2, 3, 4, 5, 6])
    expect(options[0]).toEqual({ key: '-1', value: -1, label: '禁用切换' })
    expect(options[1].label).toBe('自动选择最大可用次数')
  })

  it('lists array item options from enums and small integer ranges', () => {
    expect(arrayItemOptions(normalizeSchema(property('Infrast', 'facility'), {}).schema, {}).map((option) => option.value)).toEqual(['Mfg', 'Trade', 'Power', 'Control', 'Reception', 'Office', 'Dorm'])
    expect(arrayItemOptions(normalizeSchema(property('Recruit', 'select'), {}).schema, {})).toEqual([1, 2, 3, 4, 5, 6].map((value) => ({ key: String(value), value, label: String(value) })))
    expect(arrayItemOptions(normalizeSchema(property('Mall', 'buy_first'), {}).schema, {})).toEqual([])
    expect(arrayItemOptions({ type: 'array', items: { type: 'integer', minimum: 0, maximum: 100 } }, {})).toEqual([])
  })

  it('evaluates x-depends-on strictly against the draft values', () => {
    const penguinId = property('Fight', 'penguin_id')
    expect(dependencyState(penguinId, {})).toEqual({ satisfied: false, unmet: [{ field: 'report_to_penguin', expected: true }] })
    expect(dependencyState(penguinId, { report_to_penguin: 'true' }).satisfied).toBe(false)
    expect(dependencyState(penguinId, { report_to_penguin: true })).toEqual({ satisfied: true, unmet: [] })
    expect(dependencyState(property('Fight', 'stage'), {})).toEqual({ satisfied: true, unmet: [] })
    const eliteTwo = property('Roguelike', 'only_start_with_elite_two')
    expect(dependencyState(eliteTwo, { mode: 4 }).unmet).toEqual([{ field: 'start_with_elite_two', expected: true }])
  })

  it('explains which fields a disabled control depends on', () => {
    const fight = schemaProperties(typeByName('Fight').schema)
    const roguelike = schemaProperties(typeByName('Roguelike').schema)
    const recruit = schemaProperties(typeByName('Recruit').schema)
    expect(dependencyHint(dependencyState(fight.penguin_id, {}).unmet, fight, {})).toBe('需先开启「汇报企鹅数据」')
    expect(dependencyHint(dependencyState(roguelike.first_floor_foldartal, {}).unmet, roguelike, {})).toBe('需先将「主题」设为「探索者的银凇止境」')
    expect(dependencyHint(dependencyState(recruit.set_time, {}).unmet, recruit, {})).toBe('需先将「招募次数」设为 0')
    expect(dependencyHint(dependencyState(roguelike.only_start_with_elite_two, {}).unmet, roguelike, {})).toBe('需先将「模式」设为「凹开局」，并开启「凹干员精二直升」')
    expect(dependencyHint([{ field: 'flag', expected: false }], {}, {})).toBe('需先关闭「flag」')
  })

  it('reports x-risk, runtime_immutable, required and default values', () => {
    expect(riskKind(property('Fight', 'medicine'))).toBe('consume')
    expect(riskKind(property('Fight', 'stage'))).toBeNull()
    expect(riskLabel('consume')).toBe('消耗资源')
    expect(riskLabel('destructive')).toBe('有风险')
    expect(isRuntimeImmutable(typeByName('Fight'), 'stage')).toBe(true)
    expect(isRuntimeImmutable(typeByName('Fight'), 'medicine')).toBe(false)
    expect(requiredFields(typeByName('Copilot').schema)).toEqual(new Set(['asset_id']))
    expect(requiredFields(typeByName('Fight').schema)).toEqual(new Set())
    expect(schemaDefault(property('Copilot', 'formation'))).toEqual({ value: false })
    expect(schemaDefault(property('Fight', 'medicine'))).toBeUndefined()
    expect(schemaDefault({ type: 'integer' })).toBeUndefined()
  })

  it('uses the first paragraph of a task type description as its summary', () => {
    expect(taskTypeSummary(typeByName('Award'))).toBe('领取各种奖励。')
    expect(taskTypeSummary(typeByName('Roguelike'))).toBe('无限刷肉鸽（集成战略）。')
    expect(taskTypeSummary(typeByName('Fight'))).toBe('刷理智：刷指定关卡，支持理智药、碎石与连战。')
  })
})

describe('task field grouping', () => {
  it('lists editable fields in schema order without the name discriminator', () => {
    expect(taskFields(typeByName('Fight')).map(([name]) => name)).toEqual([
      'enable', 'stage', 'times', 'series', 'medicine', 'expiring_medicine', 'stone', 'DrGrandet', 'server', 'client_type', 'report_to_penguin', 'penguin_id', 'drops',
    ])
  })

  it('groups fields by groups and x-group', () => {
    const groups = groupTaskFields(typeByName('Fight'))
    expect(groups.map((group) => group.name)).toEqual(['基础', '资源消耗', '账号', '数据上报', '高级'])
    expect(groups[0].fields.map(([name]) => name)).toEqual(['enable', 'stage', 'times', 'series'])
    expect(groups[4].fields.map(([name]) => name)).toEqual(['drops'])
  })

  it('puts ungrouped fields into 基础, skips empty groups and keeps unknown groups visible', () => {
    expect(groupTaskFields(syntheticType({ a: { type: 'string' } })).map((group) => [group.name, group.fields.map(([name]) => name)])).toEqual([['基础', ['a']]])
    expect(groupTaskFields(syntheticType({
      loose: { type: 'string' },
      tuned: { type: 'string', 'x-group': '高级' },
      odd: { type: 'string', 'x-group': '其他' },
    }, ['高级', '空组'])).map((group) => [group.name, group.fields.map(([name]) => name)])).toEqual([
      ['高级', ['tuned']],
      ['基础', ['loose']],
      ['其他', ['odd']],
    ])
  })

  it('covers every fixture property except name', () => {
    for (const taskType of types) {
      const grouped = groupTaskFields(taskType).flatMap((group) => group.fields.map(([name]) => name))
      const expected = Object.keys(schemaProperties(taskType.schema)).filter((name) => name !== 'name')
      expect(grouped.sort()).toEqual(expected.sort())
    }
  })
})

describe('resolveWidget', () => {
  it('maps fixture fields onto one control each', () => {
    expect(widgetOf('Copilot', 'asset_id')).toBe('copilot-asset')
    expect(widgetOf('Copilot', 'formation')).toBe('switch')
    expect(widgetOf('Fight', 'enable')).toBe('switch')
    expect(widgetOf('Fight', 'drops')).toBe('item-count-map')
    expect(widgetOf('Recruit', 'recruitment_time')).toBe('duration-map')
    expect(widgetOf('Infrast', 'facility')).toBe('choice-ordered-list')
    expect(widgetOf('Recruit', 'select')).toBe('choice-tags')
    expect(widgetOf('Recruit', 'first_tags')).toBe('tags')
    expect(widgetOf('Fight', 'server')).toBe('select')
    expect(widgetOf('Fight', 'series')).toBe('select')
    expect(widgetOf('Recruit', 'extra_tags_mode')).toBe('select')
    expect(widgetOf('Infrast', 'mode')).toBe('select')
    expect(widgetOf('Reclamation', 'increment_mode')).toBe('segmented')
    expect(widgetOf('Reclamation', 'theme')).toBe('segmented')
    expect(widgetOf('Fight', 'medicine')).toBe('number')
    expect(widgetOf('Infrast', 'threshold')).toBe('number')
    expect(widgetOf('Fight', 'stage')).toBe('text')
  })

  it('falls back for structures without a dedicated control', () => {
    const resolve = (schema: JsonSchema) => resolveWidget(schema, {}, ['Synthetic', 'field'])
    expect(resolve({ anyOf: [{ type: 'string' }, { type: 'integer' }] })).toBe('json')
    expect(resolve({})).toBe('json')
    expect(resolve({ type: 'object', additionalProperties: { type: 'string' } })).toBe('key-value')
    expect(resolve({ type: 'object', properties: { a: { type: 'string' } } })).toBe('object')
    expect(resolve({ type: 'array', items: { type: 'string' } })).toBe('list')
    expect(resolve({ type: 'array', items: { type: 'string' }, 'x-widget': 'ordered-list' })).toBe('ordered-list')
    expect(resolve({ type: 'array', items: { enum: ['a', 'b'] }, 'x-widget': 'tags' })).toBe('choice-tags')
    expect(resolve({ type: 'string', maxLength: 500 })).toBe('textarea')
    expect(resolve({ type: 'string', 'x-widget': 'textarea' })).toBe('textarea')
    expect(resolve({ type: 'string', 'x-widget': 'select' })).toBe('text')
    expect(resolve({ type: 'string', 'x-widget': 'switch' })).toBe('switch')
    expect(resolve({ type: 'string', enum: ['a', 'b', 'c', 'd'] })).toBe('select')
  })
})
