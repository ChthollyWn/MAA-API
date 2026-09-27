import { toApiError } from '@/api/errors'
import { isObject, mapFieldIssues, textValue, type FieldIssue, type TaskDraft, type TaskTypeSchema } from '@/features/tasks/form'
import type { ScheduleField, ScheduleFieldErrors, ScheduleForm } from './payload'

/** 任务 id → 字段名 → 错误消息，交给 `TaskListEditor` 显示。 */
export type TaskErrors = Record<string, Record<string, string>>

export interface SaveFailureContext {
  name: string
  mode: ScheduleForm['mode']
  timezone: string
  drafts: readonly TaskDraft[]
  taskTypes: readonly TaskTypeSchema[]
}

export interface SaveFailure {
  fields: ScheduleFieldErrors
  tasks: TaskErrors
  /** 就地提示；错误全部落到了任务行上时为 null（由任务错误摘要提示）。 */
  notice: { title: string; message: string } | null
}

const VALIDATION_CODES = new Set(['VALIDATION_ERROR', 'TASK_PARAM_INVALID', 'TASK_PARAM_DEPRECATED', 'UNKNOWN_TASK_TYPE'])
const FORM_LOCATIONS: Readonly<Record<string, ScheduleField>> = { name: 'name', cron: 'cron', timezone: 'timezone', template: 'tasks' }

export function isKnownTimeZone(value: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value })
    return true
  } catch {
    return false
  }
}

/**
 * 定时任务的校验错误有三种形态：请求体校验的 `details.fields`（loc 形如 `['body', 'template', i, 类型, 字段]`）、
 * 模板复核的 `details.issues`（loc 形如 `['tasks', i, 类型, 字段]`），以及跨字段规则的 `details.field`。
 * 模板里的错误映射到任务行，其余位置映射到名称、cron、时区。
 */
function collectIssues(details: Record<string, unknown>, fallback: string, drafts: readonly TaskDraft[]) {
  const fields: ScheduleFieldErrors = {}
  const tasks: FieldIssue[] = []
  const entries = [details.fields, details.issues].flatMap((list) => (Array.isArray(list) ? list : []))
  for (const entry of entries) {
    if (!isObject(entry) || !Array.isArray(entry.loc)) continue
    const loc: unknown[] = entry.loc[0] === 'body' ? entry.loc.slice(1) : entry.loc
    const message = textValue(entry.msg, fallback)
    const [head, index, ...rest] = loc
    if ((head === 'template' || head === 'tasks') && typeof index === 'number') {
      // 判别联合的 loc 在字段前带着任务类型名。
      const path = rest[0] === drafts[index]?.name ? rest.slice(1) : rest
      const field = path.find((part): part is string => typeof part === 'string')
      if (field) tasks.push({ taskIndex: index, field, message })
      continue
    }
    const target = typeof head === 'string' ? FORM_LOCATIONS[head] : undefined
    if (target && loc.length === 1) fields[target] ??= message
  }
  if (typeof details.field === 'string') tasks.push({ field: details.field, message: fallback })
  return { fields, tasks }
}

export function describeSaveFailure(error: unknown, context: SaveFailureContext): SaveFailure {
  const apiError = toApiError(error)
  if (apiError.code === 'SCHEDULE_NAME_CONFLICT') {
    return {
      fields: { name: '已有同名的定时任务。' },
      tasks: {},
      notice: { title: '名称已被使用', message: `已有同名的定时任务「${context.name.trim()}」，请换一个名称。` },
    }
  }
  if (apiError.code === 'SCHEDULE_CRON_INVALID') {
    const field: ScheduleField = context.mode === 'cron' && isKnownTimeZone(context.timezone.trim()) ? 'cron' : 'timezone'
    return {
      fields: field === 'cron' ? { cron: '不是有效的五段 cron 表达式。' } : { timezone: '不是有效的时区名，例如 Asia/Shanghai。' },
      tasks: {},
      notice: { title: '时间设置无效', message: apiError.message },
    }
  }
  const details = isObject(apiError.details) ? apiError.details : {}
  const issues = collectIssues(details, apiError.message, context.drafts)
  const tasks = mapFieldIssues(issues.tasks, context.drafts, context.taskTypes)
  const mapped = Object.keys(tasks).length > 0 || Object.keys(issues.fields).length > 0
  if (mapped) {
    return {
      fields: issues.fields,
      tasks,
      notice: Object.keys(tasks).length > 0 ? null : { title: '校验未通过', message: '请检查标出的内容。' },
    }
  }
  const validation = apiError.status === 422 || (apiError.code !== undefined && VALIDATION_CODES.has(apiError.code))
  return { fields: {}, tasks: {}, notice: { title: validation ? '校验未通过' : '保存失败', message: apiError.message } }
}

function changedFields(previous: Record<string, unknown>, next: Record<string, unknown>): Set<string> {
  const names = new Set([...Object.keys(previous), ...Object.keys(next)])
  return new Set([...names].filter((name) => !Object.is(previous[name], next[name])))
}

/** 改过的字段与删掉的任务不再显示旧错误；其余错误保留到下一次保存。没有变化时原样返回。 */
export function pruneTaskErrors(errors: TaskErrors, previous: readonly TaskDraft[], next: readonly TaskDraft[]): TaskErrors {
  const before = new Map(previous.map((draft) => [draft.id, draft]))
  const result: TaskErrors = {}
  let changed = false
  for (const [id, fields] of Object.entries(errors)) {
    const draft = next.find((candidate) => candidate.id === id)
    const old = before.get(id)
    if (!draft) {
      changed = true
      continue
    }
    const edited = old ? changedFields(old.params, draft.params) : new Set<string>()
    const kept = Object.fromEntries(Object.entries(fields).filter(([field]) => !edited.has(field)))
    if (Object.keys(kept).length !== Object.keys(fields).length) changed = true
    if (Object.keys(kept).length > 0) result[id] = kept
  }
  return changed ? result : errors
}
