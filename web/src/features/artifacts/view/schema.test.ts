import { describe, expect, it } from 'vitest'
import {
  VIEW_LIMITS,
  initialValues,
  parseView,
  toSubmitPayload,
  validateValues,
  type ViewField,
} from './schema'

function selectField(overrides: Partial<Extract<ViewField, { field: 'select' }>> = {}): Extract<ViewField, { field: 'select' }> {
  return {
    field: 'select',
    name: 'stage',
    label: '关卡',
    description: null,
    required: true,
    options: [{ value: '1-7', label: '1-7', description: null }],
    multiple: false,
    default: null,
    ...overrides,
  }
}

function numberField(overrides: Partial<Extract<ViewField, { field: 'number' }>> = {}): Extract<ViewField, { field: 'number' }> {
  return {
    field: 'number',
    name: 'count',
    label: '次数',
    description: null,
    required: false,
    min: null,
    max: null,
    step: null,
    unit: null,
    default: null,
    ...overrides,
  }
}

function textField(overrides: Partial<Extract<ViewField, { field: 'text' }>> = {}): Extract<ViewField, { field: 'text' }> {
  return {
    field: 'text',
    name: 'note',
    label: '备注',
    description: null,
    required: false,
    maxLength: 200,
    multiline: false,
    placeholder: null,
    default: null,
    ...overrides,
  }
}

function toggleField(overrides: Partial<Extract<ViewField, { field: 'toggle' }>> = {}): Extract<ViewField, { field: 'toggle' }> {
  return {
    field: 'toggle',
    name: 'potion',
    label: '可以用理智药',
    description: null,
    required: false,
    default: false,
    ...overrides,
  }
}

function selectComponent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'field',
    field: 'select',
    name: 'stage',
    label: '关卡',
    required: true,
    options: [{ value: '1-7', label: '1-7' }],
    multiple: false,
    ...overrides,
  }
}

function textComponent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'field',
    field: 'text',
    name: 'note',
    label: '备注',
    required: false,
    max_length: 20,
    ...overrides,
  }
}

function chartComponent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'chart',
    chart: {
      chart_type: 'bar',
      title: '掉落',
      categories: ['1-7'],
      series: [{ name: '理智', values: [1] }],
    },
    caption: '最近一次',
    ...overrides,
  }
}

