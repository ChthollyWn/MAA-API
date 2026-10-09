import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Braces, Plus, X } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/ui/chip'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { SwitchRow } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  CHANNEL_FIELDS,
  CHANNEL_TYPES,
  NOTIFY_EVENTS,
  configFromFormValues,
  defaultChannelConfig,
  formValuesFromConfig,
  formatConfig,
  headerRow,
  headerRowsOf,
  isChannelType,
  isEmailAddress,
  parseConfigText,
  validateChannelForm,
  type Channel,
  type ChannelFieldSpec,
  type ChannelFieldValue,
  type ChannelFormValues,
  type ChannelType,
  type HeaderRow,
  type NotifyEventId,
} from './channel-config'
import { ChipListInput } from './controls'
import { errorText } from './format'
import { MASK, asRecord, type JsonRecord } from './schema'

const DEFAULT_TYPE: ChannelType = 'webhook'
const DEFAULT_EVENTS: readonly string[] = ['pipeline_completed']
const EVENT_ORDER: readonly string[] = NOTIFY_EVENTS.map((event) => event.value)

const TEXT_INPUT = { autoComplete: 'off', autoCapitalize: 'none', autoCorrect: 'off', spellCheck: false } as const

/** 服务端脱敏后的 `***`：地址、请求头与 JSON 三种编辑入口都沿用这一句。 */
const MASK_NOTE = '保持 *** 不变即沿用已保存的凭据'

interface EditorState {
  type: string
  name: string
  /** 表单载入时的配置：保留未改动的 `***` 与表单不认识的键。 */
  source: JsonRecord
  values: ChannelFormValues
  events: string[]
  enabled: boolean
  mode: 'form' | 'json'
  configText: string
}

function initialState(channel: Channel | null): EditorState {
  const type = channel?.type ?? DEFAULT_TYPE
  const known = isChannelType(type)
  const source = channel ? channel.config : known ? defaultChannelConfig(type) : {}
  return {
    type,
    name: channel?.name ?? '',
    source,
    values: known ? formValuesFromConfig(type, source) : {},
    events: [...(channel?.events ?? DEFAULT_EVENTS)],
    enabled: channel?.enabled ?? true,
    mode: known ? 'form' : 'json',
    configText: formatConfig(source),
  }
}

function sortEvents(events: readonly string[]): string[] {
  return [...EVENT_ORDER.filter((value) => events.includes(value)), ...events.filter((value) => !EVENT_ORDER.includes(value))]
}

function withoutKey(errors: Record<string, string>, key: string): Record<string, string> {
  if (!(key in errors)) return errors
  const next = { ...errors }
  delete next[key]
  return next
}

/** 服务端的配置校验错误带着每个字段的原因，一并显示，方便在 JSON 模式里定位。 */
function describeChannelError(error: unknown): string {
  const failure = toApiError(error)
  const reason = asRecord(failure.details).reason
  if (failure.code === 'NOTIFY_CONFIG_INVALID' && typeof reason === 'string' && reason) {
    try {
      const items: unknown = JSON.parse(reason)
      if (Array.isArray(items)) {
        const text = items
          .map((item) => {
            const record = asRecord(item)
            const location = Array.isArray(record.loc) ? record.loc.join('.') : ''
            return [location, typeof record.msg === 'string' ? record.msg : ''].filter(Boolean).join('：')
          })
          .filter(Boolean)
          .join('；')
        if (text) return `${failure.message}：${text}`
      }
    } catch {
      return `${failure.message}：${reason}`
    }
  }
  return errorText(error)
}

interface HeaderRowsEditorProps {
  id: string
  rows: HeaderRow[]
  disabled: boolean
  invalid: boolean
  describedBy?: string
  onChange: (rows: HeaderRow[]) => void
}

