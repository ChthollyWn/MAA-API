import { toApiError } from '@/api/errors'
import { asRecord } from '@/features/settings/schema'
import { errorText } from '@/features/settings/format'

/**
 * 知识库的展示规则与前端校验（M18 规格 §4.1）。
 *
 * 只有字段级校验（标识、标题、什么时候用、正文长度），与后端逐条对齐
 * （`maa_api/agent/knowledge/__init__.py`）：前端只做即时提示，最终仍以后端的 409 / 422 为准。
 * 条目数与术语表/档案正文合计都没有上限（M20）：注入多少以模型自身的上下文为准。
 */
export const KNOWLEDGE_PATH = '/more/settings/knowledge'
export const NEW_KNOWLEDGE_PATH = `${KNOWLEDGE_PATH}/new`

export function knowledgeEntryPath(entryId: string): string {
  return `${KNOWLEDGE_PATH}/${encodeURIComponent(entryId)}`
}

export const KNOWLEDGE_KINDS = ['playbook', 'screen', 'rule', 'glossary', 'profile'] as const
export type KnowledgeKind = (typeof KNOWLEDGE_KINDS)[number]

export const KIND_LABELS: Record<KnowledgeKind, string> = {
  playbook: '操作剧本',
  screen: '界面知识',
  rule: '规则',
  glossary: '术语表',
  profile: '用户档案',
}

/** 正文会整段注入 system 消息的类型；设置页按它们统计正文合计字数。 */
export const FULLTEXT_KINDS: readonly KnowledgeKind[] = ['glossary', 'profile']

export const ENTRY_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,47}$/
/** 字段级约束（列宽 / 表单），不是上下文预算。 */
export const MAX_TITLE_CHARS = 32
export const MAX_WHEN_TO_USE_CHARS = 120
export const MAX_BODY_CHARS = 20_000

export function isKnowledgeKind(value: unknown): value is KnowledgeKind {
  return typeof value === 'string' && (KNOWLEDGE_KINDS as readonly string[]).includes(value)
}

export function kindLabel(kind: string): string {
  return isKnowledgeKind(kind) ? KIND_LABELS[kind] : kind
}

export interface KnowledgeKindGroup<T> {
  /** 认不出的类型落在末尾的 `other` 组，不静默丢掉。 */
  kind: KnowledgeKind | 'other'
  label: string
  items: T[]
}

/** 按类型分组：顺序固定为 操作剧本 / 界面知识 / 规则 / 术语表 / 用户档案。 */
export function groupByKind<T extends { kind: string }>(entries: readonly T[]): KnowledgeKindGroup<T>[] {
  const groups: KnowledgeKindGroup<T>[] = KNOWLEDGE_KINDS
    .map((kind) => ({ kind, label: KIND_LABELS[kind], items: entries.filter((entry) => entry.kind === kind) }))
    .filter((group) => group.items.length > 0)
  const unknown = entries.filter((entry) => !isKnowledgeKind(entry.kind))
  if (unknown.length > 0) groups.push({ kind: 'other', label: '其他', items: unknown })
  return groups
}

export type KnowledgeSourceTone = 'neutral' | 'warning' | 'primary'

/** 来源徽标：内置 / 已修改（覆盖过的内置条目）/ 自定义。 */
export function sourceBadge(entry: { source: string; modified: boolean }): { label: string; tone: KnowledgeSourceTone; description: string } {
  if (entry.source === 'builtin') {
    return entry.modified
      ? { label: '已修改', tone: 'warning', description: '内置条目被改过，可以恢复默认。' }
      : { label: '内置', tone: 'neutral', description: '随 MAA-API 发布的内置条目。' }
  }
  return { label: '自定义', tone: 'primary', description: '自己添加的条目。' }
}

/** 这一条在 Agent 收到的索引里的样子。 */
export function indexLine(entry: { id: string; title: string; when_to_use: string }): string {
  return `- ${entry.id}｜${entry.title}：${entry.when_to_use}`
}

export interface KnowledgeForm {
  id: string
  kind: KnowledgeKind
  title: string
  whenToUse: string
  body: string
  enabled: boolean
}

export function emptyKnowledgeForm(): KnowledgeForm {
  return { id: '', kind: 'playbook', title: '', whenToUse: '', body: '', enabled: true }
}

export function knowledgeFormFromDetail(detail: {
  id: string
  kind: string
  title: string
  when_to_use: string
  body: string
  enabled: boolean
}): KnowledgeForm {
  return {
    id: detail.id,
    kind: isKnowledgeKind(detail.kind) ? detail.kind : 'playbook',
    title: detail.title,
    whenToUse: detail.when_to_use,
    body: detail.body,
    enabled: detail.enabled,
  }
}

/** 未保存判断用的快照：字段一致就认为没改动。 */
export function knowledgeSnapshot(form: KnowledgeForm): string {
  return JSON.stringify(form)
}

export interface KnowledgeFormErrors {
  id?: string
  kind?: string
  title?: string
  whenToUse?: string
  body?: string
  enabled?: string
}

