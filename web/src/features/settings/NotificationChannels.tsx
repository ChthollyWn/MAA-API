import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Bell, FlaskConical, LoaderCircle, Pencil, Plus, Trash2, X } from 'lucide-react'
import { api } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type JsonRecord = Record<string, unknown>
type RequestResult = { data?: unknown; error?: unknown }
type Channel = {
  id: string
  type: string
  name: string
  config: JsonRecord
  events: string[]
  enabled: boolean
  lastStatus?: string | null
  lastSentAt?: string | null
}
type Editor = {
  id?: string
  type: string
  name: string
  configText: string
  eventsText: string
  enabled: boolean
}

const SECRET_KEY = /(authorization|password|passwd|secret|token|api[_-]?key|device[_-]?key|(?:^|[_-])key(?:$|[_-]))/i

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {}
}

function redactConfig(value: unknown, key = ''): unknown {
  if (SECRET_KEY.test(key)) return value ? '***' : value
  if (Array.isArray(value)) return value.map((item) => redactConfig(item))
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as JsonRecord).map(([childKey, child]) => [childKey, redactConfig(child, childKey)]))
  }
  if (typeof value === 'string' && /(?:token|secret|key|password)=/i.test(value)) {
    try {
      const url = new URL(value)
      for (const [queryKey, queryValue] of url.searchParams.entries()) {
        if (SECRET_KEY.test(queryKey) && queryValue) url.searchParams.set(queryKey, '***')
      }
      return url.toString()
    } catch {
      return value.replace(/([?&](?:token|secret|key|password)=)[^&]*/gi, '$1***')
    }
  }
  return value
}

function normalizeChannels(payload: unknown): Channel[] {
  const root = asRecord(payload)
  const items = Array.isArray(root.items) ? root.items : []
  return items.flatMap((raw): Channel[] => {
    const item = asRecord(raw)
    if (typeof item.id !== 'string') return []
    return [{
      id: item.id,
      type: typeof item.type === 'string' ? item.type : '',
      name: typeof item.name === 'string' ? item.name : item.id,
      config: asRecord(redactConfig(item.config)),
      events: Array.isArray(item.events) ? item.events.filter((entry): entry is string => typeof entry === 'string') : [],
      enabled: item.enabled !== false,
      lastStatus: typeof item.last_status === 'string' ? item.last_status : null,
      lastSentAt: typeof item.last_sent_at === 'string' ? item.last_sent_at : null,
    }]
  })
}

function errorMessage(error: unknown): string {
  const root = asRecord(error)
  const detail = asRecord(root.detail)
  const nested = asRecord(root.error)
  return String(root.message ?? detail.message ?? nested.message ?? '请求失败，请检查配置后重试。')
}

function newEditor(): Editor {
  return { type: 'webhook', name: '', configText: '{}', eventsText: 'pipeline_completed', enabled: true }
}

