import { isObject, serializeTask, type TaskDraft } from '../form'

/** 服务端单条流水线最多 32 个任务（PIPELINE_TOO_MANY_TASKS）。 */
export const MAX_TASKS = 32

export function randomId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

export function createDraft(name: string, params: Record<string, unknown> = {}): TaskDraft {
  return { id: randomId(), name, params }
}

export function duplicateDraft(draft: TaskDraft): TaskDraft {
  return createDraft(draft.name, structuredClone(draft.params))
}

export function moveDraft(drafts: readonly TaskDraft[], index: number, offset: -1 | 1): TaskDraft[] {
  const target = index + offset
  const next = [...drafts]
  if (target < 0 || target >= next.length) return next
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

/** 扁平模板 `{ name, ...参数 }`（定时任务模板、`POST /api/pipelines` 的 tasks）→ 编排草稿。 */
export function draftsFromTemplate(template: readonly unknown[]): TaskDraft[] {
  return template.flatMap((task) => {
    if (!isObject(task) || typeof task.name !== 'string') return []
    const { name, ...params } = task
    return [createDraft(name, params)]
  })
}

/** 编排草稿 → 与 `POST /api/pipelines` 的 tasks 相同的扁平模板，只含改动过的参数。 */
export function templateFromDrafts(drafts: readonly TaskDraft[]): Record<string, unknown>[] {
  return drafts.map(serializeTask)
}
