import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react'
import {
  AlertTriangle,
  Check,
  KeyRound,
  LoaderCircle,
  RotateCcw,
  Save,
} from 'lucide-react'
import { api } from '@/api/client'
import { useAuth } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NotificationChannels } from './NotificationChannels'

type JsonRecord = Record<string, unknown>

type SettingField = {
  key: string
  label: string
  group: string
  type: string
  minimum?: number
  maximum?: number
  options: string[]
  sensitive: boolean
  readonly: boolean
  hotAction: string
}

type SettingState = {
  value: unknown
  source: string
  configured: boolean
  masked: boolean
}

type RequestResult = { data?: unknown; error?: unknown }

const MASK = '***'

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as JsonRecord)
    : {}
}

function settingSchema(payload: unknown): SettingField[] {
  const root = asRecord(payload)
  const items = Array.isArray(root.items) ? root.items : []
  return items.flatMap((raw): SettingField[] => {
    const item = asRecord(raw)
    if (typeof item.key !== 'string') return []
    return [{
      key: item.key,
      label: typeof item.label === 'string' ? item.label : item.key,
      group: typeof item.group === 'string' ? item.group : '其他',
      type: typeof item.type === 'string' ? item.type : 'string',
      minimum: typeof item.minimum === 'number' ? item.minimum : undefined,
      maximum: typeof item.maximum === 'number' ? item.maximum : undefined,
      options: Array.isArray(item.enum) ? item.enum.filter((entry): entry is string => typeof entry === 'string') : [],
      sensitive: item.sensitive === true,
      readonly: item.readonly === true,
      hotAction: typeof item.hot_action === 'string' ? item.hot_action : 'none',
    }]
  })
}

function settingEntries(payload: unknown, fields: SettingField[]): Record<string, SettingState> {
  const root = asRecord(payload)
  const candidate = root.items ?? root.settings ?? root.values ?? root
  const entries: Record<string, SettingState> = {}
  const pairs: Array<[string, unknown]> = Array.isArray(candidate)
    ? candidate.flatMap((item) => {
        const record = asRecord(item)
        return typeof record.key === 'string' ? [[record.key, item]] : []
      })
    : Object.entries(asRecord(candidate))
  const byKey = new Map(fields.map((field) => [field.key, field]))

  for (const [key, raw] of pairs) {
    const record = asRecord(raw)
    const hasValue = Object.prototype.hasOwnProperty.call(record, 'value')
    const value = hasValue ? record.value : raw
    const field = byKey.get(key)
    const sensitive = field?.sensitive === true || record.sensitive === true
    const configured = value !== undefined && value !== null && value !== ''
    const masked = value === MASK
    entries[key] = {
      // Never keep a returned secret in React state. The API should mask it,
      // and the UI still hides it if a server returns an unmasked value.
      value: sensitive ? (configured ? MASK : '') : value,
      source: typeof record.source === 'string' ? record.source : 'unknown',
      configured,
      masked: sensitive && masked,
    }
  }
  return entries
}

function displaySource(source: string): string {
  const names: Record<string, string> = {
    env: '环境变量',
    db: '数据库覆盖',
    yaml: '配置文件',
    default: '默认值',
  }
  return names[source] ?? (source === 'unknown' ? '来源未知' : source)
}

function displayHotAction(action: string): string {
  if (action === 'reconnect') return '保存后会尝试重新连接设备。'
  if (action === 'restart_required') return '更改后需要重启服务或相关进程。'
  if (action === 'none') return '保存后立即生效。'
  return `生效方式：${action}`
}

function requestError(error: unknown): { code: string; message: string; applied: boolean } {
  const root = asRecord(error)
  const detail = asRecord(root.detail)
  const nested = asRecord(root.error)
  const details = asRecord(root.details ?? detail.details ?? nested.details)
  return {
    code: String(root.code ?? detail.code ?? nested.code ?? ''),
    message: String(root.message ?? detail.message ?? nested.message ?? '请求失败，请检查连接后重试。'),
    applied: details.applied === true,
  }
}