export function NotificationChannels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [editor, setEditor] = useState<Editor | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const loadChannels = useCallback(async () => {
    setLoading(true)
    try {
      const result = await api.GET('/api/notifications/channels')
      const response = result as unknown as RequestResult
      if (response.error) throw response.error
      setChannels(normalizeChannels(response.data))
      setError(null)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadChannels() }, [loadChannels])

  const editChannel = (channel: Channel) => {
    setStatus(null)
    setError(null)
    setEditor({
      id: channel.id,
      type: channel.type,
      name: channel.name,
      configText: JSON.stringify(channel.config, null, 2),
      eventsText: channel.events.join(', '),
      enabled: channel.enabled,
    })
  }

  const submitEditor = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!editor) return
    let config: unknown
    try {
      config = JSON.parse(editor.configText)
      if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('配置必须是 JSON 对象。')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '配置 JSON 无效。')
      return
    }
    const payload = {
      type: editor.type.trim(),
      name: editor.name.trim(),
      config,
      events: editor.eventsText.split(',').map((entry) => entry.trim()).filter(Boolean),
      enabled: editor.enabled,
    }
    if (!payload.type) {
      setError('请填写通道类型。')
      return
    }
    if (!payload.name) {
      setError('请填写通道名称。')
      return
    }
    setSaving(true)
    setError(null)
    setStatus(null)
    try {
      const result = editor.id
        ? await api.PUT('/api/notifications/channels/{channel_id}', {
            params: { path: { channel_id: editor.id } },
            body: payload as never,
          })
        : await api.POST('/api/notifications/channels', { body: payload as never })
      const response = result as unknown as RequestResult
      if (response.error) throw response.error
      setEditor(null)
      await loadChannels()
      setStatus(editor.id ? '通知通道已更新。' : '通知通道已添加。')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSaving(false)
    }
  }

  const deleteChannel = async (channel: Channel) => {
    if (!window.confirm(`确定删除通知通道“${channel.name}”吗？`)) return
    setBusyId(channel.id)
    setError(null)
    setStatus(null)
    try {
      const result = await api.DELETE('/api/notifications/channels/{channel_id}', {
        params: { path: { channel_id: channel.id } },
      })
      const response = result as unknown as RequestResult
      if (response.error) throw response.error
      await loadChannels()
      setStatus(`通知通道“${channel.name}”已删除。`)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setBusyId(null)
    }
  }

  const testChannel = async (channel: Channel) => {
    setBusyId(channel.id)
    setError(null)
    setStatus(null)
    try {
      const result = await api.POST('/api/notifications/channels/{channel_id}/test', {
        params: { path: { channel_id: channel.id } },
        body: { event: 'pipeline_completed' } as never,
      })
      const response = result as unknown as RequestResult
      if (response.error) throw response.error
      setStatus(`已向“${channel.name}”发送测试通知。`)
      await loadChannels()
    } catch (failure) {
      setError(errorMessage(failure))
      await loadChannels()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2"><Bell className="size-4" aria-hidden="true" />通知通道</CardTitle>
          <CardDescription>管理通知目标、订阅事件，并即时发送测试消息。凭据只显示脱敏结果。</CardDescription>
        </div>
        <Button type="button" variant="outline" onClick={() => { setError(null); setStatus(null); setEditor(newEditor()) }}>
          <Plus aria-hidden="true" />添加通道
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
        {status && <p role="status" className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">{status}</p>}

        {editor && (
          <form className="space-y-4 rounded-xl border bg-muted/20 p-4" onSubmit={submitEditor}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="font-medium">{editor.id ? '编辑通知通道' : '新增通知通道'}</h3>
                <p className="mt-1 text-xs text-muted-foreground">配置 JSON 会按服务端契约校验；已有密钥以 *** 显示，回写时由服务端保留原值。</p>
              </div>
              <Button type="button" variant="ghost" size="icon" aria-label="关闭编辑器" onClick={() => setEditor(null)}><X aria-hidden="true" /></Button>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="notify-type">通道类型</Label>
                <Input id="notify-type" value={editor.type} onChange={(event) => setEditor({ ...editor, type: event.target.value })} placeholder="例如 webhook" autoComplete="off" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="notify-name">名称</Label>
                <Input id="notify-name" required maxLength={64} value={editor.name} onChange={(event) => setEditor({ ...editor, name: event.target.value })} placeholder="例如 手机推送" />
              </div>
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="notify-config">配置 JSON</Label>
                <textarea
                  id="notify-config"
                  required
                  spellCheck={false}
                  value={editor.configText}
                  onChange={(event) => setEditor({ ...editor, configText: event.target.value })}
                  className="min-h-36 w-full resize-y rounded-lg border border-input bg-background px-3 py-2 font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-describedby="notify-config-help"
                />
                <p id="notify-config-help" className="text-xs leading-5 text-muted-foreground">输入服务端支持的 JSON 对象。编辑时保留显示的 *** 占位值，可改动其它字段而保留已保存凭据。</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="notify-events">订阅事件</Label>
                <Input id="notify-events" value={editor.eventsText} onChange={(event) => setEditor({ ...editor, eventsText: event.target.value })} placeholder="pipeline_completed, update_available" />
                <p className="text-xs text-muted-foreground">多个事件以逗号分隔。</p>
              </div>
              <label className="flex min-h-11 items-center gap-3 self-end text-sm">
                <input type="checkbox" className="size-4 accent-primary" checked={editor.enabled} onChange={(event) => setEditor({ ...editor, enabled: event.target.checked })} />
                启用此通道
              </label>
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button type="button" variant="outline" onClick={() => setEditor(null)}>取消</Button>
              <Button type="submit" disabled={saving}>
                {saving ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Plus aria-hidden="true" />}
                {saving ? '正在保存…' : '保存通道'}
              </Button>
            </div>
          </form>
        )}

        {loading ? (
          <div className="flex min-h-20 items-center justify-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />正在读取通道…</div>
        ) : channels.length ? (
          <ul className="space-y-3" aria-label="通知通道列表">
            {channels.map((channel) => (
              <li key={channel.id} className="rounded-xl border p-4">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{channel.name}</h3>
                      <span className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">{channel.type}</span>
                      <span className={`rounded-md px-2 py-1 text-xs ${channel.enabled ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>{channel.enabled ? '已启用' : '已停用'}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">订阅：{channel.events.length ? channel.events.join('、') : '未订阅事件'}</p>
                    <p className="text-xs text-muted-foreground">
                      最近发送：{channel.lastStatus === 'success' ? '成功' : channel.lastStatus === 'failed' ? '失败' : '暂无记录'}
                      {channel.lastSentAt ? ` · ${new Date(channel.lastSentAt).toLocaleString()}` : ''}
                    </p>
                    <details className="pt-1">
                      <summary className="w-fit cursor-pointer text-xs text-muted-foreground underline underline-offset-4">查看脱敏配置</summary>
                      <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-muted/50 p-3 text-xs">{JSON.stringify(channel.config, null, 2)}</pre>
                    </details>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" size="sm" className="min-h-11" disabled={busyId !== null} onClick={() => editChannel(channel)}><Pencil aria-hidden="true" />编辑</Button>
                    <Button type="button" variant="outline" size="sm" className="min-h-11" disabled={busyId !== null} onClick={() => void testChannel(channel)}>
                      {busyId === channel.id ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <FlaskConical aria-hidden="true" />}
                      测试
                    </Button>
                    <Button type="button" variant="outline" size="sm" className="min-h-11" disabled={busyId !== null} onClick={() => void deleteChannel(channel)}><Trash2 aria-hidden="true" />删除</Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : !error ? (
          <div className="rounded-xl border border-dashed p-6 text-center">
            <p className="font-medium">还没有通知通道</p>
            <p className="mt-1 text-sm text-muted-foreground">添加一个通道后，可订阅任务完成等事件并发送测试消息。</p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