function HeaderRowsEditor({ id, rows, disabled, invalid, describedBy, onChange }: HeaderRowsEditorProps) {
  const update = (rowId: number, patch: Partial<HeaderRow>) => onChange(rows.map((row) => (row.id === rowId ? { ...row, ...patch } : row)))
  const aria = { 'aria-invalid': invalid || undefined, 'aria-describedby': describedBy }
  return (
    <div className="space-y-2">
      {rows.map((row, index) => (
        <div key={row.id} className="flex items-center gap-2">
          <Input
            id={index === 0 ? id : undefined}
            aria-label={`请求头 ${index + 1} 名称`}
            value={row.name}
            placeholder="名称"
            className="min-w-0 flex-[2] font-mono"
            disabled={disabled}
            {...TEXT_INPUT}
            {...aria}
            onChange={(event) => update(row.id, { name: event.target.value })}
          />
          <Input
            aria-label={`请求头 ${index + 1} 的值`}
            value={row.value}
            placeholder="值"
            className="min-w-0 flex-[3] font-mono"
            disabled={disabled}
            {...TEXT_INPUT}
            {...aria}
            onChange={(event) => update(row.id, { value: event.target.value })}
          />
          <Button type="button" variant="ghost" size="icon" aria-label={`删除请求头 ${index + 1}`} disabled={disabled} onClick={() => onChange(rows.filter((item) => item.id !== row.id))}>
            <X aria-hidden="true" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" id={rows.length ? undefined : id} disabled={disabled} onClick={() => onChange([...rows, headerRow()])}>
        <Plus aria-hidden="true" />
        添加请求头
      </Button>
    </div>
  )
}

interface ConfigFieldProps {
  spec: ChannelFieldSpec
  id: string
  value: ChannelFieldValue | undefined
  /** 载入的配置里这一项是 `***`。 */
  masked: boolean
  error?: string
  disabled: boolean
  onChange: (value: ChannelFieldValue) => void
}

function ConfigField({ spec, id, value, masked, error, disabled, onChange }: ConfigFieldProps) {
  const errorId = `${id}-error`
  const descriptionId = `${id}-description`
  const text = typeof value === 'string' ? value : ''
  const required = spec.required ? <span className="text-footnote text-muted-foreground">必填</span> : null

  if (spec.kind === 'switch') {
    return <SwitchRow id={id} label={spec.label} description={spec.description} checked={value === true} disabled={disabled} onCheckedChange={onChange} />
  }
  if (spec.kind === 'method') {
    return (
      <Field label={spec.label} error={error} errorId={errorId}>
        <SegmentedControl
          ariaLabel={spec.label}
          value={text || 'POST'}
          onValueChange={onChange}
          options={[{ value: 'POST', label: 'POST' }, { value: 'PUT', label: 'PUT' }]}
          fullWidth
          disabled={disabled}
        />
      </Field>
    )
  }

  let description = spec.description
  if (spec.kind === 'secret' && masked) description = '已保存，留空则保留原值'
  else if (spec.kind === 'url' && text.includes(MASK)) description = MASK_NOTE
  else if (spec.kind === 'headers' && headerRowsOf(value).some((row) => row.value === MASK)) description = MASK_NOTE
  const describedBy = [error ? errorId : '', description ? descriptionId : ''].filter(Boolean).join(' ') || undefined
  const aria = { 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy }
  const field = (control: ReactNode, labelled = true) => (
    <Field
      label={spec.label}
      htmlFor={labelled ? id : undefined}
      labelAside={required}
      description={description}
      descriptionId={descriptionId}
      error={error}
      errorId={errorId}
    >
      {control}
    </Field>
  )

  switch (spec.kind) {
    case 'headers':
      return field(<HeaderRowsEditor id={id} rows={headerRowsOf(value)} disabled={disabled} invalid={Boolean(error)} describedBy={describedBy} onChange={onChange} />, false)
    case 'list':
      return field(
        <ChipListInput
          id={id}
          label={spec.label}
          values={Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []}
          parse={(entry) => (spec.email && !isEmailAddress(entry) ? { error: '请填写有效的邮箱地址。' } : { value: entry })}
          inputMode={spec.email ? 'email' : 'text'}
          placeholder={spec.placeholder}
          disabled={disabled}
          invalid={Boolean(error)}
          describedBy={describedBy}
          onChange={(items) => onChange(items.map(String))}
        />,
      )
    case 'template':
      return field(
        <Textarea id={id} rows={3} value={text} className="font-mono" disabled={disabled} {...TEXT_INPUT} {...aria} onChange={(event) => onChange(event.target.value)} />,
      )
    default:
      return field(
        <Input
          id={id}
          type={spec.kind === 'secret' ? 'password' : spec.kind === 'url' ? 'url' : 'text'}
          inputMode={spec.kind === 'port' ? 'numeric' : spec.kind === 'url' ? 'url' : undefined}
          value={text}
          placeholder={spec.kind === 'secret' && masked ? '已保存' : spec.placeholder}
          className={spec.kind === 'port' ? 'w-32 tabular-nums' : undefined}
          disabled={disabled}
          {...TEXT_INPUT}
          autoComplete={spec.kind === 'secret' ? 'new-password' : 'off'}
          {...aria}
          onChange={(event) => onChange(event.target.value)}
        />,
      )
  }
}

export interface ChannelEditorProps {
  open: boolean
  /** 为空时新建。 */
  channel: Channel | null
  onOpenChange: (open: boolean) => void
  onSaved: (name: string, created: boolean) => void
}

/** 通知通道编辑器：按类型填写的表单，或「以 JSON 编辑」原始配置；两种模式来回切换数据一致。 */
export function ChannelEditor({ open, channel, onOpenChange, onSaved }: ChannelEditorProps) {
  const baseId = useId()
  const formId = `${baseId}-form`
  const fieldId = (key: string) => `${baseId}-${key}`
  const [state, setState] = useState(() => initialState(channel))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const type = isChannelType(state.type) ? state.type : null
  const jsonMode = state.mode === 'json' || !type

  const patch = (next: Partial<EditorState>) => setState((current) => ({ ...current, ...next }))

  const changeType = (next: string) => {
    if (!isChannelType(next)) return
    const source = channel && channel.type === next ? channel.config : defaultChannelConfig(next)
    patch({ type: next, source, values: formValuesFromConfig(next, source), configText: formatConfig(source) })
    setErrors((current): Record<string, string> => (current.name ? { name: current.name } : {}))
    setSubmitError(null)
  }

  const changeValue = (key: string, value: ChannelFieldValue) => {
    setState((current) => ({ ...current, values: { ...current.values, [key]: value } }))
    setErrors((current) => withoutKey(current, key))
  }

  const toJson = () => {
    if (!type) return
    patch({ mode: 'json', configText: formatConfig(configFromFormValues(type, state.values, state.source)) })
    setErrors((current) => withoutKey(current, 'config'))
  }

  const toForm = () => {
    if (!type) return
    const parsed = parseConfigText(state.configText)
    if (parsed.error !== undefined) {
      setErrors((current) => ({ ...current, config: parsed.error }))
      return
    }
    patch({ mode: 'form', source: parsed.config, values: formValuesFromConfig(type, parsed.config) })
    setErrors((current) => withoutKey(current, 'config'))
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving) return
    const nextErrors: Record<string, string> = {}
    const name = state.name.trim()
    if (!name) nextErrors.name = '请填写通道名称。'
    else if (name.length > 64) nextErrors.name = '请把名称控制在 64 个字符以内。'
    let config: JsonRecord | undefined
    if (!type) {
      nextErrors.type = '请选择通道类型。'
    } else if (state.mode === 'json') {
      const parsed = parseConfigText(state.configText)
      if (parsed.error !== undefined) nextErrors.config = parsed.error
      else config = parsed.config
    } else {
      Object.assign(nextErrors, validateChannelForm(type, state.values, state.source))
      config = configFromFormValues(type, state.values, state.source)
    }
    setErrors(nextErrors)
    setSubmitError(null)
    const firstInvalid = Object.keys(nextErrors)[0]
    if (firstInvalid || !type || !config) {
      if (firstInvalid) document.getElementById(fieldId(firstInvalid))?.focus()
      return
    }

    const body = { type, name, config, events: sortEvents(state.events) as NotifyEventId[], enabled: state.enabled }
    setSaving(true)
    try {
      const result = channel
        ? await api.PUT('/api/notifications/channels/{channel_id}', { params: { path: { channel_id: channel.id } }, body })
        : await api.POST('/api/notifications/channels', { body })
      if (result.error) throw toApiError(result.error, result.response)
      onOpenChange(false)
      onSaved(name, !channel)
    } catch (caught) {
      if (toApiError(caught).code === 'NOTIFY_CHANNEL_CONFLICT') {
        setErrors({ name: '同类型的通道里已经有这个名称，请换一个。' })
        document.getElementById(fieldId('name'))?.focus()
      } else {
        setSubmitError(describeChannelError(caught))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title={channel ? '编辑通知通道' : '添加通知通道'}
      description={channel ? undefined : '选择类型并填写连接信息，保存后可以发测试通知'}
      mobile="full"
      size="md"
      footer={(
        <div className="flex gap-2 lg:justify-end">
          <Button type="button" variant="outline" className="flex-1 lg:flex-none" onClick={() => onOpenChange(false)}>取消</Button>
          <Button type="submit" form={formId} className="flex-1 lg:flex-none" loading={saving}>保存</Button>
        </div>
      )}
    >
      <form id={formId} noValidate onSubmit={(event) => void submit(event)} className="space-y-6 pt-1">
        <div className="space-y-4">
          <Field label="类型" htmlFor={fieldId('type')} error={errors.type} errorId={`${fieldId('type')}-error`}>
            <NativeSelect
              id={fieldId('type')}
              value={state.type}
              disabled={saving}
              aria-invalid={errors.type ? true : undefined}
              onChange={(event) => changeType(event.target.value)}
            >
              {type ? null : <option value={state.type}>{state.type || '未知类型'}</option>}
              {CHANNEL_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </NativeSelect>
          </Field>
          <Field label="名称" htmlFor={fieldId('name')} labelAside={<span className="text-footnote text-muted-foreground">必填</span>} error={errors.name} errorId={`${fieldId('name')}-error`}>
            <Input
              id={fieldId('name')}
              value={state.name}
              maxLength={64}
              placeholder="例如 手机推送"
              autoComplete="off"
              disabled={saving}
              aria-invalid={errors.name ? true : undefined}
              aria-describedby={errors.name ? `${fieldId('name')}-error` : undefined}
              onChange={(event) => {
                patch({ name: event.target.value })
                setErrors((current) => withoutKey(current, 'name'))
              }}
            />
          </Field>
        </div>

        <section aria-labelledby={`${baseId}-config-title`} className="space-y-3">
          <div className="flex min-h-9 items-center justify-between gap-2">
            <h3 id={`${baseId}-config-title`} className="text-[0.9375rem] font-semibold">连接配置</h3>
            {type ? (
              <Button type="button" variant="ghost" size="sm" className="-mr-2 text-primary-text" onClick={jsonMode ? toForm : toJson}>
                <Braces aria-hidden="true" />
                {jsonMode ? '使用表单编辑' : '以 JSON 编辑'}
              </Button>
            ) : null}
          </div>
          {jsonMode ? (
            <Field
              label="配置 JSON"
              htmlFor={fieldId('config')}
              description={state.configText.includes(MASK) ? MASK_NOTE : '填写一个 JSON 对象，字段与表单一致'}
              descriptionId={`${fieldId('config')}-description`}
              error={errors.config}
              errorId={`${fieldId('config')}-error`}
            >
              <Textarea
                id={fieldId('config')}
                rows={10}
                value={state.configText}
                className="font-mono"
                disabled={saving}
                {...TEXT_INPUT}
                aria-invalid={errors.config ? true : undefined}
                aria-describedby={[errors.config ? `${fieldId('config')}-error` : '', `${fieldId('config')}-description`].filter(Boolean).join(' ')}
                onChange={(event) => {
                  patch({ configText: event.target.value })
                  setErrors((current) => withoutKey(current, 'config'))
                }}
              />
            </Field>
          ) : (
            <div className="space-y-4">
              {CHANNEL_FIELDS[type].map((spec) => (
                <ConfigField
                  key={`${type}-${spec.key}`}
                  spec={spec}
                  id={fieldId(spec.key)}
                  value={state.values[spec.key]}
                  masked={state.source[spec.key] === MASK}
                  error={errors[spec.key]}
                  disabled={saving}
                  onChange={(value) => changeValue(spec.key, value)}
                />
              ))}
            </div>
          )}
        </section>

        <fieldset>
          <legend className="text-[0.9375rem] font-semibold">订阅事件</legend>
          <p className="mt-0.5 text-footnote text-muted-foreground">选中的事件发生时发送通知，都不选时只能发送测试通知。</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {NOTIFY_EVENTS.map((event) => {
              const selected = state.events.includes(event.value)
              return (
                <Chip
                  key={event.value}
                  selected={selected}
                  className="h-11"
                  disabled={saving}
                  onClick={() => patch({ events: selected ? state.events.filter((value) => value !== event.value) : sortEvents([...state.events, event.value]) })}
                >
                  {event.label}
                </Chip>
              )
            })}
          </div>
        </fieldset>

        <SwitchRow
          label="启用此通道"
          description="停用后保留配置，但不再发送通知"
          checked={state.enabled}
          disabled={saving}
          onCheckedChange={(checked) => patch({ enabled: checked })}
        />

        {submitError ? <InlineAlert tone="danger" title="保存失败">{submitError}</InlineAlert> : null}
      </form>
    </ResponsiveSheet>
  )
}
