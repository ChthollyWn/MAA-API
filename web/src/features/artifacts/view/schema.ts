import { parseChartCard, type ChartCardData } from '@/features/agent/media'

/**
 * 生成式视图 v1 的前端解析（M90 §4.5.4）。
 *
 * 只认服务端白名单里的七种组件与四种字段；`parseView` 永不抛异常，坏组件丢弃并
 * 计数，最差的结果是视图少几块，而不是白屏。上限与 `maa_api/agent/artifacts/views.py`
 * 保持一致，不因为服务端已经校验过就放宽。
 */

export const VIEW_MIME = 'application/vnd.maa.view+json'

export const VIEW_LIMITS = {
  components: 40,
  fields: 20,
  text: 2000,
  shortText: 80,
  label: 40,
  stats: 6,
  columns: 12,
  rows: 200,
  cell: 200,
  options: 20,
  optionValue: 64,
  textField: 500,
} as const

export interface ViewStat {
  label: string
  value: number | string
  unit: string | null
  delta: number | null
  hint: string | null
}

export interface ViewColumn {
  key: string
  label: string
  align: 'left' | 'right'
  format: 'text' | 'number' | 'percent'
}

export type ViewCell = string | number | boolean | null

export interface SelectOptionSpec {
  value: string
  label: string
  description: string | null
}

interface FieldBase {
  name: string
  label: string
  description: string | null
  required: boolean
}

export type ViewField =
  | (FieldBase & { field: 'select'; options: SelectOptionSpec[]; multiple: boolean; default: string | string[] | null })
  | (FieldBase & { field: 'number'; min: number | null; max: number | null; step: number | null; unit: string | null; default: number | null })
  | (FieldBase & { field: 'text'; maxLength: number; multiline: boolean; placeholder: string | null; default: string | null })
  | (FieldBase & { field: 'toggle'; default: boolean })

export type ViewComponent =
  | { type: 'heading'; text: string; level: 2 | 3 }
  | { type: 'text'; text: string; tone: 'default' | 'muted' | 'warning' }
  | { type: 'stats'; items: ViewStat[] }
  | { type: 'table'; title: string | null; columns: ViewColumn[]; rows: Array<Record<string, ViewCell>>; caption: string | null }
  | { type: 'chart'; card: ChartCardData }
  | { type: 'field'; spec: ViewField }
  | { type: 'submit'; label: string }

export interface ParsedView {
  title: string
  description: string | null
  components: ViewComponent[]
  fields: ViewField[]
  dropped: number
}

export type FormValue = string | string[] | number | boolean | null
export type FormValues = Record<string, FormValue>

const NAME_PATTERN = /^[a-z][a-z0-9_]{0,31}$/
const DESCRIPTION_LIMIT = 120
const CAPTION_LIMIT = 300
const UNIT_LIMIT = 16
const STAT_VALUE_LIMIT = 24
const HINT_LIMIT = 80
const PLACEHOLDER_LIMIT = 60
const FIELD_DESCRIPTION_LIMIT = 120

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 去首尾空白后落在 [min, max] 的字符串；0 长度按不合法处理。 */
function trimmed(value: unknown, max: number, min = 1): string | null {
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (text.length < min || text.length > max) return null
  return text
}

function nullableText(value: unknown, max: number): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string') return null
  const text = value.trim()
  return text.length > max ? null : text
}

function nullableFinite(value: unknown): number | null {
  if (value === null || value === undefined) return null
  return finite(value)
}

function booleanOr(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function parseStat(raw: unknown): ViewStat | null {
  if (!isRecord(raw)) return null
  const label = trimmed(raw.label, VIEW_LIMITS.label)
  if (!label) return null
  const value = typeof raw.value === 'number' ? finite(raw.value) : trimmed(raw.value, STAT_VALUE_LIMIT)
  if (value === null) return null
  return {
    label,
    value,
    unit: nullableText(raw.unit, UNIT_LIMIT),
    delta: nullableFinite(raw.delta),
    hint: nullableText(raw.hint, HINT_LIMIT),
  }
}

function parseColumn(raw: unknown): ViewColumn | null {
  if (!isRecord(raw)) return null
  const key = trimmed(raw.key, 32)
  if (!key || !NAME_PATTERN.test(key)) return null
  const label = trimmed(raw.label, VIEW_LIMITS.label)
  if (!label) return null
  const align = raw.align === 'right' ? 'right' : 'left'
  const format = raw.format === 'number' || raw.format === 'percent' ? raw.format : 'text'
  return { key, label, align, format }
}

function parseCell(raw: unknown): ViewCell | null | undefined {
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'number') return finite(raw)
  if (typeof raw === 'string') return trimmed(raw, VIEW_LIMITS.cell, 0)
  return undefined
}

