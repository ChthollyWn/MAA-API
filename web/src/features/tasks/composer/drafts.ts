import { isObject, materializeDefaults, sameValue, serializeTask, type TaskDraft, type TaskTypeSchema } from '../form'

/** 服务端单条流水线最多 32 个任务（PIPELINE_TOO_MANY_TASKS）。 */
export const MAX_TASKS = 32

export function randomId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

/** 新建草稿：把 schema 有默认值的字段预填进参数（M17 起「预填即下发」，见 docs/09 §7.5）。 */
export function createDraft(taskType: TaskTypeSchema, params: Record<string, unknown> = {}): TaskDraft {
  return { id: randomId(), name: taskType.name, params: materializeDefaults(taskType, params) }
}

/** 复制草稿；任务类型未知（服务端不再提供）时原样复制参数。 */
export function duplicateDraft(draft: TaskDraft, taskType: TaskTypeSchema | undefined): TaskDraft {
  const params = structuredClone(draft.params)
  return taskType ? createDraft(taskType, params) : { id: randomId(), name: draft.name, params }
}

/** 按任务类型目录给一批草稿补一次物化；本来就是物化的草稿原样返回（引用不变）。 */
export function materializeDrafts(drafts: readonly TaskDraft[], taskTypes: readonly TaskTypeSchema[]): TaskDraft[] {
  return drafts.map((draft) => {
    const taskType = taskTypes.find((candidate) => candidate.name === draft.name)
    if (!taskType) return draft
    const params = materializeDefaults(taskType, draft.params)
    return sameValue(params, draft.params) ? draft : { ...draft, params }
  })
}

export function moveDraft(drafts: readonly TaskDraft[], index: number, offset: -1 | 1): TaskDraft[] {
  const target = index + offset
  const next = [...drafts]
  if (target < 0 || target >= next.length) return next
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

/** 扁平模板 `{ name, ...参数 }`（定时任务模板、`POST /api/pipelines` 的 tasks）→ 编排草稿。 */
export function draftsFromTemplate(template: readonly unknown[], taskTypes: readonly TaskTypeSchema[]): TaskDraft[] {
  return template.flatMap((task) => {
    if (!isObject(task) || typeof task.name !== 'string') return []
    const { name, ...params } = task
    const taskType = taskTypes.find((candidate) => candidate.name === name)
    return [taskType ? createDraft(taskType, params) : { id: randomId(), name, params }]
  })
}

/** 编排草稿 → 与 `POST /api/pipelines` 的 tasks 相同的扁平模板，含预填的默认值（所见即所提交）。 */
export function templateFromDrafts(drafts: readonly TaskDraft[]): Record<string, unknown>[] {
  return drafts.map(serializeTask)
}