function parseFieldValue(field: SettingField, raw: unknown): { value?: unknown; error?: string } {
  const value = typeof raw === 'string' ? raw.trim() : raw
  if (field.type === 'integer' || field.type === 'number') {
    if (value === '') return { error: '请填写数值。' }
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) return { error: '请输入有效数值。' }
    if (field.type === 'integer' && !Number.isInteger(parsed)) return { error: '请输入整数。' }
    if (field.minimum !== undefined && parsed < field.minimum) return { error: `不能小于 ${field.minimum}。` }
    if (field.maximum !== undefined && parsed > field.maximum) return { error: `不能大于 ${field.maximum}。` }
    return { value: parsed }
  }
  if (field.type === 'integer[]') {
    if (typeof value !== 'string' || !value) return { error: '请填写至少一个整数，并用逗号分隔。' }
    const parts = value.split(',').map((part) => part.trim())
    const values = parts.map(Number)
    if (parts.some((part) => !part) || values.some((part) => !Number.isInteger(part))) {
      return { error: '请使用逗号分隔的整数。' }
    }
    if (field.minimum !== undefined && values.some((part) => part < field.minimum!)) {
      return { error: `每个值不能小于 ${field.minimum}。` }
    }
    if (field.maximum !== undefined && values.some((part) => part > field.maximum!)) {
      return { error: `每个值不能大于 ${field.maximum}。` }
    }
    return { value: values }
  }
  if (field.type === 'boolean') return { value: value === true || value === 'true' }
  if (typeof value === 'string' && field.options.length && !field.options.includes(value)) {
    return { error: '请选择列表中的有效选项。' }
  }
  return { value }
}

function fieldInputValue(field: SettingField, value: unknown): string {
  if (field.type === 'integer[]') return Array.isArray(value) ? value.join(', ') : ''
  if (value === null || value === undefined) return ''
  return String(value)
}