function parseTableColumnList(raw: unknown): ViewColumn[] | null {
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > VIEW_LIMITS.columns) return null
  const columns: ViewColumn[] = []
  for (const item of raw) {
    const column = parseColumn(item)
    if (!column) return null
    columns.push(column)
  }
  return columns
}

function parseRows(raw: unknown, columns: readonly ViewColumn[]): Array<Record<string, ViewCell>> | null {
  if (!Array.isArray(raw) || raw.length > VIEW_LIMITS.rows) return null
  const declared = new Set(columns.map((column) => column.key))
  const rows: Array<Record<string, ViewCell>> = []
  for (const item of raw) {
    if (!isRecord(item)) return null
    if (Object.keys(item).some((key) => !declared.has(key))) return null
    const row: Record<string, ViewCell> = {}
    for (const column of columns) {
      const cell = parseCell(item[column.key])
      if (cell === undefined) return null
      row[column.key] = cell
    }
    rows.push(row)
  }
  return rows
}

function parseOption(raw: unknown): SelectOptionSpec | null {
  if (!isRecord(raw)) return null
  const value = trimmed(raw.value, VIEW_LIMITS.optionValue)
  const label = trimmed(raw.label, VIEW_LIMITS.label)
  if (!value || !label) return null
  return { value, label, description: nullableText(raw.description, DESCRIPTION_LIMIT) }
}

function fieldBase(raw: Record<string, unknown>, requiredFallback: boolean): FieldBase | null {
  const name = trimmed(raw.name, 32)
  if (!name || !NAME_PATTERN.test(name)) return null
  const label = trimmed(raw.label, VIEW_LIMITS.label)
  if (!label) return null
  return {
    name,
    label,
    description: nullableText(raw.description, FIELD_DESCRIPTION_LIMIT),
    required: booleanOr(raw.required, requiredFallback),
  }
}

function parseSelectField(raw: Record<string, unknown>): ViewField | null {
  const base = fieldBase(raw, true)
  if (!base) return null
  if (!Array.isArray(raw.options) || raw.options.length === 0 || raw.options.length > VIEW_LIMITS.options) return null
  const options: SelectOptionSpec[] = []
  for (const item of raw.options) {
    const option = parseOption(item)
    if (!option) return null
    options.push(option)
  }
  if (new Set(options.map((option) => option.value)).size !== options.length) return null
  const multiple = booleanOr(raw.multiple, false)
  const values = new Set(options.map((option) => option.value))
  let fallback: string | string[] | null = null
  if (multiple) {
    if (Array.isArray(raw.default) && raw.default.every((item) => typeof item === 'string' && values.has(item))) {
      fallback = [...raw.default] as string[]
    }
  } else if (typeof raw.default === 'string' && values.has(raw.default)) {
    fallback = raw.default
  }
  return { ...base, field: 'select', options, multiple, default: fallback }
}

function parseNumberField(raw: Record<string, unknown>): ViewField | null {
  const base = fieldBase(raw, true)
  if (!base) return null
  const step = nullableFinite(raw.step)
  if (step !== null && step <= 0) return null
  return {
    ...base,
    field: 'number',
    min: nullableFinite(raw.min),
    max: nullableFinite(raw.max),
    step,
    unit: nullableText(raw.unit, UNIT_LIMIT),
    default: nullableFinite(raw.default),
  }
}

function parseTextField(raw: Record<string, unknown>): ViewField | null {
  const base = fieldBase(raw, true)
  if (!base) return null
  const maxLength = typeof raw.max_length === 'number' && Number.isInteger(raw.max_length)
    && raw.max_length >= 1 && raw.max_length <= VIEW_LIMITS.textField
    ? raw.max_length
    : 200
  const fallback = nullableText(raw.default, VIEW_LIMITS.textField)
  return {
    ...base,
    field: 'text',
    maxLength,
    multiline: booleanOr(raw.multiline, false),
    placeholder: nullableText(raw.placeholder, PLACEHOLDER_LIMIT),
    default: fallback,
  }
}

