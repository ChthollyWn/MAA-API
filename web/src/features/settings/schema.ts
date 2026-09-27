export type JsonRecord = Record<string, unknown>

/** 服务端对敏感值的脱敏占位。 */
export const MASK = '***'

export interface SettingField {
  key: string
  label: string
  group: string
  /** string / integer / number / boolean / integer[] / string[] */
  type: string
  minimum?: number
  maximum?: number
  options: string[]
  sensitive: boolean
  readonly: boolean
  /** none / reconnect / restart_required */
  hotAction: string
}

export interface SettingState {
  /** 敏感项只会是 `***` 或空串：服务端返回的原文不进入页面状态。 */
  value: unknown
  source: string
  configured: boolean
  masked: boolean
}

export interface SettingsData {
  fields: SettingField[]
  values: Record<string, SettingState>
}

export type ParseResult = { value: unknown; error?: undefined } | { value?: undefined; error: string }

export function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as JsonRecord) : {}
}

export function settingSchema(payload: unknown): SettingField[] {
  const root = asRecord(payload)
  const items = Array.isArray(root.items) ? root.items : []
  return items.flatMap((raw): SettingField[] => {
    const item = asRecord(raw)
    if (typeof item.key !== 'string') return []
    return [{
      key: item.key,
      label: typeof item.label === 'string' ? item.label : item.key,
      group: typeof item.group === 'string' ? item.group : '其他',
      type: typeof item.type === 'string' ? item.type : 'string',
      minimum: typeof item.minimum === 'number' ? item.minimum : undefined,
      maximum: typeof item.maximum === 'number' ? item.maximum : undefined,
      options: Array.isArray(item.enum) ? item.enum.filter((entry): entry is string => typeof entry === 'string') : [],
      sensitive: item.sensitive === true,
      readonly: item.readonly === true,
      hotAction: typeof item.hot_action === 'string' ? item.hot_action : 'none',
    }]
  })
}

export function settingEntries(payload: unknown, fields: readonly SettingField[]): Record<string, SettingState> {
  const root = asRecord(payload)
  const candidate = root.items ?? root.settings ?? root.values ?? root
  const pairs: Array<[string, unknown]> = Array.isArray(candidate)
    ? candidate.flatMap((item): Array<[string, unknown]> => {
        const record = asRecord(item)
        return typeof record.key === 'string' ? [[record.key, item]] : []
      })
    : Object.entries(asRecord(candidate))
  const byKey = new Map(fields.map((field) => [field.key, field]))
  const entries: Record<string, SettingState> = {}

  for (const [key, raw] of pairs) {
    const record = asRecord(raw)
    const value = Object.prototype.hasOwnProperty.call(record, 'value') ? record.value : raw
    const source = typeof record.source === 'string' ? record.source : 'unknown'
    const sensitive = byKey.get(key)?.sensitive === true || record.sensitive === true
    const present = value !== undefined && value !== null && value !== ''
    // 服务端对敏感项一律返回 `***`，只能靠来源判断：取自代码默认值即从未配置过。
    const configured = present && !(sensitive && source === 'default')
    entries[key] = {
      value: sensitive ? (configured ? MASK : '') : value,
      source,
      configured,
      masked: sensitive && configured && value === MASK,
    }
  }
  return entries
}

const SOURCE_LABELS = new Map([
  ['env', '环境变量'],
  ['db', '数据库覆盖'],
  ['yaml', '配置文件'],
  ['default', '默认值'],
])

export function sourceLabel(source: string): string {
  return SOURCE_LABELS.get(source) ?? (!source || source === 'unknown' ? '来源未知' : source)
}

export function hotActionLabel(action: string): string {
  if (action === 'none') return '保存后立即生效'
  if (action === 'reconnect') return '保存后会重新连接设备'
  if (action === 'restart_required') return '重启服务后生效'
  return `生效方式：${action}`
}

export function rangeLabel(minimum?: number, maximum?: number): string | null {
  if (minimum !== undefined && maximum !== undefined) return `范围 ${minimum} ~ ${maximum}`
  if (minimum !== undefined) return `最小 ${minimum}`
  if (maximum !== undefined) return `最大 ${maximum}`
  return null
}