describe('M90 视图解析', () => {
  it('parses every whitelisted component', () => {
    const view = parseView({
      version: 1,
      title: '这周刷哪一关',
      description: '选一个关卡',
      components: [
        { type: 'heading', text: '本周计划', level: 3 },
        { type: 'text', text: '先看掉落', tone: 'muted' },
        { type: 'stats', items: [{ label: '已刷次数', value: 12, unit: '次', delta: 3, hint: '比上周多' }] },
        {
          type: 'table',
          title: '掉落',
          columns: [
            { key: 'stage', label: '关卡' },
            { key: 'drops', label: '掉落', align: 'right', format: 'number' },
          ],
          rows: [{ stage: '1-7', drops: 3 }],
          caption: '本周数据',
        },
        chartComponent(),
        selectComponent(),
        textComponent(),
        { type: 'submit', label: '交作业' },
      ],
    })

    expect(view).not.toBeNull()
    expect(view!.title).toBe('这周刷哪一关')
    expect(view!.description).toBe('选一个关卡')
    expect(view!.components.map((component) => component.type)).toEqual([
      'heading', 'text', 'stats', 'table', 'chart', 'field', 'field', 'submit',
    ])
    expect(view!.components[0]).toEqual({ type: 'heading', text: '本周计划', level: 3 })
    expect(view!.components[1]).toEqual({ type: 'text', text: '先看掉落', tone: 'muted' })
    expect(view!.components[2]).toMatchObject({ type: 'stats' })
    expect(view!.components[3]).toMatchObject({
      type: 'table',
      title: '掉落',
      columns: [
        { key: 'stage', label: '关卡', align: 'left', format: 'text' },
        { key: 'drops', label: '掉落', align: 'right', format: 'number' },
      ],
      rows: [{ stage: '1-7', drops: 3 }],
      caption: '本周数据',
    })
    expect(view!.components[4]).toMatchObject({
      type: 'chart',
      card: { type: 'chart', chartType: 'bar', title: '掉落', caption: '最近一次' },
    })
    expect(view!.fields.map((field) => field.name)).toEqual(['stage', 'note'])
    expect(view!.dropped).toBe(0)
  })

  it('drops unknown or malformed components and counts them', () => {
    const view = parseView({
      version: 1,
      title: '有坏组件',
      components: [
        { type: 'html', text: '<b>x</b>' },
        { type: 'heading' },
        { type: 'stats', items: '不是数组' },
        { type: 'text', text: '保留' },
      ],
    })

    expect(view!.components).toEqual([{ type: 'text', text: '保留', tone: 'default' }])
    expect(view!.dropped).toBe(3)

    const overflow = parseView({
      version: 1,
      title: '很多组件',
      components: Array.from({ length: VIEW_LIMITS.components + 1 }, () => ({ type: 'text', text: '一段' })),
    })
    expect(overflow!.components).toHaveLength(VIEW_LIMITS.components)
    expect(overflow!.dropped).toBe(1)
  })

  it('returns null for an unsupported version or shape', () => {
    expect(parseView({ version: 2, title: '旧版本', components: [] })).toBeNull()
    expect(parseView({ version: 1, components: [] })).toBeNull()
    expect(parseView({ version: 1, title: '数组形状', components: {} })).toBeNull()
    expect(parseView(null)).toBeNull()
    expect(parseView('不是对象')).toBeNull()
  })

  it('builds initial values from defaults or the last submission', () => {
    const fields: ViewField[] = [
      selectField({ name: 'pick', default: '1-7' }),
      selectField({ name: 'pick_many', multiple: true, default: ['1-7'] }),
      numberField({ name: 'count', default: 5 }),
      textField({ name: 'note', default: '默认备注' }),
      toggleField({ name: 'potion', default: true }),
      selectField({ name: 'optional', required: false }),
      textField({ name: 'optional_text', required: false }),
    ]

    expect(initialValues(fields)).toEqual({
      pick: '1-7',
      pick_many: ['1-7'],
      count: '5',
      note: '默认备注',
      potion: true,
      optional: '',
      optional_text: '',
    })

    // 有提交记录时以提交值为准，缺失的字段按空值处理，不再回落到默认值。
    expect(initialValues(fields, {
      pick: '1-8',
      pick_many: ['1-8', '1-9'],
      count: 9,
      note: '上次的答案',
      potion: false,
    })).toEqual({
      pick: '1-8',
      pick_many: ['1-8', '1-9'],
      count: '9',
      note: '上次的答案',
      potion: false,
      optional: '',
      optional_text: '',
    })
  })

  it('validates values like the server', () => {
    const fields: ViewField[] = [
      selectField({ name: 'stage', options: [{ value: '1-7', label: '1-7', description: null }] }),
      selectField({ name: 'tags', multiple: true, required: false, options: [{ value: 'fast', label: '快速', description: null }] }),
      numberField({ name: 'count', min: 5, max: 10 }),
      textField({ name: 'note', maxLength: 5, required: true }),
      toggleField({ name: 'potion', required: true }),
    ]
    const valid = { stage: '1-7', tags: [] as string[], count: '', note: '好', potion: false }

    expect(validateValues(fields, { ...valid, stage: '' })).toEqual({ stage: '必填' })
    expect(validateValues(fields, { ...valid, stage: '1-8' })).toEqual({ stage: '不在可选项里' })
    expect(validateValues(fields, { ...valid, stage: 7 })).toEqual({ stage: '选项不合法' })
    expect(validateValues(fields, { ...valid, stage: '1-8', note: '' })).toEqual({ stage: '不在可选项里', note: '必填' })

    expect(validateValues(fields, { ...valid, tags: 'fast' })).toEqual({ tags: '选项不合法' })
    expect(validateValues(fields, { ...valid, tags: ['fast', 'fast'] })).toEqual({ tags: '不能重复选择' })
    expect(validateValues(fields, { ...valid, tags: ['slow'] })).toEqual({ tags: '不在可选项里' })
    expect(validateValues([
      selectField({ name: 'tag', multiple: true, required: true, options: [{ value: 'fast', label: '快速', description: null }] }),
    ], { tag: [] })).toEqual({ tag: '必填' })

    expect(validateValues(fields, { ...valid, count: 'abc' })).toEqual({ count: '需要填数字' })
    expect(validateValues(fields, { ...valid, count: '4' })).toEqual({ count: '不能小于 5' })
    expect(validateValues(fields, { ...valid, count: '11' })).toEqual({ count: '不能大于 10' })

    expect(validateValues(fields, { ...valid, note: 123 })).toEqual({ note: '需要是文本' })
    expect(validateValues(fields, { ...valid, note: '123456' })).toEqual({ note: '不能超过 5 字' })
    expect(validateValues(fields, { ...valid, note: '   ' })).toEqual({ note: '必填' })

    expect(validateValues(fields, { ...valid, potion: '是' })).toEqual({ potion: '需要是开或关' })
    expect(validateValues(fields, { ...valid, extra: '多出来' })).toEqual({ extra: '表单里没有这一项' })
    expect(validateValues(fields, valid)).toEqual({})
  })

  it('serializes numbers and omits empty optional fields', () => {
    const fields: ViewField[] = [
      numberField({ name: 'count', required: true }),
      numberField({ name: 'ratio' }),
      textField({ name: 'note' }),
      selectField({ name: 'pick', required: false }),
      selectField({ name: 'tags', multiple: true, required: false, options: [{ value: 'fast', label: '快速', description: null }] }),
      toggleField({ name: 'potion' }),
    ]

    expect(toSubmitPayload(fields, {
      count: '12.5',
      ratio: '3',
      note: '  写点什么  ',
      pick: '',
      tags: [],
      potion: false,
    })).toEqual({ count: 12.5, ratio: 3, note: '写点什么', potion: false })

    expect(toSubmitPayload(fields, {
      count: '3',
      ratio: '',
      note: '',
      pick: '',
      tags: ['fast'],
      potion: true,
    })).toEqual({ count: 3, tags: ['fast'], potion: true })
  })
})