function parseToggleField(raw: Record<string, unknown>): ViewField | null {
  const base = fieldBase(raw, false)
  if (!base) return null
  return { ...base, field: 'toggle', default: booleanOr(raw.default, false) }
}

function parseField(raw: Record<string, unknown>): ViewField | null {
  switch (raw.field) {
    case 'select':
      return parseSelectField(raw)
    case 'number':
      return parseNumberField(raw)
    case 'text':
      return parseTextField(raw)
    case 'toggle':
      return parseToggleField(raw)
    default:
      return null
  }
}

function parseComponent(raw: unknown): ViewComponent | null {
  if (!isRecord(raw)) return null
  switch (raw.type) {
    case 'heading': {
      const text = trimmed(raw.text, VIEW_LIMITS.shortText)
      if (!text) return null
      return { type: 'heading', text, level: raw.level === 3 ? 3 : 2 }
    }
    case 'text': {
      const text = trimmed(raw.text, VIEW_LIMITS.text)
      if (!text) return null
      const tone = raw.tone === 'muted' || raw.tone === 'warning' ? raw.tone : 'default'
      return { type: 'text', text, tone }
    }
    case 'stats': {
      if (!Array.isArray(raw.items) || raw.items.length === 0 || raw.items.length > VIEW_LIMITS.stats) return null
      const items: ViewStat[] = []
      for (const item of raw.items) {
        const stat = parseStat(item)
        if (!stat) return null
        items.push(stat)
      }
      return { type: 'stats', items }
    }
    case 'table': {
      const columns = parseTableColumnList(raw.columns)
      if (!columns) return null
      const rows = parseRows(raw.rows, columns)
      if (!rows) return null
      return {
        type: 'table',
        title: nullableText(raw.title, VIEW_LIMITS.shortText),
        columns,
        rows,
        caption: nullableText(raw.caption, CAPTION_LIMIT),
      }
    }
    case 'chart': {
      const card = parseChartCard({ chart: raw.chart, caption: raw.caption ?? '' })
      return card ? { type: 'chart', card } : null
    }
    case 'field': {
      const spec = parseField(raw)
      return spec ? { type: 'field', spec } : null
    }
    case 'submit': {
      const label = trimmed(raw.label, 12) ?? '提交'
      return { type: 'submit', label }
    }
    default:
      return null
  }
}

/** 解析视图正文；永不抛异常，顶层形状不对时返回 `null`。 */
export function parseView(raw: unknown): ParsedView | null {
  if (!isRecord(raw)) return null
  if (raw.version !== 1) return null
  const title = trimmed(raw.title, VIEW_LIMITS.shortText)
  if (!title) return null
  if (!Array.isArray(raw.components)) return null

  const components: ViewComponent[] = []
  const fields: ViewField[] = []
  let dropped = 0
  for (const [index, item] of raw.components.entries()) {
    if (index >= VIEW_LIMITS.components) {
      dropped += 1
      continue
    }
    const component = parseComponent(item)
    if (!component) {
      dropped += 1
      continue
    }
    if (component.type === 'field') {
      if (fields.length >= VIEW_LIMITS.fields) {
        dropped += 1
        continue
      }
      fields.push(component.spec)
    }
    components.push(component)
  }

  return {
    title,
    description: nullableText(raw.description, CAPTION_LIMIT),
    components,
    fields,
    dropped,
  }
}

function emptyValue(field: ViewField): FormValue {
  if (field.field === 'select') return field.multiple ? [] : ''
  if (field.field === 'toggle') return false
  return ''
}

function submittedValue(field: ViewField, raw: unknown): FormValue {
  if (raw === null || raw === undefined) return emptyValue(field)
  if (field.field === 'select') {
    if (field.multiple) {
      return Array.isArray(raw) && raw.every((item) => typeof item === 'string') ? [...raw] as string[] : []
    }
    return typeof raw === 'string' ? raw : ''
  }
  if (field.field === 'number') {
    const number = finite(raw)
    return number === null ? '' : String(number)
  }
  if (field.field === 'text') return typeof raw === 'string' ? raw : ''
  return typeof raw === 'boolean' ? raw : false
}

function defaultValue(field: ViewField): FormValue {
  if (field.field === 'select') {
    if (field.multiple) return field.default ? [...field.default] : []
    return field.default ?? ''
  }
  if (field.field === 'number') return field.default === null ? '' : String(field.default)
  if (field.field === 'text') return field.default ?? ''
  return field.default
}

