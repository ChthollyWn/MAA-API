import { Fragment, type ReactNode } from 'react'
import { KeyRound, Lock } from 'lucide-react'
import { StatusDot } from '@/components/ui/badge'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { SwitchRow } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { ChipListInput, ExpandableNote, NumberInput, type ChipParseResult } from './controls'
import {
  fieldDomId,
  hotActionLabel,
  isListType,
  parseFieldValue,
  rangeLabel,
  sourceLabel,
  type SettingField,
  type SettingState,
} from './schema'

/** schema 没有的补充说明。 */
const FIELD_NOTES: Readonly<Record<string, string>> = {
  'agent.auto_diagnose_failures': '流水线失败或内核崩溃时，自动让 Agent 分析日志（默认开启）',
}

const SEGMENTED_LIMIT = 3
const SEGMENTED_LABEL_LENGTH = 12

function secretStatus(state: SettingState | undefined): string {
  if (!state?.configured) return '未配置'
  return state.masked ? '已配置，已脱敏' : '已配置，页面已隐藏'
}

/** 字段下方的小字：密钥状态、生效方式与来源。 */
function MetaLine({ field, state }: { field: SettingField; state: SettingState | undefined }) {
  const parts: ReactNode[] = []
  if (field.sensitive && !field.readonly) {
    parts.push(
      <span className="inline-flex items-center gap-1 align-bottom">
        <KeyRound aria-hidden="true" className="size-3.5 shrink-0" />
        {secretStatus(state)}
      </span>,
    )
  }
  if (field.type === 'integer[]') {
    const range = rangeLabel(field.minimum, field.maximum)
    if (range) parts.push(<span>每项{range}</span>)
  }
  parts.push(<span>{field.readonly ? '只能在配置文件或环境变量中修改' : hotActionLabel(field.hotAction)}</span>)
  parts.push(<span>来源：{sourceLabel(state?.source ?? 'unknown')}</span>)
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {index ? <span aria-hidden="true"> · </span> : null}
          {part}
        </Fragment>
      ))}
    </>
  )
}

function readonlyText(field: SettingField, state: SettingState | undefined): { text: string; mono: boolean } {
  const value = state?.value
  if (field.sensitive) return { text: state?.configured ? '已配置' : '未配置', mono: false }
  if (typeof value === 'boolean') return { text: value ? '已开启' : '已关闭', mono: false }
  if (Array.isArray(value)) return value.length ? { text: value.join('、'), mono: true } : { text: '空', mono: false }
  if (value === undefined || value === null || value === '') return { text: '未设置', mono: false }
  return { text: String(value), mono: true }
}

function ReadonlyRow({ field, state }: { field: SettingField; state: SettingState | undefined }) {
  const id = fieldDomId(field.key)
  const display = readonlyText(field, state)
  return (
    <div role="group" aria-labelledby={`${id}-label`} aria-describedby={`${id}-meta`} className="space-y-1 px-4 py-3.5">
      <div className="flex min-h-5 items-center justify-between gap-2">
        <span id={`${id}-label`} className="text-sm font-medium leading-5">{field.label}</span>
        <span className="inline-flex shrink-0 items-center gap-1 text-footnote text-muted-foreground">
          <Lock aria-hidden="true" className="size-3.5" />
          只读
        </span>
      </div>
      <p className={cn('break-all text-[0.9375rem]', display.mono && 'font-mono text-sm')}>{display.text}</p>
      <p id={`${id}-meta`} className="text-footnote text-muted-foreground">
        <MetaLine field={field} state={state} />
      </p>
    </div>
  )
}

function parseListItem(field: SettingField, text: string): ChipParseResult {
  if (field.type !== 'integer[]') return { value: text }
  const parsed = Number(text)
  if (!Number.isInteger(parsed)) return { error: '请输入整数。' }
  const checked = parseFieldValue(field, [parsed])
  return checked.error !== undefined ? { error: checked.error } : { value: parsed }
}

/** 选项少且短、当前值也在选项里时用分段控件，否则用原生下拉。 */
function segmentable(field: SettingField, serverValue: string): boolean {
  return (
    field.options.length <= SEGMENTED_LIMIT &&
    field.options.every((option) => option.length <= SEGMENTED_LABEL_LENGTH) &&
    field.options.includes(serverValue)
  )
}

interface ControlProps {
  field: SettingField
  state: SettingState | undefined
  value: unknown
  id: string
  invalid: boolean
  describedBy: string
  disabled: boolean
  onChange: (value: unknown) => void
}

