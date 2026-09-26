import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { AlertTriangle, Bot, Check, ChevronDown, CircleHelp, LoaderCircle, Plus, RotateCw, Send, ShieldCheck, ShieldOff, Trash2, Wrench, X } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/stores/auth'

type JsonRecord = Record<string, unknown>

interface AgentSession {
  session_id: string
  title?: string | null
  status?: string
  model?: string
  message_count?: number
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  atomic_grant?: { granted: boolean; expires_at?: string | null; grant_id?: string | null } | null
  updated_at?: string | null
  last_message_at?: string | null
  incident_key?: string | null
}

interface AgentMessage {
  id: number
  session_id: string
  seq: number
  role: string
  content?: string | null
  tool_calls?: Array<{ id?: string; function?: { name?: string; arguments?: string } }> | null
  tool_name?: string | null
  tool_call_id?: string | null
  audit_id?: number | null
  finish_reason?: string | null
  created_at?: string | null
}

interface AgentConfirmation {
  confirmation_id: string
  action: string
  risk_level: string
  reason: string
  payload: JsonRecord
  status: string
  expires_at: string
  execution?: { status?: string; result?: string | null } | null
}

interface ToolTrace {
  toolCallId: string
  name: string
  arguments?: unknown
  result?: unknown
  confirmationId?: string
  auditId?: number
}

interface StreamingTurn {
  userText: string
  assistantText: string
  tools: ToolTrace[]
  pendingConfirmation: string | null
  running: boolean
  error: string | null
}

interface CapabilityResult {
  model?: string
  ready?: boolean
  tool_calling?: boolean
  vision?: boolean
  status?: string
  degraded_reason?: string | null
}

interface PageEnvelope<T> {
  items: T[]
  total: number
}

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function parseJson(value: string | null | undefined): unknown {
  if (!value) return null
  try { return JSON.parse(value) as unknown } catch { return value }
}

function apiMessage(error: unknown, response?: Response): string {
  return toApiError(error, response).message || '请求失败，请稍后重试。'
}

function expiryLabel(value?: string | null): string {
  if (!value) return '有效期未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : `有效至 ${new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(date)}`
}

function sessionTitle(session: AgentSession): string {
  if (session.title?.trim()) return session.title
  return session.incident_key ? '自动诊断会话' : '新会话'
}

function requestId(): string {
  return typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function parseSseFrame(frame: string, onEvent: (name: string, data: unknown) => void): void {
  let name = 'message'
  const data: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') name = value
    if (field === 'data') data.push(value)
  }
  if (data.length === 0) return
  const raw = data.join('\n')
  try { onEvent(name, JSON.parse(raw) as unknown) }
  catch { onEvent(name, { message: raw }) }
}

async function streamMessage(
  sessionId: string,
  content: string,
  signal: AbortSignal,
  onEvent: (name: string, data: unknown) => void,
): Promise<void> {
  const headers = new Headers({
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
    'X-Request-Id': requestId(),
  })
  const token = useAuth.getState().token
  if (token) headers.set('X-Token', token)
  const response = await globalThis.fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    credentials: 'same-origin',
    headers,
    body: JSON.stringify({ content }),
    signal,
  })
  if (!response.ok) {
    if (response.status === 401) useAuth.getState().onUnauthorized()
    let body: unknown
    try { body = await response.json() as unknown } catch { body = null }
    throw new Error(apiMessage(body, response))
  }
  if (!response.body) throw new Error('服务没有返回可读取的 SSE 消息流。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    while (true) {
      const separator = /\r?\n\r?\n/.exec(buffer)
      if (!separator || separator.index === undefined) break
      parseSseFrame(buffer.slice(0, separator.index), onEvent)
      buffer = buffer.slice(separator.index + separator[0].length)
    }
  }
  buffer += decoder.decode()
  if (buffer.trim()) parseSseFrame(buffer, onEvent)
}

function pretty(value: unknown): string {
  try { return JSON.stringify(value, null, 2) ?? String(value) }
  catch { return String(value) }
}

