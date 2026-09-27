import type { JsonSchema, Unset } from '../form'

/** 每个字段控件拿到的公共参数。 */
export interface ControlProps {
  /** 主控件的 DOM id，字段标签的 htmlFor 指向它。 */
  id: string
  /** 字段标签元素的 id，组合控件用 aria-labelledby 指向它。 */
  labelId: string
  label: string
  /** 已经过 normalizeSchema 的字段 schema。 */
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  /** 草稿里有这个键：用户改动过，包括明确留空。 */
  present: boolean
  isNull: boolean
  disabled: boolean
  invalid: boolean
  describedBy?: string
  onChange: (next: unknown | Unset) => void
}

/** 数字框旁已有自己的 −/+ 或空间很窄，隐藏浏览器自带的微调箭头。 */
export const NUMBER_INPUT_CLASS =
  'text-center tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none'

export function ariaProps({ invalid, describedBy }: Pick<ControlProps, 'invalid' | 'describedBy'>) {
  return { 'aria-invalid': invalid || undefined, 'aria-describedby': describedBy || undefined }
}
