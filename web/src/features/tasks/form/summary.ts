import { enumOptions, fieldLabel, isObject, normalizeSchema, taskFields, type JsonSchema, type TaskTypeSchema } from './schema'

export const DEFAULT_PARAMS_SUMMARY = '使用默认参数'

export interface SummaryOptions {
  /** 最多展示几个字段，默认 3。 */
  maxFields?: number
  /** 摘要的字数上限，默认 24。 */
  maxLength?: number
  /** 自定义某个字段的整段文字（例如把 Copilot 的 asset_id 换成作业名）；返回 undefined 时用默认格式。 */
  formatField?: (field: string, value: unknown) => string | undefined
}

const PART_MAX_LENGTH = 14
const SEPARATOR = ' · '

function length(text: string): number {
  return Array.from(text).length
}

function truncate(text: string, max: number): string {
  const chars = Array.from(text)
  if (chars.length <= max) return text
  return `${chars.slice(0, Math.max(1, max - 1)).join('')}…`
}

/** 摘要用的短标签：去掉末尾括注，「最大理智药数」这类写法缩成「理智药」。 */
function compactLabel(label: string): string {
  const withoutNote = label.replace(/\s*[（(][^（）()]*[）)]\s*$/, '')
  const maximum = /^最大\s*(.+?)数$/.exec(withoutNote)
  return maximum ? maximum[1] : withoutNote
}

function labelled(label: string, valueText: string): string {
  const room = PART_MAX_LENGTH - length(valueText) - 1
  return `${truncate(compactLabel(label), Math.max(2, room))} ${valueText}`
}

function describeField(name: string, schema: JsonSchema | undefined, root: JsonSchema, value: unknown): string {
  const normalized = schema ? normalizeSchema(schema, root).schema : {}
  const label = fieldLabel(normalized, name)
  if (value === null || value === '') return labelled(label, '留空')
  const option = enumOptions(normalized).find((candidate) => Object.is(candidate.value, value))
  if (option) return truncate(option.label, PART_MAX_LENGTH)
  if (typeof value === 'boolean') return labelled(label, value ? '开' : '关')
  if (typeof value === 'number') return labelled(label, String(value))
  if (typeof value === 'string') return truncate(value, PART_MAX_LENGTH)
  if (Array.isArray(value)) return labelled(label, `${value.length} 项`)
  if (isObject(value)) return labelled(label, `${Object.keys(value).length} 项`)
  return truncate(label, PART_MAX_LENGTH)
}

/**
 * 任务行的参数摘要：按 schema 字段顺序取用户改动过的前几个字段，例如 Fight 改了关卡与理智药 →「1-7 · 理智药 2」。
 * 字符串直接显示值，枚举显示中文标签，布尔为「标签 开/关」，数组与映射为「标签 N 项」；放不下的字段以「+N」计数。
 */
export function summarizeTaskParams(taskType: TaskTypeSchema, params: Readonly<Record<string, unknown>>, options: SummaryOptions = {}): string {
  const { maxFields = 3, maxLength = 24, formatField } = options
  const fields: Array<[string, JsonSchema | undefined]> = taskFields(taskType)
  const known = new Set(fields.map(([name]) => name))
  for (const name of Object.keys(params)) {
    if (name !== 'name' && !known.has(name)) fields.push([name, undefined])
  }
  const parts = fields
    .filter(([name]) => params[name] !== undefined)
    .map(([name, schema]) => truncate(formatField?.(name, params[name]) ?? describeField(name, schema, taskType.schema, params[name]), PART_MAX_LENGTH))
  if (parts.length === 0) return DEFAULT_PARAMS_SUMMARY
  for (let count = Math.min(maxFields, parts.length); count >= 1; count -= 1) {
    const hidden = parts.length - count
    const text = parts.slice(0, count).join(SEPARATOR) + (hidden > 0 ? ` +${hidden}` : '')
    if (length(text) <= maxLength) return text
  }
  const suffix = parts.length > 1 ? ` +${parts.length - 1}` : ''
  return truncate(parts[0], maxLength - length(suffix)) + suffix
}