function messageText(message: AgentMessage): string {
  if (message.role === 'tool') {
    const parsed = parseJson(message.content)
    return pretty(parsed)
  }
  return message.content ?? ''
}

function MessageBubble({ message }: { message: AgentMessage }) {
  const user = message.role === 'user'
  const tool = message.role === 'tool'
  const name = message.tool_name ?? message.tool_calls?.map((call) => call.function?.name).filter(Boolean).join('、')
  if (tool) {
    return <details className="group rounded-xl border bg-muted/35 p-3">
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <Wrench className="size-4 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">工具结果 · {message.tool_name ?? '调用'}</span>
        {message.audit_id ? <span className="text-xs text-muted-foreground">审计 #{message.audit_id}</span> : null}
        <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words border-t pt-3 font-mono text-xs leading-5">{messageText(message)}</pre>
    </details>
  }
  return <article className={`max-w-[92%] rounded-2xl border px-4 py-3 sm:max-w-[82%] ${user ? 'ml-auto border-primary/20 bg-primary/10' : 'mr-auto bg-card'}`}>
    {!user && name ? <p className="mb-2 text-xs font-medium text-muted-foreground">工具计划：{name}</p> : null}
    {message.content ? <p className="whitespace-pre-wrap break-words text-sm leading-6">{message.content}</p> : null}
    {message.tool_calls?.length ? <details className="mt-2 rounded-lg border bg-muted/30 px-3 py-2">
      <summary className="flex min-h-8 cursor-pointer items-center gap-2 text-xs font-medium">查看工具调用 <ChevronDown className="size-4" aria-hidden="true" /></summary>
      <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words border-t pt-2 font-mono text-xs leading-5">{pretty(message.tool_calls.map((call) => ({ name: call.function?.name, arguments: parseJson(call.function?.arguments) })))}</pre>
    </details> : null}
    <p className="mt-2 text-[11px] text-muted-foreground">{user ? '你' : 'Agent'} · #{message.seq}</p>
  </article>
}

function ToolTraceList({ tools }: { tools: ToolTrace[] }) {
  if (tools.length === 0) return null
  return <section className="space-y-2" aria-label="本轮工具轨迹">
    {tools.map((tool) => <details key={tool.toolCallId} className="group rounded-xl border bg-muted/35 p-3">
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <Wrench className="size-4 text-primary" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">{tool.name}</span>
        {tool.confirmationId ? <Badge variant="warning">等待确认</Badge> : null}
        {tool.auditId ? <span className="text-xs text-muted-foreground">审计 #{tool.auditId}</span> : null}
        <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="mt-2 space-y-2 border-t pt-3">
        <p className="text-xs font-medium text-muted-foreground">参数</p>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5">{pretty(tool.arguments)}</pre>
        {tool.result !== undefined ? <>
          <p className="text-xs font-medium text-muted-foreground">结果</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5">{pretty(tool.result)}</pre>
        </> : null}
      </div>
    </details>)}
  </section>
}

function ConfirmationCard({
  confirmationId,
  onResolve,
  busy,
}: {
  confirmationId: string
  onResolve: (id: string, approved: boolean) => void
  busy: boolean
}) {
  const confirmation = useQuery({
    queryKey: [...keys.agent.confirmations(), confirmationId],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: confirmationId } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as AgentConfirmation
    },
    retry: false,
  })
  const detail = confirmation.data
  const grant = detail?.action === 'grant_atomic_ops'
  const approved = detail?.status === 'approved'
  const rejected = detail?.status === 'rejected' || detail?.status === 'expired'
  return <Card className="border-warning/50 border-l-4 border-l-warning" aria-live="polite">
    <CardHeader className="pb-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-base"><AlertTriangle className="size-4 text-warning" aria-hidden="true" />需要确认此操作</CardTitle>
          <CardDescription>{detail ? (grant ? '会话级屏幕操作授权' : detail.reason) : '正在读取确认详情…'}</CardDescription>
        </div>
        <Badge variant={approved ? 'positive' : rejected ? 'destructive' : 'warning'}>
          {approved ? '已批准' : rejected ? '已拒绝或过期' : expiryLabel(detail?.expires_at)}
        </Badge>
      </div>
    </CardHeader>
    <CardContent className="space-y-3">
      {confirmation.error ? <p role="alert" className="text-sm text-destructive">读取确认详情失败：{apiMessage(confirmation.error)}</p> : null}
      {detail?.payload.arguments ? <details className="rounded-lg border bg-muted/35 px-3 py-2">
        <summary className="min-h-8 cursor-pointer py-1 text-sm font-medium">查看操作参数</summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words border-t pt-2 font-mono text-xs leading-5">{pretty(detail.payload.arguments)}</pre>
      </details> : null}
      {approved ? <p className="text-sm text-positive">已批准，Agent 正在继续处理。</p> : rejected ? <p className="text-sm text-muted-foreground">确认已结束，本轮不会重试该操作。</p> : <>
        <p className="text-xs text-muted-foreground">此操作会经过服务端策略和审计；拒绝或过期后 Agent 不会重试。</p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={busy || confirmation.isLoading || !detail} onClick={() => onResolve(confirmationId, true)}>
            {busy ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <Check aria-hidden="true" />}
            {busy ? '正在处理…' : '批准并继续'}
          </Button>
          <Button type="button" variant="outline" disabled={busy || confirmation.isLoading || !detail} onClick={() => onResolve(confirmationId, false)}><X aria-hidden="true" />拒绝</Button>
        </div>
      </>}
    </CardContent>
  </Card>
}

