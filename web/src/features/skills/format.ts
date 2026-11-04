import { toApiError } from '@/api/errors'
import { errorText } from '@/features/settings/format'
import { asRecord } from '@/features/settings/schema'
import type { SkillWriteRequest } from './api'

/**
 * 技能页的展示规则、表单与请求体互转（M78 规格 §4.6）。
 *
 * 字段长度与后端 `maa_api/agent/skills/registry.py` 对齐：id 2–48、标题 32、
 * 什么时候用 200、正文 20000。前端只做即时提示，工具与代理的注册情况仍由
 * 服务层在 `PUT` 时统一报 400 `SKILL_INVALID`。
 */
export const SKILLS_PATH = '/more/skills'
export const NEW_SKILL_PATH = `${SKILLS_PATH}/new`

export function skillPath(skillId: string): string {
  return `${SKILLS_PATH}/${encodeURIComponent(skillId)}`
}

export const SKILL_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,47}$/
export const MAX_TITLE_CHARS = 32
export const MAX_DESCRIPTION_CHARS = 200
export const MAX_BODY_CHARS = 20_000

export type SkillSourceGroup = 'builtin' | 'custom' | 'draft'

const SOURCE_GROUP_LABELS: Record<SkillSourceGroup, string> = {
  builtin: '内置',
  custom: '自定义',
  draft: '草稿',
}

export interface SkillGroup<T> {
  key: SkillSourceGroup
  label: string
  items: T[]
}

/** 草稿优先于来源：`status='draft'` 的一律进「草稿」组。 */
function groupKey(skill: { source: string; status: string }): SkillSourceGroup {
  if (skill.status === 'draft') return 'draft'
  return skill.source === 'builtin' ? 'builtin' : 'custom'
}

/** 按「内置 / 自定义 / 草稿」分组，空组不返回。 */
export function groupSkills<T extends { source: string; status: string }>(skills: readonly T[]): SkillGroup<T>[] {
  return (['builtin', 'custom', 'draft'] as const)
    .map((key) => ({
      key,
      label: SOURCE_GROUP_LABELS[key],
      items: skills.filter((skill) => groupKey(skill) === key),
    }))
    .filter((group) => group.items.length > 0)
}

export type SkillBadgeTone = 'neutral' | 'warning' | 'primary' | 'outline'

/** 来源徽标：内置 / 已修改（覆盖过的内置）/ 自定义 / 草稿。 */
export function sourceBadge(skill: { source: string; status: string; modified: boolean }): {
  label: string
  tone: SkillBadgeTone
  description: string
} {
  if (skill.status === 'draft') {
    return { label: '草稿', tone: 'outline', description: '还没启用的草稿技能。' }
  }
  if (skill.source === 'builtin') {
    return skill.modified
      ? { label: '已修改', tone: 'warning', description: '内置技能被改过，可以恢复默认。' }
      : { label: '内置', tone: 'neutral', description: '随 MAA-API 发布的内置技能。' }
  }
  return { label: '自定义', tone: 'primary', description: '自己添加的技能。' }
}

export interface NamedSpec {
  name: string
  title: string
}

/** 定义里的 title 是给人看的名字；缺了就退回代码名。 */
export function specTitle(spec: NamedSpec): string {
  return spec.title.trim() || spec.name
}

/** 能挂技能的是总控与 Worker；子代理和后台任务不出现在选项里。 */
export function skillAgentOptions<T extends { kind: string }>(specs: readonly T[]): T[] {
  return specs.filter((spec) => spec.kind === 'orchestrator' || spec.kind === 'worker')
}

/** 可用代理显示为定义 title 的「、」连接；全不选表示所有代理。 */
export function agentNamesLabel(agents: readonly string[], specs: readonly NamedSpec[]): string {
  if (agents.length === 0) return '所有代理'
  const titles = new Map(specs.map((spec) => [spec.name, specTitle(spec)]))
  return agents.map((agent) => titles.get(agent) ?? agent).join('、')
}

export interface SkillForm {
  id: string
  title: string
  description: string
  toolsText: string
  agents: string[]
  body: string
  enabled: boolean
}

export function emptySkillForm(): SkillForm {
  return { id: '', title: '', description: '', toolsText: '', agents: [], body: '', enabled: true }
}

export function skillFormFromDetail(detail: {
  id: string
  title: string
  description: string
  tools: string[]
  agents: string[]
  body: string
  enabled: boolean
}): SkillForm {
  return {
    id: detail.id,
    title: detail.title,
    description: detail.description,
    toolsText: detail.tools.join('\n'),
    agents: [...detail.agents],
    body: detail.body,
    enabled: detail.enabled,
  }
}

