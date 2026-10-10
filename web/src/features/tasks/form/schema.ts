export type JsonSchema = Record<string, unknown>

export interface TaskTypeSchema {
  name: string
  label: string
  description: string
  schema: JsonSchema
  groups: string[]
  runtime_immutable: string[]
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function objectValue(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : {}
}

export function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

export function schemaProperties(schema: JsonSchema): Record<string, JsonSchema> {
  const properties = schema.properties
  if (!isObject(properties)) return {}
  return Object.fromEntries(Object.entries(properties).filter(([, child]) => isObject(child))) as Record<string, JsonSchema>
}

export function resolveReference(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (typeof schema.$ref !== 'string' || !schema.$ref.startsWith('#/')) return schema
  let current: unknown = root
  for (const segment of schema.$ref.slice(2).split('/').map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'))) {
    current = isObject(current) ? current[segment] : undefined
  }
  if (!isObject(current)) return schema
  return { ...current, ...schema, $ref: undefined }
}

export interface NormalizedSchema {
  schema: JsonSchema
  nullable: boolean
  complex: boolean
}

/** 解析 $ref，并把 anyOf / oneOf / type 数组里的 null 剥掉：剩下唯一分支时得到实际类型并标记可空，否则按复杂结构处理。 */
export function normalizeSchema(input: JsonSchema, root: JsonSchema, depth = 0): NormalizedSchema {
  if (depth > 12) return { schema: input, nullable: false, complex: true }
  const resolved = resolveReference(input, root)
  const schema = { ...resolved }
  let nullable = schema.nullable === true
  delete schema.nullable

  const rawType = schema.type
  if (Array.isArray(rawType)) {
    const types = rawType.filter((entry) => entry !== 'null')
    nullable ||= types.length !== rawType.length
    if (types.length === 1) schema.type = types[0]
    else if (types.length > 1) return { schema, nullable, complex: true }
    else return { schema, nullable: true, complex: true }
  }

  const unionKey = Array.isArray(schema.anyOf) ? 'anyOf' : Array.isArray(schema.oneOf) ? 'oneOf' : null
  if (unionKey) {
    const branches = schema[unionKey] as unknown[]
    const nonNull: JsonSchema[] = []
    for (const branch of branches) {
      if (!isObject(branch)) return { schema, nullable, complex: true }
      const resolvedBranch = resolveReference(branch, root)
      if (resolvedBranch.type === 'null' || resolvedBranch.const === null || (Array.isArray(resolvedBranch.enum) && resolvedBranch.enum.length === 1 && resolvedBranch.enum[0] === null)) {
        nullable = true
      } else nonNull.push(resolvedBranch)
    }
    delete schema[unionKey]
    if (nonNull.length !== 1) return { schema, nullable, complex: true }
    const branch = normalizeSchema(nonNull[0], root, depth + 1)
    return { schema: { ...schema, ...branch.schema }, nullable: nullable || branch.nullable, complex: branch.complex }
  }

  return { schema, nullable, complex: false }
}

export function getType(schema: JsonSchema): string | undefined {
  if (typeof schema.type === 'string') return schema.type
  if (schema.properties || schema.additionalProperties) return 'object'
  if (schema.items) return 'array'
  return undefined
}

export function fieldLabel(schema: JsonSchema, key: string): string {
  return textValue(schema['x-label'], textValue(schema.title, key))
}

export function scalarEnumValue(serialized: string): unknown {
  try {
    return JSON.parse(serialized) as unknown
  } catch {
    return serialized
  }
}

export function enumLabels(schema: JsonSchema): Record<string, string> {
  return isObject(schema['x-enum-labels']) ? schema['x-enum-labels'] as Record<string, string> : {}
}

export function fieldWidget(schema: JsonSchema): string {
  return textValue(schema['x-widget'])
}

export interface EnumOption {
  /** `JSON.stringify(value)`，可直接用作 `<option>` / 分段控件的值。 */
  key: string
  value: unknown
  label: string
}

/**
 * 枚举选项与中文标签。`x-widget: select` 但 schema 没给 `enum` 时（如 Fight.series 只有取值范围与
 * `x-enum-labels`），从标签的键推导选项，数值类型按大小排序。
 */
export function enumOptions(schema: JsonSchema): EnumOption[] {
  const labels = enumLabels(schema)
  if (Array.isArray(schema.enum)) {
    return schema.enum.map((value) => ({ key: JSON.stringify(value), value, label: labels[String(value)] ?? String(value) }))
  }
  if (fieldWidget(schema) !== 'select') return []
  const type = getType(schema)
  const numeric = type === 'integer' || type === 'number'
  const options = Object.entries(labels).flatMap(([raw, label]): EnumOption[] => {
    const value = numeric ? Number(raw) : raw
    if (typeof value === 'number' && !Number.isFinite(value)) return []
    return [{ key: JSON.stringify(value), value, label }]
  })
  return numeric ? options.sort((left, right) => (left.value as number) - (right.value as number)) : options
}

const MAX_RANGE_OPTIONS = 12

/** 数组元素的可选值：元素是枚举，或是范围不超过 12 个值的整数（例如公招 Tag 等级 1–6）。 */
export function arrayItemOptions(schema: JsonSchema, root: JsonSchema): EnumOption[] {
  const itemSchema = isObject(schema.items) ? normalizeSchema(schema.items as JsonSchema, root).schema : {}
  const options = enumOptions(itemSchema)
  if (options.length > 0) return options
  const { minimum, maximum } = itemSchema
  if (getType(itemSchema) !== 'integer' || typeof minimum !== 'number' || typeof maximum !== 'number') return []
  if (!Number.isInteger(minimum) || !Number.isInteger(maximum) || maximum < minimum || maximum - minimum >= MAX_RANGE_OPTIONS) return []
  const labels = enumLabels(itemSchema)
  return Array.from({ length: maximum - minimum + 1 }, (_, index) => {
    const value = minimum + index
    return { key: JSON.stringify(value), value, label: labels[String(value)] ?? String(value) }
  })
}

export type WidgetKind =
  | 'copilot-asset'
  | 'json'
  | 'item-count-map'
  | 'duration-map'
  | 'key-value'
  | 'object'
  | 'choice-tags'
  | 'choice-ordered-list'
  | 'tags'
  | 'ordered-list'
  | 'list'
  | 'segmented'
  | 'select'
  | 'switch'
  | 'number'
  | 'textarea'
  | 'text'

const SEGMENTED_MAX_OPTIONS = 3
const SEGMENTED_MAX_LABEL = 6

/**
 * 每个字段对应的唯一控件，判定顺序与旧 `ValueEditor` 的分支一致。
 * 选项不超过 3 个且每个标签不超过 6 个字的枚举用分段控件，否则用原生下拉（长标签在手机上排不下）。
 */
export function resolveWidget(input: JsonSchema, root: JsonSchema, path: readonly string[]): WidgetKind {
  const normalized = normalizeSchema(input, root)
  const schema = normalized.schema
  const widget = fieldWidget(schema)
  const type = getType(schema)
  if (path[0] === 'Copilot' && path.at(-1) === 'asset_id') return 'copilot-asset'
  if (normalized.complex || !type) return 'json'
  if (widget === 'item-count-map') return 'item-count-map'
  if (widget === 'duration-map') return 'duration-map'
  if (type === 'object') return schema.properties ? 'object' : 'key-value'
  if (type === 'array') {
    // 元素是对象的数组（如 Roguelike.core_char_list）没有专用控件：ListControl 按
    // `String(item)` 显示，渲染出来是 `[object Object]`，退回 JSON 文本框。
    const itemSchema = isObject(schema.items) ? normalizeSchema(schema.items as JsonSchema, root).schema : {}
    if (getType(itemSchema) === 'object') return 'json'
    const hasOptions = arrayItemOptions(schema, root).length > 0
    if (widget === 'tags') return hasOptions ? 'choice-tags' : 'tags'
    if (widget === 'ordered-list') return hasOptions ? 'choice-ordered-list' : 'ordered-list'
    return 'list'
  }
  const options = enumOptions(schema)
  if (options.length > 0) {
    const compact = options.length <= SEGMENTED_MAX_OPTIONS && options.every((option) => Array.from(option.label).length <= SEGMENTED_MAX_LABEL)
    return compact ? 'segmented' : 'select'
  }
  if (type === 'boolean' || widget === 'switch') return 'switch'
  if (type === 'integer' || type === 'number') return 'number'
  if (type === 'string') return widget === 'textarea' || (typeof schema.maxLength === 'number' && schema.maxLength > 120) ? 'textarea' : 'text'
  return 'json'
}

export interface DependencyState {
  satisfied: boolean
  unmet: Array<{ field: string; expected: unknown }>
}

/** `x-depends-on`：每个依赖字段在草稿里的值都与期望值严格相等（`Object.is`）才算满足；未满足时禁用而不隐藏。 */
export function dependencyState(schema: JsonSchema, formValues: Record<string, unknown>): DependencyState {
  const dependsOn = isObject(schema['x-depends-on']) ? schema['x-depends-on'] : null
  const unmet = Object.entries(dependsOn ?? {})
    .filter(([dependency, expected]) => !Object.is(formValues[dependency], expected))
    .map(([field, expected]) => ({ field, expected }))
  return { satisfied: unmet.length === 0, unmet }
}

/** 依赖说明，例如「需先开启「汇报企鹅数据」」。 */
export function dependencyHint(unmet: DependencyState['unmet'], siblings: Record<string, JsonSchema>, root: JsonSchema): string {
  const parts = unmet.map(({ field, expected }) => {
    const schema = siblings[field] ? normalizeSchema(siblings[field], root).schema : {}
    const label = fieldLabel(schema, field)
    if (expected === true) return `开启「${label}」`
    if (expected === false) return `关闭「${label}」`
    if (expected === null) return `将「${label}」留空`
    const option = enumOptions(schema).find((candidate) => Object.is(candidate.value, expected))
    if (option) return `将「${label}」设为「${option.label}」`
    return typeof expected === 'number' ? `将「${label}」设为 ${expected}` : `将「${label}」设为「${String(expected)}」`
  })
  return parts.length > 0 ? `需先${parts.join('，并')}` : ''
}

export function riskKind(schema: JsonSchema): string | null {
  const risk = schema['x-risk']
  if (risk === undefined) return null
  return typeof risk === 'string' ? risk : 'consume'
}

export function riskLabel(kind: string): string {
  return kind === 'consume' ? '消耗资源' : '有风险'
}

export function isRuntimeImmutable(taskType: TaskTypeSchema, name: string): boolean {
  return taskType.runtime_immutable.includes(name)
}

export function requiredFields(schema: JsonSchema): Set<string> {
  return new Set(Array.isArray(schema.required) ? schema.required.filter((entry): entry is string => typeof entry === 'string') : [])
}

/** schema 声明的默认值；`default: null` 表示交给内核决定，视为没有已知默认值。 */
export function schemaDefault(schema: JsonSchema): { value: unknown } | undefined {
  if (!('default' in schema) || schema.default === null || schema.default === undefined) return undefined
  return { value: schema.default }
}

/** 默认值的深拷贝：数组与映射不能与 schema（或查询缓存）共享引用。 */
function copyDefault(value: unknown): unknown {
  return isObject(value) || Array.isArray(value) ? structuredClone(value) : value
}

/**
 * M17 的预填：把「schema 有非 null `default` 且草稿里没有该键」的字段填成默认值，返回新对象。
 * 没有默认值的字段（`default` 缺失或为 null，见 docs/09 §7.5 的四类）一个都不碰；判别字段 `name`
 * 由草稿的 `name` 承载，不写进参数；嵌套对象只在草稿里已有该对象时递归。
 */
export function materializeDefaults(taskType: TaskTypeSchema, params: Record<string, unknown> = {}): Record<string, unknown> {
  return materializeObject(taskType.schema, taskType.schema, params)
}

function materializeObject(root: JsonSchema, schema: JsonSchema, params: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = { ...params }
  for (const [name, input] of Object.entries(schemaProperties(schema))) {
    if (name === 'name') continue
    const normalized = normalizeSchema(input, root).schema
    // 显式 undefined 与缺键等效（与 fieldState 一致）；显式 null 是「明确留空」，保留。
    if (result[name] === undefined) {
      const fallback = schemaDefault(normalized)
      if (fallback) result[name] = copyDefault(fallback.value)
      continue
    }
    const value = result[name]
    if (!isObject(value) || Object.keys(schemaProperties(normalized)).length === 0) continue
    result[name] = materializeObject(root, normalized, value)
  }
  return result
}

export type FieldEntry = [name: string, schema: JsonSchema]

/** 可编辑字段（schema 顺序），去掉由任务类型决定的判别字段 `name`。 */
export function taskFields(taskType: TaskTypeSchema): FieldEntry[] {
  return Object.entries(schemaProperties(taskType.schema)).filter(([name]) => name !== 'name')
}

export interface FieldGroup {
  name: string
  fields: FieldEntry[]
}

const DEFAULT_GROUP = '基础'

/**
 * 按任务类型的 `groups` 与字段的 `x-group` 分组；没有 `x-group` 的字段归入「基础」，空分组省略。
 * `x-group` 不在 `groups` 里的字段按出现顺序追加在最后，避免被静默隐藏。
 */
export function groupTaskFields(taskType: TaskTypeSchema): FieldGroup[] {
  const fields = taskFields(taskType)
  const groups = taskType.groups.length > 0 ? taskType.groups : [DEFAULT_GROUP]
  const ungrouped = fields.filter(([, schema]) => typeof schema['x-group'] !== 'string')
  const finalGroups = ungrouped.length > 0 && !groups.includes(DEFAULT_GROUP) ? [...groups, DEFAULT_GROUP] : [...groups]
  for (const [, schema] of fields) {
    const group = textValue(schema['x-group'], DEFAULT_GROUP)
    if (!finalGroups.includes(group)) finalGroups.push(group)
  }
  return finalGroups
    .map((group) => ({ name: group, fields: fields.filter(([, schema]) => textValue(schema['x-group'], DEFAULT_GROUP) === group) }))
    .filter((group) => group.fields.length > 0)
}

/** 任务类型说明的第一段（后端 docstring 之后的段落是给开发者看的）。 */
export function taskTypeSummary(taskType: TaskTypeSchema): string {
  return textValue(taskType.description).split(/\n\s*\n/)[0].trim()
}