function SessionList({
  sessions,
  selectedId,
  onSelect,
  onCreate,
  creating,
  onDelete,
  deleting,
}: {
  sessions: AgentSession[]
  selectedId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  creating: boolean
  onDelete: (id: string) => void
  deleting: boolean
}) {
  return <section className="space-y-3" aria-label="Agent 会话历史">
    <div className="flex items-center justify-between gap-2">
      <div><h2 className="font-semibold">会话</h2><p className="text-xs text-muted-foreground">{sessions.length} 条最近记录</p></div>
      <Button type="button" size="sm" className="min-h-11" disabled={creating} onClick={onCreate}>
        {creating ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <Plus aria-hidden="true" />}
        新建
      </Button>
    </div>
    {sessions.length ? <ul className="max-h-[32rem] space-y-1 overflow-y-auto pr-1">
      {sessions.map((session) => <li key={session.session_id}>
        <div className={`flex min-h-12 items-stretch gap-1 rounded-xl border ${selectedId === session.session_id ? 'border-primary/40 bg-primary/10' : 'border-transparent hover:bg-muted/70'}`}>
          <button type="button" className="min-w-0 flex-1 cursor-pointer rounded-xl px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-current={selectedId === session.session_id ? 'page' : undefined} onClick={() => onSelect(session.session_id)}>
            <span className="block truncate text-sm font-medium">{sessionTitle(session)}</span>
            <span className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
              {session.incident_key ? <Badge variant="warning" className="px-1.5 py-0.5">自动诊断</Badge> : null}
              <span>{session.message_count ?? 0} 条消息</span>
              <span>{session.model || '模型未配置'}</span>
            </span>
          </button>
          <Button type="button" size="icon" variant="ghost" className="my-auto mr-1 size-10" aria-label={`删除会话 ${sessionTitle(session)}`} disabled={deleting} onClick={() => onDelete(session.session_id)}><Trash2 aria-hidden="true" className="size-4" /></Button>
        </div>
      </li>)}
    </ul> : <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">还没有会话。新建会话后可以用自然语言查询状态、编排任务或分析故障。</p>}
  </section>
}

export default function AgentPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('session_id')
  const [draft, setDraft] = useState('')
  const [turn, setTurn] = useState<StreamingTurn | null>(null)
  const [capability, setCapability] = useState<CapabilityResult | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const transcriptRef = useRef<HTMLDivElement | null>(null)

  const sessions = useQuery({
    queryKey: keys.agent.sessions({ page: 1, size: 50 }),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/sessions', {
        params: { query: { page: 1, size: 50 } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as PageEnvelope<AgentSession>
    },
    retry: false,
  })
  const selectedSession = useQuery({
    queryKey: selectedId ? keys.agent.session(selectedId) : [...keys.agent.all(), 'no-session'],
    enabled: Boolean(selectedId),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/sessions/{session_id}', {
        params: { path: { session_id: selectedId! } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as AgentSession
    },
    retry: false,
  })
  const messages = useQuery({
    queryKey: selectedId ? keys.agent.messages(selectedId) : [...keys.agent.all(), 'no-messages'],
    enabled: Boolean(selectedId),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/sessions/{session_id}/messages', {
        params: { path: { session_id: selectedId! }, query: { page: 1, size: 200 } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as PageEnvelope<AgentMessage>
    },
    retry: false,
  })
  const pendingConfirmations = useQuery({
    queryKey: [...keys.agent.pendingConfirmations(), selectedId],
    enabled: Boolean(selectedId),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/confirmations', {
        params: { query: { status: 'pending', page: 1, size: 100 } },
      })
      if (error) throw toApiError(error, response)
      const page = data as unknown as PageEnvelope<AgentConfirmation>
      return page.items.filter((item) => item.payload.session_id === selectedId)
    },
    retry: false,
    refetchInterval: 10_000,
  })

  const createSession = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST('/api/agent/sessions', { body: {} })
      if (error) throw toApiError(error, response)
      return data as unknown as AgentSession
    },
    onSuccess: async (session) => {
      await queryClient.invalidateQueries({ queryKey: keys.agent.sessions() })
      setSearchParams({ session_id: session.session_id })
      setActionError(null)
      setMobileHistoryOpen(false)
    },
    onError: (error) => setActionError(apiMessage(error)),
  })
  const deleteSession = useMutation({
    mutationFn: async (sessionId: string) => {
      const { error, response } = await api.DELETE('/api/agent/sessions/{session_id}', {
        params: { path: { session_id: sessionId } },
      })
      if (error) throw toApiError(error, response)
      return sessionId
    },
    onSuccess: async (sessionId) => {
      if (sessionId === selectedId) setSearchParams({})
      await queryClient.invalidateQueries({ queryKey: keys.agent.sessions() })
    },
    onError: (error) => setActionError(apiMessage(error)),
  })
  const revokeGrant = useMutation({
    mutationFn: async (sessionId: string) => {
      const { error, response } = await api.DELETE('/api/agent/sessions/{session_id}/atomic-grant', {
        params: { path: { session_id: sessionId } },
      })
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: keys.agent.session(selectedId ?? '') })
      setActionError(null)
    },
    onError: (error) => setActionError(apiMessage(error)),
  })
  const checkCapabilities = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST('/api/agent/capabilities/check')
      if (error) throw toApiError(error, response)
      return data as unknown as CapabilityResult
    },
    onSuccess: (result) => {
      setCapability(result)
      setActionError(null)
    },
    onError: (error) => setActionError(apiMessage(error)),
  })
  const resolveConfirmation = useMutation({
    mutationFn: async (input: { id: string; approved: boolean }) => {
      const { error, response } = await api.POST('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: input.id } },
        body: { approved: input.approved },
      })
      if (error) throw toApiError(error, response)
      return input
    },
    onSuccess: async ({ id }) => {
      setTurn((current) => current?.pendingConfirmation === id
        ? { ...current, pendingConfirmation: null }
        : current)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), selectedId] }),
        queryClient.invalidateQueries({ queryKey: [...keys.agent.confirmations(), id] }),
        queryClient.invalidateQueries({ queryKey: keys.agent.audits() }),
      ])
      setActionError(null)
    },
    onError: (error) => setActionError(apiMessage(error)),
  })

  const sessionRows = sessions.data?.items ?? []
  const conversation = selectedSession.data
  const history = useMemo(() => messages.data?.items ?? [], [messages.data?.items])
  const sessionPending = pendingConfirmations.data ?? []

  useEffect(() => {
    if (selectedId || !sessionRows.length) return
    setSearchParams({ session_id: sessionRows[0].session_id }, { replace: true })
  }, [selectedId, sessionRows, setSearchParams])

  useEffect(() => {
    const transcript = transcriptRef.current
    if (!transcript || typeof transcript.scrollTo !== 'function') return
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    transcript.scrollTo({ top: transcript.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [history.length, turn?.assistantText, turn?.tools.length])

  useEffect(() => () => abortRef.current?.abort(), [])

  const selectSession = (id: string) => {
    if (id === selectedId) return
    abortRef.current?.abort()
    setTurn(null)
    setActionError(null)
    setSearchParams({ session_id: id })
    setMobileHistoryOpen(false)
  }

  const confirmDelete = (id: string) => {
    const session = sessionRows.find((item) => item.session_id === id)
    if (window.confirm(`删除会话「${session ? sessionTitle(session) : '会话'}」？`)) deleteSession.mutate(id)
  }

  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedId || !draft.trim() || turn?.running || resolveConfirmation.isPending) return
    const content = draft.trim()
    setDraft('')
    setActionError(null)
    const controller = new AbortController()
    abortRef.current = controller
    setTurn({ userText: content, assistantText: '', tools: [], pendingConfirmation: null, running: true, error: null })
    const handleEvent = (name: string, rawData: unknown) => {
      const data = isRecord(rawData) ? rawData : {}
      if (name === 'tool_start') {
        const trace: ToolTrace = {
          toolCallId: stringValue(data.tool_call_id, requestId()),
          name: stringValue(data.name, '未知工具'),
          arguments: data.arguments,
        }
        setTurn((current) => current ? { ...current, tools: [...current.tools, trace] } : current)
      } else if (name === 'tool_result') {
        setTurn((current) => current ? {
          ...current,
          tools: current.tools.map((tool) => tool.toolCallId === data.tool_call_id
            ? { ...tool, result: rawData, auditId: typeof data.audit_id === 'number' ? data.audit_id : undefined }
            : tool),
        } : current)
      } else if (name === 'confirmation_required') {
        const confirmationId = stringValue(data.confirmation_id)
        setTurn((current) => current ? {
          ...current,
          pendingConfirmation: confirmationId || null,
          tools: current.tools.map((tool) => tool.toolCallId === data.tool_call_id
            ? { ...tool, confirmationId }
            : tool),
        } : current)
        void queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), selectedId] })
      } else if (name === 'text_delta') {
        setTurn((current) => current ? { ...current, assistantText: current.assistantText + stringValue(data.text) } : current)
      } else if (name === 'error') {
        const message = stringValue(data.message, 'Agent 对话失败。')
        setTurn((current) => current ? { ...current, error: message } : current)
      } else if (name === 'message_start') {
        const degradedReason = stringValue(data.degraded_reason)
        if (degradedReason) setCapability((current) => ({ ...current, degraded_reason: degradedReason }))
      }
    }
    try {
      await streamMessage(selectedId, content, controller.signal, handleEvent)
      setTurn((current) => current ? { ...current, running: false } : current)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.messages(selectedId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.session(selectedId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.sessions() }),
        queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), selectedId] }),
      ])
    } catch (error) {
      if (controller.signal.aborted) return
      const message = error instanceof Error ? error.message : 'Agent 对话失败。'
      setDraft((current) => current || content)
      setTurn((current) => current ? { ...current, running: false, error: message } : current)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  const desktopSessions = <SessionList
    sessions={sessionRows}
    selectedId={selectedId}
    onSelect={selectSession}
    onCreate={() => createSession.mutate()}
    creating={createSession.isPending}
    onDelete={confirmDelete}
    deleting={deleteSession.isPending}
  />

  return <main className="mx-auto w-full max-w-6xl space-y-4 px-3 py-4 sm:px-6 sm:py-6">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">自然语言控制台</p>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><Bot className="size-6 text-primary" aria-hidden="true" />Agent</h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">用自然语言编排流水线、安排定时任务、查看运行日志并诊断 MAA 故障。</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" className="min-h-11" disabled={checkCapabilities.isPending} onClick={() => checkCapabilities.mutate()}>
          {checkCapabilities.isPending ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <RotateCw aria-hidden="true" />}
          检查模型能力
        </Button>
        <Badge variant={conversation?.atomic_grant?.granted ? 'positive' : 'outline'}>
          {conversation?.atomic_grant?.granted ? <><ShieldCheck className="mr-1 size-3.5" aria-hidden="true" />原子操作已授权</> : <><ShieldOff className="mr-1 size-3.5" aria-hidden="true" />未授权</>}
        </Badge>
      </div>
    </header>

    {(actionError || capability?.degraded_reason || checkCapabilities.data?.degraded_reason) ? <div className="space-y-2" aria-live="polite">
      {actionError ? <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{actionError}</p> : null}
      {capability?.degraded_reason || checkCapabilities.data?.degraded_reason ? <p className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm"><CircleHelp aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />{capability?.degraded_reason ?? checkCapabilities.data?.degraded_reason}<span className="ml-auto shrink-0">{capability?.vision === false || checkCapabilities.data?.vision === false ? '视觉能力不可用' : null}</span></p> : null}
      {(actionError ?? '').match(/LLM|密钥|模型配置|base_url/i) ? <Link className="inline-flex min-h-11 items-center rounded-lg px-3 text-sm font-medium text-primary underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to="/more/settings">前往设置检查 LLM 配置</Link> : null}
    </div> : null}

    {sessions.error ? <Card className="border-destructive/40"><CardContent className="flex flex-wrap items-center justify-between gap-3 p-4"><p role="alert" className="text-sm text-destructive">读取会话失败：{apiMessage(sessions.error)}</p><Button type="button" variant="outline" onClick={() => void sessions.refetch()}>重试</Button></CardContent></Card> : null}

    <div className="grid min-h-[min(70dvh,52rem)] gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
      <aside className="hidden rounded-2xl border bg-card p-3 lg:block">{desktopSessions}</aside>
      <div className="space-y-3 lg:hidden">
        <details className="group rounded-xl border bg-card" open={mobileHistoryOpen} onToggle={(event) => setMobileHistoryOpen(event.currentTarget.open)}>
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between px-3 font-medium [&::-webkit-details-marker]:hidden">
            <span>会话历史{selectedSession.data ? ` · ${sessionTitle(selectedSession.data)}` : ''}</span>
            <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="border-t p-3">{desktopSessions}</div>
        </details>
      </div>

      <section className="flex min-h-[min(70dvh,52rem)] min-w-0 flex-col overflow-hidden rounded-2xl border bg-card" aria-label="Agent 对话">
        {conversation ? <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 className="truncate font-semibold">{sessionTitle(conversation)}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{conversation.model || '模型未配置'} · {conversation.total_tokens ?? 0} tokens</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {conversation.atomic_grant?.granted ? <>
              <Badge variant="positive">{expiryLabel(conversation.atomic_grant.expires_at)}</Badge>
              <Button type="button" size="sm" variant="outline" className="min-h-11" disabled={revokeGrant.isPending} onClick={() => revokeGrant.mutate(conversation.session_id)}>
                {revokeGrant.isPending ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <ShieldOff aria-hidden="true" />}
                撤销授权
              </Button>
            </> : <Badge variant="outline">原子操作需单独授权</Badge>}
          </div>
        </header> : null}

        <div ref={transcriptRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 sm:p-5" aria-live="polite">
          {!selectedId ? <div className="grid min-h-[22rem] place-items-center text-center">
            <div className="max-w-md space-y-3">
              <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Bot aria-hidden="true" className="size-7" /></span>
              <h2 className="text-lg font-semibold">从一个问题开始</h2>
              <p className="text-sm leading-6 text-muted-foreground">创建会话后可查询设备与日志、排队执行任务、创建定时任务，也可以把自动诊断记录继续交给 Agent 分析。</p>
              <Button type="button" disabled={createSession.isPending} onClick={() => createSession.mutate()}><Plus aria-hidden="true" />新建会话</Button>
            </div>
          </div> : null}
          {selectedId && selectedSession.isPending ? <p role="status" className="py-8 text-center text-sm text-muted-foreground"><LoaderCircle className="mr-2 inline size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />正在读取会话…</p> : null}
          {selectedId && selectedSession.error ? <p role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">会话读取失败：{apiMessage(selectedSession.error)}</p> : null}
          {selectedId && messages.isPending ? <p role="status" className="py-4 text-center text-sm text-muted-foreground">正在载入对话历史…</p> : null}
          {selectedId && messages.error ? <p role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">消息读取失败：{apiMessage(messages.error)}</p> : null}
          {history.map((message) => <MessageBubble key={message.id} message={message} />)}
          {selectedId && !messages.isPending && history.length === 0 && !turn ? <div className="mx-auto grid max-w-lg place-items-center py-8 text-center">
            <div className="space-y-3">
              <h3 className="font-semibold">你可以这样开始</h3>
              <div className="flex flex-wrap justify-center gap-2">
                {['查看设备状态和队列', '分析最近一次失败的流水线', '帮我保存一个 Copilot 作业'].map((prompt) => <Button key={prompt} type="button" variant="outline" className="min-h-11 whitespace-normal" onClick={() => setDraft(prompt)}>{prompt}</Button>)}
              </div>
              <p className="text-xs text-muted-foreground">需要执行的操作会先经过策略检查；资源消耗和破坏性操作逐次等待确认。</p>
            </div>
          </div> : null}
          {sessionPending.map((confirmation) => <ConfirmationCard
            key={confirmation.confirmation_id}
            confirmationId={confirmation.confirmation_id}
            busy={resolveConfirmation.isPending && resolveConfirmation.variables?.id === confirmation.confirmation_id}
            onResolve={(id, approved) => resolveConfirmation.mutate({ id, approved })}
          />)}
          {turn ? <div className="space-y-3">
            <article className="ml-auto max-w-[92%] rounded-2xl border border-primary/20 bg-primary/10 px-4 py-3 sm:max-w-[82%]">
              <p className="whitespace-pre-wrap break-words text-sm leading-6">{turn.userText}</p>
              <p className="mt-2 text-[11px] text-muted-foreground">你 · 本轮</p>
            </article>
            {turn.assistantText ? <article className="mr-auto max-w-[92%] rounded-2xl border bg-background px-4 py-3 text-sm leading-6 sm:max-w-[82%]"><p className="whitespace-pre-wrap break-words">{turn.assistantText}</p></article> : null}
            <ToolTraceList tools={turn.tools} />
            {turn.pendingConfirmation ? <>
              <p className="flex items-center gap-2 text-sm text-warning" role="status"><AlertTriangle className="size-4" aria-hidden="true" />等待你确认后继续；该操作尚未执行。</p>
              {!sessionPending.some((item) => item.confirmation_id === turn.pendingConfirmation) ? <ConfirmationCard
                confirmationId={turn.pendingConfirmation}
                busy={resolveConfirmation.isPending && resolveConfirmation.variables?.id === turn.pendingConfirmation}
                onResolve={(id, approved) => resolveConfirmation.mutate({ id, approved })}
              /> : null}
            </> : null}
            {turn.running && !turn.pendingConfirmation ? <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />Agent 正在处理…</p> : null}
            {turn.error ? <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{turn.error}</p> : null}
          </div> : null}
        </div>

        {selectedId ? <form className="border-t bg-background/70 p-3 sm:p-4" onSubmit={(event) => void send(event)}>
          <Label htmlFor="agent-composer" className="sr-only">发送给 Agent</Label>
          <div className="flex items-end gap-2">
            <textarea
              id="agent-composer"
              className="min-h-12 max-h-40 min-w-0 flex-1 resize-y rounded-xl border border-input bg-background px-3 py-2.5 text-sm leading-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              rows={2}
              maxLength={32_000}
              placeholder="例如：查看刚才失败流水线的日志，并告诉我原因…"
              value={draft}
              disabled={turn?.running}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onComposerKeyDown}
            />
            <Button type="submit" size="icon" aria-label="发送消息" disabled={!draft.trim() || Boolean(turn?.running)}>
              {turn?.running ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <Send aria-hidden="true" />}
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">Enter 发送 · Shift+Enter 换行 · 高风险和资源消耗操作仍需逐次确认</p>
        </form> : null}
      </section>
    </div>
  </main>
}