function SettingControl({ field, state, value, id, invalid, describedBy, disabled, onChange }: ControlProps) {
  const aria = { 'aria-invalid': invalid || undefined, 'aria-describedby': describedBy || undefined }
  if (field.options.length) {
    const serverValue = typeof state?.value === 'string' ? state.value : ''
    const current = typeof value === 'string' ? value : serverValue
    if (segmentable(field, serverValue)) {
      return (
        <SegmentedControl
          ariaLabel={field.label}
          value={current}
          onValueChange={onChange}
          options={field.options.map((option) => ({ value: option, label: option }))}
          fullWidth
          disabled={disabled}
        />
      )
    }
    return (
      <NativeSelect id={id} value={current} disabled={disabled} onChange={(event) => onChange(event.target.value)} {...aria}>
        {field.options.includes(serverValue) ? null : <option value={serverValue}>{serverValue || '未设置'}</option>}
        {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
      </NativeSelect>
    )
  }
  if (field.type === 'integer' || field.type === 'number') {
    return (
      <NumberInput
        id={id}
        label={field.label}
        value={typeof value === 'string' ? value : String(value ?? '')}
        integer={field.type === 'integer'}
        minimum={field.minimum}
        maximum={field.maximum}
        disabled={disabled}
        invalid={invalid}
        describedBy={describedBy}
        onChange={onChange}
      />
    )
  }
  if (isListType(field.type)) {
    return (
      <ChipListInput
        id={id}
        label={field.label}
        values={Array.isArray(value) ? (value as (string | number)[]) : []}
        parse={(text) => parseListItem(field, text)}
        inputMode={field.type === 'integer[]' ? 'numeric' : 'text'}
        disabled={disabled}
        invalid={invalid}
        describedBy={describedBy}
        onChange={onChange}
      />
    )
  }
  return (
    <Input
      id={id}
      type={field.sensitive ? 'password' : 'text'}
      value={typeof value === 'string' ? value : String(value ?? '')}
      autoComplete={field.sensitive ? 'new-password' : 'off'}
      autoCapitalize="none"
      autoCorrect="off"
      spellCheck={false}
      placeholder={field.sensitive ? (state?.configured ? '留空则保留当前值' : '尚未配置') : '未设置'}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      {...aria}
    />
  )
}

export interface SettingFieldRowProps {
  field: SettingField
  state: SettingState | undefined
  value: unknown
  changed: boolean
  error?: string
  disabled: boolean
  onChange: (value: unknown) => void
}

/** 一个 schema 字段：标签、唯一的控件、错误，以及生效方式与来源；只读字段只展示当前值。 */
export function SettingFieldRow({ field, state, value, changed, error, disabled, onChange }: SettingFieldRowProps) {
  if (field.readonly) return <ReadonlyRow field={field} state={state} />

  const id = fieldDomId(field.key)
  const ids = { meta: `${id}-meta`, error: `${id}-error`, note: `${id}-note` }
  const note = FIELD_NOTES[field.key]
  const changedTag = changed ? (
    <span className="inline-flex items-center gap-1 text-footnote font-medium text-primary-text">
      <StatusDot tone="primary" />
      已修改
    </span>
  ) : null

  if (field.type === 'boolean') {
    return (
      <div className="space-y-1 px-4 py-2">
        <SwitchRow
          id={id}
          label={field.label}
          description={<MetaLine field={field} state={state} />}
          checked={value === true}
          onCheckedChange={onChange}
          disabled={disabled}
          aside={changedTag}
        />
        {error ? <p id={ids.error} className="text-footnote font-medium text-danger">{error}</p> : null}
        {note ? <div className="pb-1.5"><ExpandableNote id={ids.note} text={note} /></div> : null}
      </div>
    )
  }

  const describedBy = [error ? ids.error : '', ids.meta, note ? ids.note : ''].filter(Boolean).join(' ')
  const segmented = field.options.length > 0 && segmentable(field, typeof state?.value === 'string' ? state.value : '')
  return (
    <div className="space-y-1.5 px-4 py-3.5">
      <Field
        label={field.label}
        htmlFor={segmented ? undefined : id}
        labelAside={changedTag}
        description={<MetaLine field={field} state={state} />}
        descriptionId={ids.meta}
        error={error}
        errorId={ids.error}
      >
        <SettingControl
          field={field}
          state={state}
          value={value}
          id={id}
          invalid={Boolean(error)}
          describedBy={describedBy}
          disabled={disabled}
          onChange={onChange}
        />
      </Field>
      {note ? <ExpandableNote id={ids.note} text={note} /> : null}
    </div>
  )
}