/**
 * 表单初值：给了 `submitted` 就以上次提交为准（缺失字段按空值），否则取字段默认值。
 * 数字统一转成字符串，和 `<input type="number">` 的受控值保持一致。
 */
export function initialValues(
  fields: readonly ViewField[],
  submitted?: Record<string, unknown>,
): FormValues {
  const values: FormValues = {}
  const fromSubmission = submitted !== undefined
  for (const field of fields) {
    const raw = fromSubmission && field.name in submitted ? submitted[field.name] : undefined
    values[field.name] = fromSubmission ? submittedValue(field, raw) : defaultValue(field)
  }
  return values
}

/** 与后端 `validate_submission` 同一组原因文案；失败返回 `{字段名: 原因}`。 */
export function validateValues(
  fields: readonly ViewField[],
  values: FormValues,
): Record<string, string> {
  const errors: Record<string, string> = {}
  const names = new Set(fields.map((field) => field.name))

  for (const field of fields) {
    const raw = values[field.name]
    const present = raw !== undefined && raw !== null
    if (!present) {
      if (field.required) errors[field.name] = '必填'
      continue
    }

    if (field.field === 'select') {
      const optionValues = new Set(field.options.map((option) => option.value))
      if (field.multiple) {
        if (!Array.isArray(raw) || !raw.every((item) => typeof item === 'string')) {
          errors[field.name] = '选项不合法'
        } else if (raw.length === 0) {
          if (field.required) errors[field.name] = '必填'
        } else if (raw.some((item) => !optionValues.has(item))) {
          errors[field.name] = '不在可选项里'
        } else if (new Set(raw).size !== raw.length) {
          errors[field.name] = '不能重复选择'
        }
      } else if (typeof raw !== 'string') {
        errors[field.name] = '选项不合法'
      } else if (raw === '') {
        if (field.required) errors[field.name] = '必填'
      } else if (!optionValues.has(raw)) {
        errors[field.name] = '不在可选项里'
      }
      continue
    }

    if (field.field === 'number') {
      if (raw === '' || (typeof raw === 'string' && raw.trim() === '')) {
        if (field.required) errors[field.name] = '必填'
        continue
      }
      let number: number | null = null
      if (typeof raw === 'number') number = finite(raw)
      else if (typeof raw === 'string') number = finite(Number(raw.trim()))
      if (number === null) {
        errors[field.name] = '需要填数字'
      } else if (field.min !== null && number < field.min) {
        errors[field.name] = `不能小于 ${String(field.min)}`
      } else if (field.max !== null && number > field.max) {
        errors[field.name] = `不能大于 ${String(field.max)}`
      }
      continue
    }

    if (field.field === 'text') {
      if (typeof raw !== 'string') {
        errors[field.name] = '需要是文本'
        continue
      }
      const text = raw.trim()
      if (field.required && text === '') errors[field.name] = '必填'
      else if (text.length > field.maxLength) errors[field.name] = `不能超过 ${field.maxLength} 字`
      continue
    }

    if (typeof raw !== 'boolean') errors[field.name] = '需要是开或关'
  }

  for (const key of Object.keys(values)) {
    if (!names.has(key)) errors[key] = '表单里没有这一项'
  }
  return errors
}

/**
 * 提交前规整：数字框的字符串转数字，空的选填项省略；开关总带上。
 * 只输出视图里声明过的字段，客户端多出来的键不发出去。
 */
export function toSubmitPayload(
  fields: readonly ViewField[],
  values: FormValues,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const field of fields) {
    const raw = values[field.name]
    if (field.field === 'select') {
      if (field.multiple) {
        if (Array.isArray(raw) && raw.length > 0) payload[field.name] = [...raw]
      } else if (typeof raw === 'string' && raw !== '') {
        payload[field.name] = raw
      }
      continue
    }
    if (field.field === 'number') {
      if (typeof raw === 'number' && Number.isFinite(raw)) payload[field.name] = raw
      else if (typeof raw === 'string' && raw.trim() !== '') {
        const number = Number(raw.trim())
        if (Number.isFinite(number)) payload[field.name] = number
      }
      continue
    }
    if (field.field === 'text') {
      if (typeof raw === 'string' && raw.trim() !== '') payload[field.name] = raw.trim()
      continue
    }
    if (typeof raw === 'boolean') payload[field.name] = raw
  }
  return payload
}
