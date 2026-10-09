import type { components } from '@/types/api'
import { MASK, asRecord, type JsonRecord } from './schema'

export type ChannelType = components['schemas']['NotifyChannelType']
export type NotifyEventId = components['schemas']['NotifyEvent']

export const CHANNEL_TYPES = [
  { value: 'email', label: '邮件' },
  { value: 'webhook', label: 'Webhook' },
  { value: 'bark', label: 'Bark' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wecom', label: '企业微信' },
  { value: 'feishu', label: '飞书' },
] as const satisfies readonly { value: ChannelType; label: string }[]

export function isChannelType(value: unknown): value is ChannelType {
  return CHANNEL_TYPES.some((type) => type.value === value)
}

export function channelTypeLabel(type: string): string {
  return CHANNEL_TYPES.find((entry) => entry.value === type)?.label ?? type
}

export const NOTIFY_EVENTS = [
  { value: 'pipeline_completed', label: '流水线完成' },
  { value: 'pipeline_failed', label: '流水线失败' },
  { value: 'core_crashed', label: '内核异常退出' },
  { value: 'device_disconnected', label: '设备断开连接' },
  { value: 'update_available', label: '发现可用更新' },
  { value: 'update_finished', label: '更新完成' },
  { value: 'confirmation_required', label: '需要人工确认' },
] as const satisfies readonly { value: NotifyEventId; label: string }[]

export function isNotifyEvent(value: unknown): value is NotifyEventId {
  return NOTIFY_EVENTS.some((event) => event.value === value)
}

export type ChannelFieldKind = 'text' | 'url' | 'secret' | 'port' | 'switch' | 'list' | 'method' | 'headers' | 'template'

export interface ChannelFieldSpec {
  key: string
  label: string
  kind: ChannelFieldKind
  required?: boolean
  /** 与后端配置模型一致的默认值；没有默认值的项新建时不写入。 */
  initial?: string | boolean | readonly string[] | Readonly<Record<string, string>>
  /** 留空时不写入配置，由后端使用缺省值。 */
  omitWhenEmpty?: boolean
  /** url：允许的协议。 */
  schemes?: readonly ('http:' | 'https:')[]
  /** list：每一项必须是邮箱地址。 */
  email?: boolean
  /** list：单项的称呼，用在「请至少添加一个收件人」里。 */
  itemName?: string
  placeholder?: string
  description?: string
}

const WEB_SCHEMES = ['http:', 'https:'] as const
const HTTPS_ONLY = ['https:'] as const

/** 字段、默认值与必填项对齐 `maa_api/services/notify_service.py` 的配置模型。 */
export const CHANNEL_FIELDS: Readonly<Record<ChannelType, readonly ChannelFieldSpec[]>> = {
  email: [
    { key: 'server', label: 'SMTP 服务器', kind: 'text', required: true, initial: '', placeholder: 'smtp.example.com' },
    { key: 'port', label: '端口', kind: 'port', required: true, placeholder: '465' },
    {
      key: 'use_tls',
      label: '使用 SSL/TLS 连接',
      kind: 'switch',
      initial: true,
      description: '开启时直接建立加密连接（常用端口 465）；关闭时先普通连接再升级加密（常用端口 587）',
    },
    { key: 'username', label: '用户名', kind: 'text', initial: '', placeholder: '通常是发件邮箱', description: '留空则不登录；发件人用这个地址，没有时用第一个收件人' },
    { key: 'password', label: '密码或授权码', kind: 'secret', initial: '' },
    { key: 'to', label: '收件人', kind: 'list', required: true, initial: [], email: true, itemName: '收件人', placeholder: 'name@example.com' },
  ],
  webhook: [
    { key: 'url', label: '请求地址', kind: 'url', required: true, initial: '', schemes: WEB_SCHEMES, placeholder: 'https://example.com/hook' },
    { key: 'method', label: '请求方法', kind: 'method', initial: 'POST' },
    { key: 'headers', label: '请求头', kind: 'headers', initial: {} },
    { key: 'body_template', label: '消息模板', kind: 'template', initial: '{title}\n{body}', description: '用 {title} 和 {body} 插入通知标题与正文，只做文本替换' },
  ],
  bark: [
    { key: 'server', label: '服务器', kind: 'url', initial: 'https://api.day.app', omitWhenEmpty: true, schemes: WEB_SCHEMES, description: '使用自建 Bark 服务时改成自己的地址' },
    { key: 'device_key', label: '设备密钥', kind: 'secret', required: true, initial: '', description: 'Bark App 推送地址中的那段 key' },
    { key: 'sound', label: '提示音', kind: 'text', omitWhenEmpty: true, placeholder: '留空使用默认提示音' },
    { key: 'group', label: '分组', kind: 'text', omitWhenEmpty: true, placeholder: '例如 MAA' },
  ],
  dingtalk: [
    { key: 'webhook', label: '机器人 Webhook', kind: 'url', required: true, initial: '', schemes: HTTPS_ONLY, placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=…' },
    { key: 'secret', label: '加签密钥', kind: 'secret', initial: '', description: '机器人安全设置选择「加签」时填写' },
    { key: 'at_mobiles', label: '@ 手机号', kind: 'list', initial: [], itemName: '手机号', placeholder: '13800000000' },
  ],
  wecom: [
    { key: 'webhook', label: '机器人 Webhook', kind: 'url', required: true, initial: '', schemes: HTTPS_ONLY, placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…' },
    { key: 'mentioned_list', label: '@ 成员', kind: 'list', initial: [], itemName: '成员', placeholder: '成员 UserID，或 @all', description: '@all 会提醒群里所有人' },
  ],
  feishu: [
    { key: 'webhook', label: '机器人 Webhook', kind: 'url', required: true, initial: '', schemes: HTTPS_ONLY, placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/…' },
    { key: 'secret', label: '签名密钥', kind: 'secret', initial: '', description: '机器人安全设置勾选「签名校验」时填写，未勾选则留空' },
    { key: 'at_user_ids', label: '@ 成员', kind: 'list', initial: [], itemName: '成员 ID', placeholder: '用户 ID（ou_… 或 user_id），或 all', description: '填 all 会 @ 群里所有人；ID 里不能有引号、尖括号或空格' },
  ],
}

export interface HeaderRow {
  id: number
  name: string
  value: string
}

export type ChannelFieldValue = string | boolean | string[] | HeaderRow[]
export type ChannelFormValues = Record<string, ChannelFieldValue>

let nextHeaderRowId = 1

export function headerRow(name = '', value = ''): HeaderRow {
  nextHeaderRowId += 1
  return { id: nextHeaderRowId, name, value }
}

function textOf(value: ChannelFieldValue | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

function listOf(value: ChannelFieldValue | undefined): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export function headerRowsOf(value: ChannelFieldValue | undefined): HeaderRow[] {
  return Array.isArray(value) ? value.filter((row): row is HeaderRow => typeof row === 'object' && row !== null) : []
}

function stringify(value: unknown): string {
  if (typeof value === 'string') return value
  if (value !== null && typeof value === 'object') return JSON.stringify(value)
  return value === undefined || value === null ? '' : String(value)
}

export function defaultChannelConfig(type: ChannelType): JsonRecord {
  const config: JsonRecord = {}
  for (const spec of CHANNEL_FIELDS[type]) {
    if (spec.initial === undefined) continue
    config[spec.key] = Array.isArray(spec.initial) ? [...spec.initial] : typeof spec.initial === 'object' ? { ...spec.initial } : spec.initial
  }
  return config
}

/** 配置（服务端已脱敏）转成表单值；脱敏的密钥显示为空输入框。 */
export function formValuesFromConfig(type: ChannelType, config: JsonRecord): ChannelFormValues {
  const values: ChannelFormValues = {}
  for (const spec of CHANNEL_FIELDS[type]) {
    const raw = config[spec.key]
    switch (spec.kind) {
      case 'switch':
        values[spec.key] = typeof raw === 'boolean' ? raw : spec.initial === true
        break
      case 'list':
        values[spec.key] = Array.isArray(raw) ? raw.map(stringify) : []
        break
      case 'headers':
        values[spec.key] = Object.entries(asRecord(raw)).map(([name, value]) => headerRow(name, stringify(value)))
        break
      case 'method':
        values[spec.key] = typeof raw === 'string' && raw.trim() ? raw.trim().toUpperCase() : 'POST'
        break
      case 'secret':
        values[spec.key] = typeof raw === 'string' && raw !== MASK ? raw : ''
        break
      default:
        values[spec.key] = stringify(raw)
    }
  }
  return values
}

/**
 * 表单值转成提交的配置。`source` 是表单载入时的配置：留空的密钥若原来是 `***` 就原样提交，
 * 由服务端换回已保存的值；表单不认识的键也原样保留，两种编辑模式来回切换不丢数据。
 */
export function configFromFormValues(type: ChannelType, values: ChannelFormValues, source: JsonRecord): JsonRecord {
  const specs = CHANNEL_FIELDS[type]
  const config: JsonRecord = {}
  for (const spec of specs) {
    const value = values[spec.key]
    switch (spec.kind) {
      case 'switch':
        config[spec.key] = value === true
        break
      case 'list':
        config[spec.key] = listOf(value).map((item) => item.trim()).filter(Boolean)
        break
      case 'headers':
        config[spec.key] = Object.fromEntries(
          headerRowsOf(value)
            .map((row) => [row.name.trim(), row.value.trim()] as const)
            .filter(([name, headerValue]) => name || headerValue),
        )
        break
      case 'method':
        config[spec.key] = textOf(value) || 'POST'
        break
      case 'secret':
        config[spec.key] = textOf(value) || (source[spec.key] === MASK ? MASK : '')
        break
      case 'port': {
        const text = textOf(value)
        if (!text) break
        const port = Number(text)
        config[spec.key] = Number.isFinite(port) ? port : text
        break
      }
      default: {
        const text = textOf(value)
        if (text || !spec.omitWhenEmpty) config[spec.key] = text
      }
    }
  }
  const known = new Set(specs.map((spec) => spec.key))
  for (const [key, value] of Object.entries(source)) {
    if (!known.has(key)) config[key] = value
  }
  return config
}

function requiredMessage(label: string): string {
  return `请填写${/^[A-Za-z0-9]/.test(label) ? ' ' : ''}${label}。`
}

function allowedUrl(text: string, schemes: readonly string[]): boolean {
  try {
    const url = new URL(text)
    return schemes.includes(url.protocol) && Boolean(url.host) && !url.username && !url.password
  } catch {
    return false
  }
}

const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/

export function isEmailAddress(text: string): boolean {
  return /^[^\s@]+@[^\s@]+$/.test(text)
}

/** 表单模式保存前的校验；返回字段键到错误文案的映射。 */
export function validateChannelForm(type: ChannelType, values: ChannelFormValues, source: JsonRecord): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const spec of CHANNEL_FIELDS[type]) {
    const value = values[spec.key]
    switch (spec.kind) {
      case 'text':
      case 'template':
        if (spec.required && !textOf(value)) errors[spec.key] = requiredMessage(spec.label)
        break
      case 'url': {
        const text = textOf(value)
        if (!text) {
          if (spec.required) errors[spec.key] = requiredMessage(spec.label)
        } else if (!allowedUrl(text, spec.schemes ?? WEB_SCHEMES)) {
          errors[spec.key] = spec.schemes === HTTPS_ONLY ? '请输入以 https:// 开头的地址。' : '请输入以 http:// 或 https:// 开头的地址。'
        }
        break
      }
      case 'secret':
        if (spec.required && !textOf(value) && source[spec.key] !== MASK) errors[spec.key] = requiredMessage(spec.label)
        break
      case 'port': {
        const text = textOf(value)
        const port = Number(text)
        if (!text) {
          if (spec.required) errors[spec.key] = requiredMessage(spec.label)
        } else if (!Number.isInteger(port) || port < 1 || port > 65535) {
          errors[spec.key] = '请输入 1 ~ 65535 之间的端口号。'
        }
        break
      }
      case 'list': {
        const items = listOf(value).map((item) => item.trim()).filter(Boolean)
        if (spec.required && !items.length) errors[spec.key] = `请至少添加一个${spec.itemName ?? spec.label}。`
        else if (spec.email && items.some((item) => !isEmailAddress(item))) errors[spec.key] = '请填写有效的邮箱地址。'
        break
      }
      case 'headers': {
        const rows = headerRowsOf(value).filter((row) => row.name.trim() || row.value.trim())
        const names = rows.map((row) => row.name.trim())
        if (names.some((name) => !name)) errors[spec.key] = '请填写请求头名称。'
        else if (names.some((name) => !HEADER_NAME.test(name))) errors[spec.key] = '请只用字母、数字和 - 等符号填写请求头名称。'
        else if (new Set(names.map((name) => name.toLowerCase())).size !== names.length) errors[spec.key] = '请不要填写重复的请求头名称。'
        break
      }
      default:
        break
    }
  }
  return errors
}

export function parseConfigText(text: string): { config: JsonRecord; error?: undefined } | { config?: undefined; error: string } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { error: '请输入有效的 JSON，检查括号、引号和逗号。' }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { error: '请填写一个 JSON 对象。' }
  return { config: parsed as JsonRecord }
}

export function formatConfig(config: JsonRecord): string {
  return JSON.stringify(config, null, 2)
}

const SECRET_KEY = /(authorization|password|passwd|secret|token|api[_-]?key|device[_-]?key|(?:^|[_-])key(?:$|[_-]))/i

/**
 * 服务端已经脱敏；这里再按同样的规则兜底一次，页面状态里不留凭据原文。
 * 范围必须与服务端 `notify_service._mask_config` 一致：敏感键名、`url` / `server` /
 * `webhook` 的查询参数、`headers`。列表上的启停会把这份配置原样写回，超出这个范围的
 * 改写（例如消息模板里的 `&token=`）服务端不会还原，会让之后的发送全部失败。
 */
export function redactConfig(value: unknown, key = ''): unknown {
  if (SECRET_KEY.test(key)) return value ? MASK : value
  if (Array.isArray(value)) return value.map((item) => redactConfig(item))
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as JsonRecord).map(([childKey, child]) => [childKey, redactConfig(child, childKey)]))
  }
  if (typeof value === 'string' && /^(url|server|webhook)$/i.test(key)) {
    try {
      const url = new URL(value)
      for (const [queryKey, queryValue] of url.searchParams.entries()) {
        if (SECRET_KEY.test(queryKey) && queryValue) url.searchParams.set(queryKey, MASK)
      }
      return url.toString()
    } catch {
      return value
    }
  }
  return value
}

export interface Channel {
  id: string
  type: string
  name: string
  config: JsonRecord
  events: string[]
  enabled: boolean
  lastStatus: string | null
  lastSentAt: string | null
  lastError: string | null
}

export function normalizeChannels(payload: unknown): Channel[] {
  const root = asRecord(payload)
  const items = Array.isArray(root.items) ? root.items : []
  return items.flatMap((raw): Channel[] => {
    const item = asRecord(raw)
    if (typeof item.id !== 'string') return []
    return [{
      id: item.id,
      type: typeof item.type === 'string' ? item.type : '',
      name: typeof item.name === 'string' ? item.name : item.id,
      config: asRecord(redactConfig(item.config)),
      events: Array.isArray(item.events) ? item.events.filter((entry): entry is string => typeof entry === 'string') : [],
      enabled: item.enabled !== false,
      lastStatus: typeof item.last_status === 'string' ? item.last_status : null,
      lastSentAt: typeof item.last_sent_at === 'string' ? item.last_sent_at : null,
      lastError: typeof item.last_error === 'string' ? item.last_error : null,
    }]
  })
}