export function isListType(type: string): type is 'integer[]' | 'string[]' {
  return type === 'integer[]' || type === 'string[]'
}

/** 列表控件交来数组；逗号分隔的文本仍然接受，方便脚本或旧数据。 */
function listItems(raw: unknown): unknown[] | null {
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string') return raw.trim() ? raw.split(',') : []
  return null
}

/** 把控件里的值转成要提交的值；不合法时返回就地显示的错误。 */
export function parseFieldValue(field: SettingField, raw: unknown): ParseResult {
  const value = typeof raw === 'string' ? raw.trim() : raw
  if (field.type === 'integer' || field.type === 'number') {
    if (value === '' || value === undefined || value === null) return { error: '请填写数值。' }
    const parsed = typeof value === 'number' ? value : Number(value)
    if (!Number.isFinite(parsed)) return { error: '请输入有效数值。' }
    if (field.type === 'integer' && !Number.isInteger(parsed)) return { error: '请输入整数。' }
    if (field.minimum !== undefined && parsed < field.minimum) return { error: `不能小于 ${field.minimum}。` }
    if (field.maximum !== undefined && parsed > field.maximum) return { error: `不能大于 ${field.maximum}。` }
    return { value: parsed }
  }
  if (field.type === 'integer[]') {
    const items = listItems(raw)
    if (!items?.length) return { error: '请至少添加一个整数。' }
    const values: number[] = []
    for (const item of items) {
      const text = typeof item === 'string' ? item.trim() : item
      const parsed = text === '' || typeof text === 'boolean' || text === null ? Number.NaN : Number(text)
      if (!Number.isInteger(parsed)) return { error: '每一项都必须是整数。' }
      if (field.minimum !== undefined && parsed < field.minimum) return { error: `每个值不能小于 ${field.minimum}。` }
      if (field.maximum !== undefined && parsed > field.maximum) return { error: `每个值不能大于 ${field.maximum}。` }
      values.push(parsed)
    }
    if (new Set(values).size !== values.length) return { error: '不能包含重复的值。' }
    return { value: values }
  }
  if (field.type === 'string[]') {
    const items = listItems(raw)
    if (!items) return { error: '请填写文本列表。' }
    const values = items.map((item) => String(item ?? '').trim())
    if (values.some((item) => !item)) return { error: '列表中不能有空白项。' }
    if (new Set(values).size !== values.length) return { error: '不能包含重复的值。' }
    return { value: values }
  }
  if (field.type === 'boolean') return { value: value === true || value === 'true' }
  if (typeof value === 'string' && field.options.length && !field.options.includes(value)) {
    return { error: '请选择列表中的有效选项。' }
  }
  return { value }
}

/** 控件的初始值：数字按文本编辑，列表复制一份，敏感项留空。 */
export function initialInputValue(field: SettingField, state: SettingState | undefined): unknown {
  if (field.sensitive) return ''
  const value = state?.value
  if (field.type === 'boolean') return value === true
  if (isListType(field.type)) return Array.isArray(value) ? [...value] : []
  if (value === null || value === undefined) return ''
  return String(value)
}

export function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (left !== null && right !== null && typeof left === 'object' && typeof right === 'object') {
    return JSON.stringify(left) === JSON.stringify(right)
  }
  return false
}

/** 只有提交后会改变服务端值的草稿才算改动；敏感项填了内容就算。 */
export function isFieldChanged(field: SettingField, draft: unknown, state: SettingState | undefined): boolean {
  if (draft === undefined || field.readonly) return false
  if (field.sensitive) return typeof draft === 'string' && draft.trim() !== ''
  if (sameValue(draft, initialInputValue(field, state))) return false
  const parsed = parseFieldValue(field, draft)
  return parsed.error !== undefined || !sameValue(parsed.value, state?.value)
}

export function fieldDomId(key: string): string {
  return `setting-${key.replace(/[^a-zA-Z0-9_-]/g, '-')}`
}
