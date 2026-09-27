import { ApiError, toApiError } from '@/api/errors'
import { isObject, schemaProperties, textValue, type TaskTypeSchema } from './schema'
import type { TaskDraft } from './serialize'

export function unwrapHttpError(value: unknown): Record<string, unknown> {
  if (isObject(value) && isObject(value.error)) return value.error
  if (isObject(value)) return value
  return { message: typeof value === 'string' ? value : '请求失败，请稍后重试' }
}

export function errorMessage(value: unknown): string {
  const apiError = toApiError(value)
  if (apiError.status === 403) return '请求被策略拒绝（403）。若操作本应允许，请检查代理是否剥离了 X-Token 鉴权头，并重新登录。'
  const source = unwrapHttpError(value)
  const nested = isObject(source.error) ? source.error : source
  const message = nested.message ?? source.message
  return typeof message === 'string' ? message : '请求失败，请稍后重试'
}

export interface FieldIssue {
  taskIndex?: number
  field: string
  message: string
}

/**
 * 从 validate / pipelines 的错误体里收集字段错误。支持两种 details 形态：
 * `{ field, message, task_index? }`（服务端业务校验）与 `{ loc: ['body', 'tasks', i, …, field], msg }`（pydantic）。
 */
export function collectFieldIssues(body: unknown, taskCount: number): { issues: FieldIssue[]; message: string } {
  const issues: FieldIssue[] = []
  const payload = body instanceof ApiError ? body.body : body
  const error = unwrapHttpError(payload)
  const details = isObject(error.details) ? error.details : isObject(error.context) ? error.context : error
  const visit = (candidate: unknown, location: unknown[] = []) => {
    if (Array.isArray(candidate)) {
      for (const entry of candidate) visit(entry, location)
      return
    }
    if (!isObject(candidate)) return
    if (Array.isArray(candidate.loc)) {
      const loc = candidate.loc
      const tasksAt = loc.indexOf('tasks')
      const possibleIndex = tasksAt >= 0 ? loc[tasksAt + 1] : undefined
      const field = [...loc].reverse().find((part) => typeof part === 'string' && !['body', 'tasks', 'task'].includes(part))
      if (typeof field === 'string') issues.push({ taskIndex: typeof possibleIndex === 'number' ? possibleIndex : undefined, field, message: textValue(candidate.msg, errorMessage(body)) })
    }
    if (typeof candidate.field === 'string') {
      let taskIndex: number | undefined
      for (let index = 0; index < taskCount; index += 1) {
        if (candidate.task_index === index || candidate.taskIndex === index || candidate.index === index) taskIndex = index
      }
      issues.push({ taskIndex, field: candidate.field, message: textValue(candidate.message, errorMessage(body)) })
    }
    for (const key of ['errors', 'fields', 'detail', 'details', 'context']) {
      if (candidate[key] !== undefined && candidate[key] !== candidate) visit(candidate[key], [...location, key])
    }
  }
  visit(details)
  if (details !== error) visit(error.detail)
  const unique = issues.filter((issue, index) => issues.findIndex((candidate) => candidate.field === issue.field && candidate.taskIndex === issue.taskIndex && candidate.message === issue.message) === index)
  return { issues: unique, message: errorMessage(body) }
}

/** 把字段错误映射到「任务 id → 字段名 → 消息」；没有任务序号时落到第一个拥有该字段的任务，未知字段丢弃。 */
export function mapFieldIssues(
  issues: readonly FieldIssue[],
  tasks: ReadonlyArray<Pick<TaskDraft, 'id' | 'name'>>,
  taskTypes: readonly TaskTypeSchema[],
): Record<string, Record<string, string>> {
  const result: Record<string, Record<string, string>> = {}
  for (const issue of issues) {
    const candidates = issue.taskIndex === undefined ? tasks : tasks.slice(issue.taskIndex, issue.taskIndex + 1)
    const matched = candidates.find((task) => Object.keys(schemaProperties(taskTypes.find((type) => type.name === task.name)?.schema ?? {})).some((field) => field === issue.field))
    if (!matched) continue
    result[matched.id] ??= {}
    result[matched.id][issue.field] = issue.message
  }
  return result
}
