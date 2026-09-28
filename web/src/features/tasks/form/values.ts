import { isObject, objectValue, schemaDefault, type JsonSchema } from './schema'

/** 「删除该键 / 该项」的标记：没有默认值的字段恢复默认就是从草稿里删掉，回到不提交的状态。 */
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

/** 结构比较：数组按元素、对象按键值（键顺序不影响结果），其余按 `Object.is`。 */
export function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((item, index) => sameValue(item, right[index]))
  }
  if (!isObject(left) || !isObject(right)) return false
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length && keys.every((key) => key in right && sameValue(left[key], right[key]))
}

export function setField(values: Record<string, unknown>, name: string, next: unknown): Record<string, unknown> {
  return setObjectValue(values, [name], next)
}

/**
 * 恢复默认要写入的值：有默认值就写回默认值（深拷贝），没有默认值才用 `UNSET` 删键。
 * `default: null` 与没有 `default` 一样视为没有已知默认值。
 */
export function resetValueFor(schema: JsonSchema): unknown | Unset {
  const fallback = schemaDefault(schema)
  if (!fallback) return UNSET
  return isObject(fallback.value) || Array.isArray(fallback.value) ? structuredClone(fallback.value) : fallback.value
}

/** 恢复默认：有默认值写回默认值，没有默认值的字段从草稿里删掉。 */
export function resetField(values: Record<string, unknown>, name: string, schema: JsonSchema): Record<string, unknown> {
  return setObjectValue(values, [name], resetValueFor(schema))
}

/** 明确留空：提交 `null`，只用于 schema 允许 null 的字段。 */
export function setFieldNull(values: Record<string, unknown>, name: string): Record<string, unknown> {
  return setObjectValue(values, [name], null)
}

/** 五档字段状态，见 docs/09 §7.5：默认 / 未设置 / 已修改 / 已设为空（+ 由 schema 决定的「必填」）。 */
export type FieldState = 'default' | 'unset' | 'modified' | 'null'

/** 缺键时看默认值：有 `default` 就是「默认」，没有就是「未设置」；显式 `null` 单独一档。 */
export function fieldState(values: Readonly<Record<string, unknown>>, name: string, schema: JsonSchema): FieldState {
  const value = values[name]
  if (value === null) return 'null'
  const fallback = schemaDefault(schema)
  if (value === undefined) return fallback ? 'default' : 'unset'
  return fallback && sameValue(value, fallback.value) ? 'default' : 'modified'
}

/** 与 schema 默认值不同（含明确留空）才算出「改过」，分组徽标与摘要都按它计。 */
export function isFieldModified(values: Readonly<Record<string, unknown>>, name: string, schema: JsonSchema): boolean {
  const state = fieldState(values, name, schema)
  return state === 'modified' || state === 'null'
}

export function countModified(values: Readonly<Record<string, unknown>>, schemas: Readonly<Record<string, JsonSchema>>): number {
  return Object.entries(schemas).filter(([name, schema]) => isFieldModified(values, name, schema)).length
}
