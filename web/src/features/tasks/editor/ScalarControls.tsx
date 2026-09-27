import { Minus, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { UNSET, enumOptions, getType, scalarEnumValue, schemaDefault, textValue, type JsonSchema } from '../form'
import { NUMBER_INPUT_CLASS, ariaProps, type ControlProps } from './control-props'

const NULL_OPTION = '__null__'
const USE_DEFAULT = '使用默认'
const NULL_TEXT = '已设为空'

function knownDefault<T>(schema: JsonSchema, guard: (value: unknown) => value is T): T | undefined {
  const fallback = schemaDefault(schema)
  return fallback && guard(fallback.value) ? fallback.value : undefined
}

const isBoolean = (value: unknown): value is boolean => typeof value === 'boolean'
const isNumber = (value: unknown): value is number => typeof value === 'number'
const isString = (value: unknown): value is string => typeof value === 'string'

/** 布尔开关。没有已知默认值且未设置时滑块居中，表示交给内核决定。 */
export function SwitchControl({ id, schema, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const effective = isNull ? undefined : isBoolean(value) ? value : knownDefault(schema, isBoolean)
  return (
    <div className="flex shrink-0 items-center gap-2">
      {isNull ? <span className="text-footnote text-muted-foreground">{NULL_TEXT}</span> : null}
      <Switch
        id={id}
        checked={effective === true}
        data-unset={effective === undefined || undefined}
        className="data-[unset=true]:[&>span]:translate-x-[0.5625rem]"
        disabled={disabled}
        onCheckedChange={(checked) => onChange(checked)}
        {...ariaProps({ invalid, describedBy })}
      />
    </div>
  )
}

export function SelectControl({ id, schema, value, present, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const options = enumOptions(schema)
  const fallback = schemaDefault(schema)
  const defaultLabel = fallback ? options.find((option) => Object.is(option.value, fallback.value))?.label : undefined
  const currentKey = isNull ? NULL_OPTION : present ? JSON.stringify(value) : ''
  const unlisted = present && !isNull && !options.some((option) => option.key === currentKey)
  return (
    <NativeSelect
      id={id}
      value={currentKey}
      disabled={disabled}
      {...ariaProps({ invalid, describedBy })}
      onChange={(event) => {
        const next = event.target.value
        if (next === '') onChange(UNSET)
        else if (next !== NULL_OPTION) onChange(options.find((option) => option.key === next)?.value ?? scalarEnumValue(next))
      }}
    >
      <option value="">{defaultLabel ? `默认：${defaultLabel}` : USE_DEFAULT}</option>
      {isNull ? <option value={NULL_OPTION} disabled>{NULL_TEXT}</option> : null}
      {unlisted ? <option value={currentKey}>{String(value)}</option> : null}
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
  if (min !== undefined && max !== undefined) return `范围 ${min} ~ ${max}`
  if (min !== undefined) return `最小 ${min}`
  if (max !== undefined) return `最大 ${max}`
  return null
}

/** 数字输入：−/+ 步进并夹在 schema 范围内；清空输入即恢复默认。 */
export function NumberControl({ id, label, schema, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const integer = getType(schema) === 'integer'
  const min = isNumber(schema.minimum) ? schema.minimum : undefined
  const max = isNumber(schema.maximum) ? schema.maximum : undefined
  const step = isNumber(schema.multipleOf) && schema.multipleOf > 0 ? schema.multipleOf : integer ? 1 : 0.05
  const fallback = knownDefault(schema, isNumber)
  const current = isNumber(value) ? value : undefined
  const base = current ?? fallback ?? 0
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
          placeholder={isNull ? NULL_TEXT : fallback !== undefined ? String(fallback) : USE_DEFAULT}
          disabled={disabled}
          {...ariaProps({ invalid, describedBy })}
          onChange={(event) => {
            if (event.target.value === '') onChange(UNSET)
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

function textPlaceholder(schema: JsonSchema, isNull: boolean): string {
  if (isNull) return NULL_TEXT
  const fallback = knownDefault(schema, isString)
  if (fallback) return fallback
  const example = Array.isArray(schema.examples) ? textValue(schema.examples[0]) : ''
  return example ? `例如 ${example}` : USE_DEFAULT
}

/** 字符串输入；清空即恢复默认。 */
export function TextControl({ id, schema, value, isNull, disabled, invalid, describedBy, onChange, multiline = false }: ControlProps & { multiline?: boolean }) {
  const shared = {
    id,
    value: isString(value) ? value : '',
    placeholder: textPlaceholder(schema, isNull),
    disabled,
    ...ariaProps({ invalid, describedBy }),
  }
  if (multiline) return <Textarea {...shared} onChange={(event) => onChange(event.target.value === '' ? UNSET : event.target.value)} />
  return <Input {...shared} onChange={(event) => onChange(event.target.value === '' ? UNSET : event.target.value)} />
}
