import { fieldLabel, isObject, schemaDefault, schemaProperties, type TaskTypeSchema } from './schema'
import { sameValue } from './values'

export interface TaskDraft {
  id: string
  name: string
  params: Record<string, unknown>
}

/** 提交体 = 任务类型 + 草稿里的全部参数；M17 起草稿含预填的 schema 默认值（所见即所提交）。 */
export function serializeTask(task: Pick<TaskDraft, 'name' | 'params'>): Record<string, unknown> {
  return { name: task.name, ...task.params }
}

export function nonZeroRisk(value: unknown): boolean {
  if (value === undefined || value === null || value === false || value === 0 || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (isObject(value)) return Object.keys(value).length > 0
  return true
}

/**
 * 被用户改成非零 / 非空的 `x-risk` 字段，每项一行：「任务名 · 字段名：JSON 值」，用于提交前确认。
 *
 * M17 起草稿会预填 schema 默认值，因此**与默认值相同的值不算「用户选的」**：`Mall.shopping`
 * 与 `Roguelike.investment_enabled` 的内核默认值就是 `true`，不加这条判断的话，新建一个
 * 什么都不改的采购/肉鸽任务也会弹「确认消耗资源」，把确认训练成无脑点过。
 */
export function riskSummary(tasks: ReadonlyArray<Pick<TaskDraft, 'name' | 'params'>>, types: readonly TaskTypeSchema[]): string[] {
  const lines: string[] = []
  for (const task of tasks) {
    const type = types.find((candidate) => candidate.name === task.name)
    if (!type) continue
    for (const [name, schema] of Object.entries(schemaProperties(type.schema))) {
      if (schema['x-risk'] === undefined) continue
      const value = task.params[name]
      if (!nonZeroRisk(value)) continue
      const fallback = schemaDefault(schema)
      if (fallback && sameValue(value, fallback.value)) continue
      lines.push(`${type.label} · ${fieldLabel(schema, name)}：${JSON.stringify(value)}`)
    }
  }
  return lines
}
