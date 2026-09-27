import { create } from 'zustand'
import type { TaskDraft } from '../form'
import { randomId } from './drafts'

/** 任务 id → 字段名 → 错误消息（来自 `mapFieldIssues`）。 */
export type TaskErrors = Record<string, Record<string, string>>

interface ComposerState {
  title: string
  drafts: TaskDraft[]
  errors: TaskErrors
  /** 同一份编排的提交与重试共用一个请求编号；编排一改就作废。 */
  idempotencyKey: string | null
  setTitle: (title: string) => void
  setDrafts: (drafts: TaskDraft[]) => void
  setErrors: (errors: TaskErrors) => void
  /** 取当前编排的请求编号，没有就生成一个。 */
  takeIdempotencyKey: () => string
  reset: () => void
}

function changedFields(previous: Record<string, unknown>, next: Record<string, unknown>): Set<string> {
  const names = new Set([...Object.keys(previous), ...Object.keys(next)])
  return new Set([...names].filter((name) => !Object.is(previous[name], next[name])))
}

/** 改过的字段与删掉的任务不再显示旧错误；其余错误保留到下一次提交。 */
function pruneErrors(errors: TaskErrors, previous: readonly TaskDraft[], next: readonly TaskDraft[]): TaskErrors {
  const before = new Map(previous.map((draft) => [draft.id, draft]))
  const result: TaskErrors = {}
  for (const draft of next) {
    const current = errors[draft.id]
    if (!current) continue
    const old = before.get(draft.id)
    const changed = old ? changedFields(old.params, draft.params) : new Set<string>()
    const kept = Object.fromEntries(Object.entries(current).filter(([field]) => !changed.has(field)))
    if (Object.keys(kept).length > 0) result[draft.id] = kept
  }
  return result
}

/**
 * 编排器草稿：只存在内存里，在新建、队列、历史之间切换时保留，提交成功后清空。
 * 定时任务编辑器复用 `TaskListEditor` 时用自己的状态，不共享这里。
 */
export const useComposerStore = create<ComposerState>((set, get) => ({
  title: '',
  drafts: [],
  errors: {},
  idempotencyKey: null,
  setTitle: (title) => set({ title, idempotencyKey: null }),
  setDrafts: (drafts) => set((state) => ({ drafts, errors: pruneErrors(state.errors, state.drafts, drafts), idempotencyKey: null })),
  setErrors: (errors) => set({ errors }),
  takeIdempotencyKey: () => {
    const key = get().idempotencyKey ?? randomId()
    set({ idempotencyKey: key })
    return key
  },
  reset: () => set({ title: '', drafts: [], errors: {}, idempotencyKey: null }),
}))
