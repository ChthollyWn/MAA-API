import { Minus, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { enumOptions, getType, resetValueFor, scalarEnumValue, schemaDefault, textValue, type JsonSchema } from '../form'
import { NUMBER_INPUT_CLASS, ariaProps, type ControlProps } from './control-props'

const NULL_OPTION = '__null__'
/** 没有默认值的字段没填时的占位文案（M17 起统一叫「未设置」）。 */
const UNSET_TEXT = '未设置'
const NULL_TEXT = '已设为空'

function knownDefault<T>(schema: JsonSchema, guard: (value: unknown) => value is T): T | undefined {
  const fallback = schemaDefault(schema)
  return fallback && guard(fallback.value) ? fallback.value : undefined
}

const isBoolean = (value: unknown): value is boolean => typeof value === 'boolean'
const isNumber = (value: unknown): value is number => typeof value === 'number'
const isString = (value: unknown): value is string => typeof value === 'string'

/**
 * 控件显示的值：草稿里有就用草稿的；草稿里没有（旧草稿还没有物化过）时显示 schema 默认值，
 * 于是有默认值的控件不会看起来是空的；明确留空（null）时什么都不显示。
 */
function effectiveValue<T>(value: unknown, isNull: boolean, fallback: T | undefined, guard: (input: unknown) => input is T): T | undefined {
  if (isNull) return undefined
  return guard(value) ? value : fallback
}

/** 布尔开关。只有没有默认值的布尔字段（以及明确留空）才让滑块居中，有默认值的开关直接显示开/关。 */
export function SwitchControl({ id, schema, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const effective = effectiveValue(value, isNull, knownDefault(schema, isBoolean), isBoolean)
  return (
    <div className="flex shrink-0 items-center gap-2">
      {isNull ? <span className="text-footnote text-muted-foreground">{NULL_TEXT}</span> : null}
      <Switch
        id={id}
        checked={effective === true}
        data-unset={(isNull || effective === undefined) || undefined}
        className="data-[unset=true]:[&>span]:translate-x-[0.5625rem]"
        disabled={disabled}
        onCheckedChange={(checked) => onChange(checked)}
        {...ariaProps({ invalid, describedBy })}
      />
    </div>
  )
}

/** 原生下拉。有默认值时不再有空选项（值总在草稿里）；没有默认值时第一项是「未设置」。 */
export function SelectControl({ id, schema, value, present, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const options = enumOptions(schema)
  const fallback = schemaDefault(schema)
  // 旧草稿还没物化时显示默认值，而不是落到列表里的第一个选项。
  const currentKey = isNull ? NULL_OPTION : present ? JSON.stringify(value) : fallback ? JSON.stringify(fallback.value) : ''
  const displayed = present && !isNull ? value : fallback?.value
  const unlisted = currentKey !== '' && currentKey !== NULL_OPTION && !options.some((option) => option.key === currentKey)
  return (
    <NativeSelect
      id={id}
      value={currentKey}
      disabled={disabled}
      {...ariaProps({ invalid, describedBy })}
      onChange={(event) => {
        const next = event.target.value
        if (next === '') onChange(resetValueFor(schema))
        else if (next !== NULL_OPTION) onChange(options.find((option) => option.key === next)?.value ?? scalarEnumValue(next))
      }}
    >
      {fallback ? null : <option value="">{UNSET_TEXT}</option>}
      {isNull ? <option value={NULL_OPTION} disabled>{NULL_TEXT}</option> : null}
      {unlisted ? <option value={currentKey}>{String(displayed)}</option> : null}
      {options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
    </NativeSelect>
  )
}

/** 选项少且短的枚举。未设置时不选中任何一项；选中后只能用「恢复默认」回到未设置。 */
export function SegmentedField({ label, schema, value, present, isNull, disabled, onChange }: ControlProps) {
  const options = enumOptions(schema)
  const fallback = schemaDefault(schema)
  const current = present && !isNull ? options.find((option) => Object.is(option.value, value))?.key ?? '' : ''
  const defaultLabel = !present && fallback ? options.find((option) => Object.is(option.value, fallback.value))?.label : undefined
  return (
    <div className="space-y-1.5">
      <SegmentedControl
        ariaLabel={label}
        value={current}
        fullWidth
        disabled={disabled}
        options={options.map((option) => ({ value: option.key, label: option.label }))}
        onValueChange={(key) => {
          const option = options.find((candidate) => candidate.key === key)
          if (option) onChange(option.value)
        }}
      />
      {isNull ? <p className="text-footnote text-muted-foreground">{NULL_TEXT}</p> : null}
      {defaultLabel ? <p className="text-footnote text-muted-foreground">默认：{defaultLabel}</p> : null}
    </div>
  )
}

function fractionDigits(step: number): number {
  const [, fraction = ''] = String(step).split('.')
  return fraction.length
}

function rangeText(min: number | undefined, max: number | undefined): string | null {
  if (min !== undefined && max !== undefined) return `范围 ${min}–${max}`
  if (min !== undefined) return `最小 ${min}`
  if (max !== undefined) return `最大 ${max}`
  return null
}

/** 数字输入：−/+ 步进并夹在 schema 范围内；清空输入 = 有默认值写回默认值，没有默认值才回到未设置。 */
export function NumberControl({ id, label, schema, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const integer = getType(schema) === 'integer'
  const min = isNumber(schema.minimum) ? schema.minimum : undefined
  const max = isNumber(schema.maximum) ? schema.maximum : undefined
  const step = isNumber(schema.multipleOf) && schema.multipleOf > 0 ? schema.multipleOf : integer ? 1 : 0.05
  const fallback = knownDefault(schema, isNumber)
  const current = effectiveValue(value, isNull, fallback, isNumber)
  const base = current ?? 0
  const clamp = (next: number) => Math.min(max ?? Number.POSITIVE_INFINITY, Math.max(min ?? Number.NEGATIVE_INFINITY, Number(next.toFixed(fractionDigits(step)))))
  const range = rangeText(min, max)
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <div className="flex items-center gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`减少${label}`}
          disabled={disabled || (current !== undefined && min !== undefined && current <= min)}
          onClick={() => onChange(clamp(base - step))}
        >
          <Minus aria-hidden="true" />
        </Button>
        <Input
          id={id}
          type="number"
          inputMode={integer ? 'numeric' : 'decimal'}
          className={`w-28 ${NUMBER_INPUT_CLASS}`}
          min={min}
          max={max}
          step={step}
          value={current ?? ''}
          placeholder={isNull ? NULL_TEXT : fallback === undefined ? UNSET_TEXT : undefined}
          disabled={disabled}
          {...ariaProps({ invalid, describedBy })}
          onChange={(event) => {
            if (event.target.value === '') onChange(resetValueFor(schema))
            else {
              const next = Number(event.target.value)
              if (Number.isFinite(next)) onChange(next)
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`增加${label}`}
          disabled={disabled || (current !== undefined && max !== undefined && current >= max)}
          onClick={() => onChange(clamp(base + step))}
        >
          <Plus aria-hidden="true" />
        </Button>
      </div>
      {range ? <span className="text-footnote tabular-nums text-muted-foreground">{range}</span> : null}
    </div>
  )
}

/** placeholder 只对没有默认值的字段有意义：有默认值的字段显示的是默认值本身。 */
function textPlaceholder(schema: JsonSchema, isNull: boolean, hasDefault: boolean): string | undefined {
  if (isNull) return NULL_TEXT
  if (hasDefault) return undefined
  const example = Array.isArray(schema.examples) ? textValue(schema.examples[0]) : ''
  return example ? `例如 ${example}` : UNSET_TEXT
}

/** 字符串输入；清空 = 有默认值写回默认值，没有默认值才回到未设置。 */
export function TextControl({ id, schema, value, isNull, disabled, invalid, describedBy, onChange, multiline = false }: ControlProps & { multiline?: boolean }) {
  const fallback = knownDefault(schema, isString)
  const current = effectiveValue(value, isNull, fallback, isString)
  const shared = {
    id,
    value: current ?? '',
    placeholder: textPlaceholder(schema, isNull, fallback !== undefined),
    disabled,
    ...ariaProps({ invalid, describedBy }),
  }
  const change = (text: string) => onChange(text === '' ? resetValueFor(schema) : text)
  if (multiline) return <Textarea {...shared} onChange={(event) => change(event.target.value)} />
  return <Input {...shared} onChange={(event) => change(event.target.value)} />
}