export function validateKnowledgeForm(
  form: KnowledgeForm,
  options: { isNew: boolean },
): KnowledgeFormErrors {
  const errors: KnowledgeFormErrors = {}
  const id = form.id.trim()
  if (options.isNew) {
    if (!id) errors.id = '标识不能为空。'
    else if (!ENTRY_ID_PATTERN.test(id)) errors.id = '标识用 2–48 位小写字母、数字或连字符，并以字母或数字开头。'
  }

  const title = form.title.trim()
  if (!title) errors.title = '标题不能为空。'
  else if (form.title.length > MAX_TITLE_CHARS) errors.title = `标题最多 ${MAX_TITLE_CHARS} 字，现在 ${form.title.length} 字。`

  const whenToUse = form.whenToUse.trim()
  if (!whenToUse) errors.whenToUse = '什么时候用不能为空。'
  else if (form.whenToUse.length > MAX_WHEN_TO_USE_CHARS) {
    errors.whenToUse = `什么时候用最多 ${MAX_WHEN_TO_USE_CHARS} 字，现在 ${form.whenToUse.length} 字。`
  }

  const body = form.body.trim()
  if (!body) errors.body = '正文不能为空。'
  else if (form.body.length > MAX_BODY_CHARS) errors.body = `正文最多 ${MAX_BODY_CHARS} 字，现在 ${form.body.length} 字。`

  return errors
}

export function lengthHint(value: string, max: number): string {
  return `${value.length} / ${max} 字`
}

/**
 * `updated_at` 是不带时区的 UTC 串：`new Date()` 会按本地时区解释它，
 * 补齐 `Z` 再解析，否则东八区会少显示 8 小时。
 */
const NAIVE_UTC = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

export function parseUtcTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = value.trim()
  const normalized = NAIVE_UTC.test(text) ? `${text.replace(' ', 'T')}Z` : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

const updatedAtFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' })

export function formatUpdatedAt(value: string | null | undefined): string | null {
  const date = parseUtcTimestamp(value)
  return date ? updatedAtFormat.format(date) : null
}

/** 后端给出通用 message 时回落到的中文说明（格式见 `docs/05` 的统一错误体）。 */
const GENERIC_MESSAGES = new Set(['与当前状态冲突', '参数校验失败', '资源不存在'])
const CODE_TEXT: Record<string, string> = {
  KNOWLEDGE_NOT_FOUND: '这条知识条目不存在，可能已经在别处被删掉了。',
  KNOWLEDGE_BUILTIN_READONLY: '内置条目不能改类型，也不能删除；要停用就关掉「启用」。',
  VALIDATION_ERROR: '字段不合法，请检查标识、类型、标题、什么时候用和正文。',
}

export function describeKnowledgeError(error: unknown): string {
  const failure = toApiError(error)
  if (failure.status === undefined && !failure.code) return errorText(error)
  const message = failure.message.trim()
  if (message && !GENERIC_MESSAGES.has(message) && !/^请求(参数)?校验失败/.test(message)) return message
  const text = failure.code ? CODE_TEXT[failure.code] : undefined
  return text ?? message ?? ''
}

const SERVER_FIELD_NAMES: Record<string, keyof KnowledgeFormErrors> = {
  entry_id: 'id',
  id: 'id',
  kind: 'kind',
  title: 'title',
  when_to_use: 'whenToUse',
  body: 'body',
  enabled: 'enabled',
}

const SERVER_FIELD_LABELS: Record<keyof KnowledgeFormErrors, string> = {
  id: '标识',
  kind: '类型',
  title: '标题',
  whenToUse: '什么时候用',
  body: '正文',
  enabled: '启用',
}

/** 422 的 `details.fields` 逐字段映射回表单，标出是哪一项被服务端拒绝。 */
export function serverFieldErrors(error: unknown): KnowledgeFormErrors {
  const details = asRecord(toApiError(error).details)
  const fields = Array.isArray(details.fields) ? details.fields : []
  const errors: KnowledgeFormErrors = {}
  for (const raw of fields) {
    const item = asRecord(raw)
    const loc = Array.isArray(item.loc) ? item.loc : []
    const last = loc[loc.length - 1]
    const key = typeof last === 'string' ? SERVER_FIELD_NAMES[last] : undefined
    if (key && !errors[key]) errors[key] = `服务端拒绝了「${SERVER_FIELD_LABELS[key]}」：${serverFieldMessage(item)}`
  }
  return errors
}

/** 校验错误里的英文是 pydantic 的 msg，按 `type` 换成中文；服务端已经给中文时原样保留。 */
const SERVER_ERROR_TYPES: Record<string, string> = {
  missing: '不能为空。',
  string_too_short: '内容太短。',
  string_too_long: '内容太长。',
  string_pattern_mismatch: '格式不符合要求。',
  string_type: '需要填写文本。',
  literal_error: '不是允许的取值。',
  bool_parsing: '需要开关值。',
  int_parsing: '需要整数。',
  value_error: '取值不符合要求。',
}

const CJK = /[\u4e00-\u9fff]/

function serverFieldMessage(item: Record<string, unknown>): string {
  const message = typeof item.msg === 'string' ? item.msg.trim() : ''
  if (CJK.test(message)) return message
  const type = typeof item.type === 'string' ? item.type : ''
  return SERVER_ERROR_TYPES[type] ?? '内容不合法，请检查后重试。'
}