/** 多行工具名：每行一个，空行丢掉。 */
export function parseTools(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

export function skillWriteFromForm(form: SkillForm): SkillWriteRequest {
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    body: form.body,
    tools: parseTools(form.toolsText),
    agents: [...form.agents],
    enabled: form.enabled,
  }
}

/** 未保存判断用的快照：字段一致就认为没改动。 */
export function skillSnapshot(form: SkillForm): string {
  return JSON.stringify(form)
}

export interface SkillFormErrors {
  id?: string
  title?: string
  description?: string
  tools?: string
  agents?: string
  body?: string
}

export function validateSkillForm(form: SkillForm, options: { isNew: boolean }): SkillFormErrors {
  const errors: SkillFormErrors = {}
  if (options.isNew) {
    const id = form.id.trim()
    if (!id) errors.id = 'id 不能为空；小写字母、数字与连字符，2–48 个字符。'
    else if (!SKILL_ID_PATTERN.test(id)) errors.id = 'id 用小写字母、数字与连字符，2–48 个字符，并以字母或数字开头。'
  }

  const title = form.title.trim()
  if (!title) errors.title = '标题不能为空。'
  else if (form.title.length > MAX_TITLE_CHARS) errors.title = `标题最多 ${MAX_TITLE_CHARS} 字，现在 ${form.title.length} 字。`

  const description = form.description.trim()
  if (!description) errors.description = '什么时候用不能为空。'
  else if (form.description.length > MAX_DESCRIPTION_CHARS) {
    errors.description = `什么时候用最多 ${MAX_DESCRIPTION_CHARS} 字，现在 ${form.description.length} 字。`
  }

  const body = form.body.trim()
  if (!body) errors.body = '正文不能为空。'
  else if (form.body.length > MAX_BODY_CHARS) errors.body = `正文最多 ${MAX_BODY_CHARS} 字，现在 ${form.body.length} 字。`

  return errors
}

const GENERIC_MESSAGES = new Set(['与当前状态冲突', '参数校验失败', '资源不存在'])
const CODE_TEXT: Record<string, string> = {
  SKILL_NOT_FOUND: '这个技能不存在，可能已经在别处被删掉了。',
  SKILL_BUILTIN_READONLY: '内置技能不能删除；要停用它就关掉「启用」。',
  SKILL_INVALID: '技能内容不合法，请检查后重试。',
}

export function describeSkillError(error: unknown): string {
  const failure = toApiError(error)
  if (failure.status === undefined && !failure.code) return errorText(error)
  const message = failure.message.trim()
  if (message && !GENERIC_MESSAGES.has(message) && !/^请求(参数)?校验失败/.test(message)) return message
  const text = failure.code ? CODE_TEXT[failure.code] : undefined
  return text ?? message ?? ''
}

const SERVER_FIELD_NAMES: Record<string, keyof SkillFormErrors> = {
  id: 'id',
  title: 'title',
  description: 'description',
  tools: 'tools',
  agents: 'agents',
  body: 'body',
}

const SERVER_FIELD_LABELS: Record<keyof SkillFormErrors, string> = {
  id: 'id',
  title: '标题',
  description: '什么时候用',
  tools: '依赖的工具',
  agents: '可用代理',
  body: '正文',
}

/**
 * 400 `SKILL_INVALID`：`details.field` 对应表单字段，未知工具 / 代理的
 * `details.unknown` 换成「这些工具不存在：…」这样的说明。
 */
export function skillServerFieldErrors(error: unknown): SkillFormErrors {
  const failure = toApiError(error)
  if (failure.code !== 'SKILL_INVALID') return {}
  const details = asRecord(failure.details)
  const field = typeof details.field === 'string' ? details.field : ''
  const key = SERVER_FIELD_NAMES[field]
  if (!key) return {}

  const unknown = Array.isArray(details.unknown)
    ? details.unknown.filter((value): value is string => typeof value === 'string' && value.length > 0)
    : []
  let message: string
  if (key === 'tools' && unknown.length > 0) message = `这些工具不存在：${unknown.join('、')}`
  else if (key === 'agents' && unknown.length > 0) message = `这些代理不能用技能：${unknown.join('、')}`
  else {
    const reason = typeof details.reason === 'string' ? details.reason.trim() : ''
    message = reason || `「${SERVER_FIELD_LABELS[key]}」不合法。`
  }

  const errors: SkillFormErrors = {}
  errors[key] = message
  return errors
}
