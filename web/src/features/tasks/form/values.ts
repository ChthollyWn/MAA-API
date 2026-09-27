import { objectValue } from './schema'

/** 「删除该键 / 该项」的标记：草稿只保存用户改动过的参数，恢复默认就是把键删掉。 */
export const UNSET = Symbol('unset')
export type Unset = typeof UNSET

export function setObjectValue(value: Record<string, unknown>, path: string[], next: unknown | Unset): Record<string, unknown> {
  const [head, ...rest] = path
  if (!head) return value
  const result = { ...value }
  if (rest.length === 0) {
    if (next === UNSET) delete result[head]
    else result[head] = next
    return result
  }
  const child = objectValue(result[head])
  const changed = setObjectValue(child, rest, next)
  if (Object.keys(changed).length === 0 && next === UNSET) delete result[head]
  else result[head] = changed
  return result
}

export function updateArrayItem(values: unknown[], index: number, next: unknown | Unset): unknown[] {
  const result = [...values]
  if (next === UNSET) result.splice(index, 1)
  else result[index] = next
  return result
}

/** 与相邻项交换位置；越界时原样返回。 */
export function moveArrayItem(values: unknown[], index: number, offset: -1 | 1): unknown[] {
  const target = index + offset
  if (target < 0 || target >= values.length) return values
  const result = [...values]
  ;[result[index], result[target]] = [result[target], result[index]]
  return result
}

export function setField(values: Record<string, unknown>, name: string, next: unknown): Record<string, unknown> {
  return setObjectValue(values, [name], next)
}

/** 恢复默认：删除该字段，提交时不再携带。 */
export function resetField(values: Record<string, unknown>, name: string): Record<string, unknown> {
  return setObjectValue(values, [name], UNSET)
}

/** 明确留空：提交 `null`，只用于 schema 允许 null 的字段。 */
export function setFieldNull(values: Record<string, unknown>, name: string): Record<string, unknown> {
  return setObjectValue(values, [name], null)
}

export type FieldState = 'default' | 'modified' | 'null'

export function fieldState(values: Record<string, unknown>, name: string): FieldState {
  const value = values[name]
  if (value === undefined) return 'default'
  return value === null ? 'null' : 'modified'
}

export function isFieldModified(values: Record<string, unknown>, name: string): boolean {
  return values[name] !== undefined
}

export function countModified(values: Record<string, unknown>, names: readonly string[]): number {
  return names.filter((name) => isFieldModified(values, name)).length
}