export default function SettingsPage() {
  const [fields, setFields] = useState<SettingField[]>([])
  const [values, setValues] = useState<Record<string, SettingState>>({})
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [dirty, setDirty] = useState<Set<string>>(() => new Set())
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resettingGroup, setResettingGroup] = useState<string | null>(null)
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string; repair: boolean } | null>(null)
  const [localToken, setLocalToken] = useState('')
  const [tokenSaved, setTokenSaved] = useState(false)

  const loadSettings = useCallback(async () => {
    setLoading(true)
    setMessage(null)
    try {
      const [schemaResult, settingsResult] = await Promise.all([
        api.GET('/api/settings/schema'),
        api.GET('/api/settings'),
      ])
      const schemaResponse = schemaResult as unknown as RequestResult
      const settingsResponse = settingsResult as unknown as RequestResult
      if (schemaResponse.error) throw schemaResponse.error
      if (settingsResponse.error) throw settingsResponse.error
      const nextFields = settingSchema(schemaResponse.data)
      const nextValues = settingEntries(settingsResponse.data, nextFields)
      setFields(nextFields)
      setValues(nextValues)
      const nextDraft: Record<string, unknown> = {}
      for (const field of nextFields) {
        // A sensitive value is represented only by the mask and remains blank
        // in the editor until the user explicitly enters a replacement.
        const state = nextValues[field.key]
        nextDraft[field.key] = field.sensitive ? '' : state?.value ?? ''
      }
      setDraft(nextDraft)
      setDirty(new Set())
      setErrors({})
    } catch (error) {
      setMessage({ kind: 'error', text: requestError(error).message, repair: false })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSettings()
  }, [loadSettings])

  const groups = useMemo(() => {
    const grouped = new Map<string, SettingField[]>()
    for (const field of fields) {
      const list = grouped.get(field.group) ?? []
      list.push(field)
      grouped.set(field.group, list)
    }
    return [...grouped.entries()]
  }, [fields])

  const changeField = (key: string, value: unknown) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setDirty((current) => new Set(current).add(key))
    setErrors((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
    setMessage(null)
  }

  const saveSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const updates: Record<string, unknown> = {}
    const nextErrors: Record<string, string> = {}
    for (const field of fields) {
      if (!dirty.has(field.key) || field.readonly) continue
      const raw = draft[field.key]
      if (field.sensitive && (typeof raw !== 'string' || !raw.trim())) continue
      const parsed = parseFieldValue(field, raw)
      if (parsed.error) nextErrors[field.key] = parsed.error
      else if (parsed.value !== undefined) updates[field.key] = parsed.value
    }
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    if (!Object.keys(updates).length) {
      setDirty(new Set())
      setMessage({ kind: 'success', text: '没有需要保存的更改。', repair: false })
      return
    }

    setSaving(true)
    setMessage(null)
    try {
      const result = await api.PUT('/api/settings', { body: { items: updates } as never })
      const response = result as unknown as RequestResult
      if (response.error) throw response.error
      await loadSettings()
      setMessage({ kind: 'success', text: '设置已保存。', repair: false })
    } catch (error) {
      const failure = requestError(error)
      const repair = failure.code === 'SETTING_APPLY_FAILED' && failure.applied
      setMessage({
        kind: 'error',
        text: repair ? '已保存但生效失败。配置已保留，请检查设备连接或相关进程。' : failure.message,
        repair,
      })
      if (repair) await loadSettings()
    } finally {
      setSaving(false)
    }
  }

  const resetGroup = async (group: string, groupFields: SettingField[]) => {
    const keys = groupFields.filter((field) => !field.readonly).map((field) => field.key)
    if (!keys.length) return
    const confirmed = window.confirm(`确定清除“${group}”分组的数据库覆盖，并回落到配置文件或默认值吗？`)
    if (!confirmed) return
    setResettingGroup(group)
    setMessage(null)
    try {
      const result = await api.POST('/api/settings/reset', { body: { keys } as never })
      const response = result as unknown as RequestResult
      if (response.error) throw response.error
      await loadSettings()
      setMessage({ kind: 'success', text: `“${group}”分组已回落到配置文件或默认值。`, repair: false })
    } catch (error) {
      setMessage({ kind: 'error', text: requestError(error).message, repair: false })
    } finally {
      setResettingGroup(null)
    }
  }

  const saveLocalToken = () => {
    const token = localToken.trim()
    if (!token) {
      useAuth.getState().clearToken()
      setTokenSaved(false)
      setMessage({ kind: 'success', text: '此浏览器中的访问 token 已清除。', repair: false })
      return
    }
    useAuth.getState().setToken(token)
    setLocalToken('')
    setTokenSaved(true)
    setMessage({ kind: 'success', text: '访问 token 已保存在此浏览器。', repair: false })
  }

  const clearLocalToken = () => {
    useAuth.getState().clearToken()
    setLocalToken('')
    setTokenSaved(false)
    setMessage({ kind: 'success', text: '此浏览器中的访问 token 已清除。', repair: false })
  }

  const renderField = (field: SettingField) => {
    const id = `setting-${field.key.replace(/[^a-zA-Z0-9_-]/g, '-')}`
    const errorId = `${id}-error`
    const state = values[field.key]
    const current = draft[field.key]
    const disabled = field.readonly || loading || saving || resettingGroup !== null
    const shared = {
      id,
      disabled,
      'aria-invalid': errors[field.key] ? true : undefined,
      'aria-describedby': errors[field.key] ? errorId : undefined,
      onChange: (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => changeField(field.key, event.target.value),
    }

    let input: ReactNode
    if (field.options.length) {
      input = (
        <select
          {...shared}
          value={typeof current === 'string' ? current : ''}
          className="min-h-11 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value="">请选择</option>
          {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      )
    } else if (field.type === 'boolean') {
      input = (
        <select
          {...shared}
          value={current === true || current === 'true' ? 'true' : 'false'}
          className="min-h-11 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value="true">启用</option>
          <option value="false">停用</option>
        </select>
      )
    } else {
      const numeric = field.type === 'integer' || field.type === 'number'
      input = (
        <Input
          {...shared}
          type={field.sensitive ? 'password' : numeric ? 'number' : 'text'}
          inputMode={numeric ? 'decimal' : field.type === 'integer[]' ? 'numeric' : undefined}
          min={field.minimum}
          max={field.maximum}
          step={field.type === 'integer' ? 1 : numeric ? 'any' : undefined}
          autoComplete="off"
          spellCheck={false}
          value={field.sensitive ? (typeof current === 'string' ? current : '') : fieldInputValue(field, current)}
          placeholder={field.sensitive ? (state?.configured ? '留空以保留当前密钥' : '输入密钥') : field.type === 'integer[]' ? '用逗号分隔，例如 5554, 5555' : undefined}
        />
      )
    }

    return (
      <div key={field.key} className="grid gap-2 sm:grid-cols-[minmax(10rem,0.8fr)_minmax(0,1.2fr)] sm:items-start sm:gap-5">
        <div className="space-y-1 pt-2">
          <Label htmlFor={id}>{field.label}</Label>
          <p className="text-xs text-muted-foreground">{field.key}</p>
        </div>
        <div className="min-w-0 space-y-2">
          {input}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>来源：{displaySource(state?.source ?? 'unknown')}</span>
            {field.sensitive && (
              <span className="inline-flex items-center gap-1">
                <KeyRound className="size-3.5" aria-hidden="true" />
                {state?.configured ? (state.masked ? '已配置，已脱敏' : '已配置，页面已隐藏') : '尚未配置'}
              </span>
            )}
            {field.minimum !== undefined && <span>最小值 {field.minimum}</span>}
            {field.maximum !== undefined && <span>最大值 {field.maximum}</span>}
            {field.readonly && <span>由服务端管理</span>}
          </div>
          <p className="text-xs text-muted-foreground">{displayHotAction(field.hotAction)}</p>
          {errors[field.key] && <p id={errorId} role="alert" className="text-sm text-destructive">{errors[field.key]}</p>}
        </div>
      </div>
    )
  }

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 px-4 py-5 sm:px-6 sm:py-8">
      <header className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">系统配置</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">设置</h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
          配置由服务端 schema 提供。保存后，页面会显示当前生效值及其来源；敏感字段只显示脱敏状态。
        </p>
      </header>

      {message && (
        <div
          role={message.kind === 'error' ? 'alert' : 'status'}
          className={`flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between ${message.kind === 'error' ? 'border-destructive/40 bg-destructive/5 text-destructive' : 'border-primary/30 bg-primary/5'}`}
        >
          <div className="flex items-start gap-2 text-sm">
            {message.kind === 'error' ? <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <Check className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
            <span>{message.text}</span>
          </div>
          {message.repair && (
            <a className="shrink-0 text-sm font-medium underline underline-offset-4" href="#settings-group-设备">
              前往设备设置
            </a>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>本地访问 token</CardTitle>
          <CardDescription>仅保存在当前浏览器，用于 API 请求认证。token 不会展示在页面或写入日志。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1 space-y-2">
              <Label htmlFor="local-auth-token">替换访问 token</Label>
              <Input
                id="local-auth-token"
                type="password"
                autoComplete="new-password"
                value={localToken}
                onChange={(event) => { setLocalToken(event.target.value); setTokenSaved(false) }}
                placeholder={useAuth.getState().token ? '当前浏览器已设置 token' : '输入 token'}
              />
              <p className="text-xs text-muted-foreground">{tokenSaved || useAuth.getState().token ? '当前浏览器已设置 token。' : '尚未设置 token。'}</p>
            </div>
            <div className="flex gap-2">
              <Button type="button" onClick={saveLocalToken}>
                <KeyRound aria-hidden="true" />保存 token
              </Button>
              <Button type="button" variant="outline" onClick={clearLocalToken}>
                清除
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <form className="space-y-5" onSubmit={saveSettings}>
        {loading ? (
          <Card><CardContent className="flex min-h-28 items-center justify-center gap-2 pt-5 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />正在读取设置…</CardContent></Card>
        ) : groups.length ? groups.map(([group, groupFields]) => (
          <Card key={group} id={`settings-group-${group}`}>
            <CardHeader className="flex-row items-start justify-between gap-3">
              <div className="space-y-1.5">
                <CardTitle>{group}</CardTitle>
                <CardDescription>{groupFields.length} 项设置 · 由服务端 schema 驱动</CardDescription>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-11"
                disabled={saving || resettingGroup !== null || !groupFields.some((field) => !field.readonly)}
                onClick={() => void resetGroup(group, groupFields)}
              >
                {resettingGroup === group ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <RotateCcw aria-hidden="true" />}
                重置分组
              </Button>
            </CardHeader>
            <CardContent className="space-y-5">
              {groupFields.map(renderField)}
            </CardContent>
          </Card>
        )) : (
          <Card><CardContent className="pt-5 text-sm text-muted-foreground">服务端没有返回设置 schema。</CardContent></Card>
        )}
        <div className="sticky z-10 flex justify-end rounded-xl border bg-background/95 p-2 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/85" style={{ bottom: 'calc(3.5rem + env(safe-area-inset-bottom, 0px) + 0.5rem)' }}>
          <Button type="submit" disabled={loading || saving || resettingGroup !== null || dirty.size === 0}>
            {saving ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Save aria-hidden="true" />}
            {saving ? '正在保存…' : '保存设置'}
          </Button>
        </div>
      </form>

      <NotificationChannels />
    </main>
  )
}
