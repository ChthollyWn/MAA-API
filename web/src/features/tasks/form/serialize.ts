import { fieldLabel, isObject, schemaProperties, type TaskTypeSchema } from './schema'

export interface TaskDraft {
  id: string
  name: string
  params: Record<string, unknown>
}

/** 提交体只带任务类型与用户改动过的参数，不补默认值。 */
export function serializeTask(task: Pick<TaskDraft, 'name' | 'params'>): Record<string, unknown> {
  return { name: task.name, ...task.params }
}

export function nonZeroRisk(value: unknown): boolean {
  if (value === undefined || value === null || value === false || value === 0 || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (isObject(value)) return Object.keys(value).length > 0
  return true
}

/** 被改成非零 / 非空的 `x-risk` 字段，每项一行：「任务名 · 字段名：JSON 值」，用于提交前确认。 */
export function riskSummary(tasks: ReadonlyArray<Pick<TaskDraft, 'name' | 'params'>>, types: readonly TaskTypeSchema[]): string[] {
  const lines: string[] = []
  for (const task of tasks) {
    const type = types.find((candidate) => candidate.name === task.name)
    if (!type) continue
    for (const [name, schema] of Object.entries(schemaProperties(type.schema))) {
      if (schema['x-risk'] !== undefined && nonZeroRisk(task.params[name])) lines.push(`${type.label} · ${fieldLabel(schema, name)}：${JSON.stringify(task.params[name])}`)
    }
  }
  return lines
}
