import { useId } from 'react'
import { Chip } from '@/components/ui/chip'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SwitchRow } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import type { FormValue, ViewField as ViewFieldSpec } from './schema'

export interface ViewFieldProps {
  spec: ViewFieldSpec
  value: FormValue
  error: string | undefined
  disabled: boolean
  onChange: (value: FormValue) => void
}

function stringValue(value: FormValue): string {
  return typeof value === 'string' ? value : ''
}

/**
 * 一个视图字段：label、说明、控件与错误。
 *
 * 只渲染白名单控件（下拉、数字、单行/多行文本、开关），不把视图里的字符串
 * 当类名或事件处理。
 */
export function ViewField({ spec, value, error, disabled, onChange }: ViewFieldProps) {
  const id = useId()
  const descriptionId = spec.description ? `${id}-description` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [descriptionId, errorId].filter(Boolean).join(' ') || undefined

  if (spec.field === 'toggle') {
    return (
      <div className="space-y-1.5">
        <SwitchRow
          id={id}
          label={spec.label}
          description={spec.description ?? undefined}
          checked={value === true}
          disabled={disabled}
          onCheckedChange={(checked) => onChange(checked)}
        />
        {error ? <p id={errorId} className="text-footnote font-medium text-danger">{error}</p> : null}
      </div>
    )
  }

  let control
  if (spec.field === 'select' && spec.multiple) {
    const selected = Array.isArray(value) ? value : []
    control = (
      <div
        role="group"
        aria-label={spec.label}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        className="flex flex-wrap gap-2"
      >
        {spec.options.map((option) => (
          <Chip
            key={option.value}
            selected={selected.includes(option.value)}
            disabled={disabled}
            onClick={() => onChange(
              selected.includes(option.value)
                ? selected.filter((item) => item !== option.value)
                : [...selected, option.value],
            )}
          >
            {option.label}
          </Chip>
        ))}
      </div>
    )
  } else if (spec.field === 'select') {
    control = (
      <NativeSelect
        id={id}
        value={stringValue(value)}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="" disabled>选择一项</option>
        {spec.options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </NativeSelect>
    )
  } else if (spec.field === 'number') {
    control = (
      <div className="flex min-w-0 items-center gap-2">
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          value={typeof value === 'number' ? String(value) : stringValue(value)}
          min={spec.min ?? undefined}
          max={spec.max ?? undefined}
          step={spec.step ?? undefined}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
        />
        {spec.unit ? <span className="shrink-0 text-footnote text-muted-foreground">{spec.unit}</span> : null}
      </div>
    )
  } else if (spec.multiline) {
    control = (
      <Textarea
        id={id}
        value={stringValue(value)}
        maxLength={spec.maxLength}
        placeholder={spec.placeholder ?? undefined}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  } else {
    control = (
      <Input
        id={id}
        value={stringValue(value)}
        maxLength={spec.maxLength}
        placeholder={spec.placeholder ?? undefined}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }

  return (
    <Field
      label={spec.label}
      htmlFor={spec.field === 'select' && spec.multiple ? undefined : id}
      description={spec.description ?? undefined}
      descriptionId={descriptionId}
      error={error}
      errorId={errorId}
    >
      {control}
    </Field>
  )
}
