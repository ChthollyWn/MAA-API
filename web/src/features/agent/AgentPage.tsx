import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { ArrowDown, Bot, Gauge, MessagesSquare, SquarePen, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media'
import { Composer } from './Composer'
import { ConfirmationCard } from './ConfirmationCard'
import { LiveTurn, QuickPrompts, TranscriptEntryView } from './MessageViews'
import { SessionList } from './SessionList'
import { CapabilityAlert, ErrorAlert, GrantBar } from './StatusAlerts'
import { formatTokenCount, grantRemainingLabel, isRecord, sessionTitle, stringValue, toShownError } from './format'
import { requestId, streamMessage } from './stream'
import { buildTranscript } from './transcript'
import type {
  AgentConfirmation,
  AgentSession,
  CapabilityResult,
  PageEnvelope,
  ShownError,
  StreamingTurn,
  ToolTrace,
  TurnSegment,
} from './types'
import { useConversationScroll } from './use-conversation-scroll'
import { useMessageWindow } from './use-message-window'

function appendText(segments: TurnSegment[], text: string): TurnSegment[] {
  if (!text) return segments
  const last = segments.at(-1)
  if (last?.kind === 'text') return [...segments.slice(0, -1), { kind: 'text', text: last.text + text }]
  return [...segments, { kind: 'text', text }]
}

/** 会话授权倒计时用的时钟；只在有授权时走动。 */
function useNow(active: boolean, intervalMs = 15_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [active, intervalMs])
  return now
}

export default function AgentPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('session_id')
  const desktop = useMediaQuery(DESKTOP_QUERY)
  const [confirm, confirmElement] = useConfirm()
  const [draft, setDraft] = useState('')
  const [turn, setTurn] = useState<StreamingTurn | null>(null)
  // 本轮失败信息独立于 turn：成功收尾时会清空 turn，错误必须能在历史之外继续显示。
  const [turnError, setTurnError] = useState<ShownError | null>(null)
  const [capability, setCapability] = useState<CapabilityResult | null>(null)
  const [actionError, setActionError] = useState<ShownError | null>(null)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const [content, setContent] = useState<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const composerRef = useRef<HTMLTextAreaElement | null>(null)
  // 用户关掉过的降级原因：之后每轮 message_start 带回同样的原因时不再弹出。
  const dismissedReasonRef = useRef<string | null>(null)

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
  const messages = useMessageWindow(selectedId)
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

  const sessionRows = useMemo(() => sessions.data?.items ?? [], [sessions.data?.items])
  const conversation = selectedSession.data
  const selectedRow = sessionRows.find((row) => row.session_id === selectedId)
  const history = messages.items
  const entries = useMemo(() => buildTranscript(history), [history])
  const sessionPending = pendingConfirmations.data ?? []
  const otherPending = sessionPending.filter((item) => item.confirmation_id !== turn?.pendingConfirmation)
  const grant = conversation?.atomic_grant
  const now = useNow(Boolean(grant?.granted))
  const grantRemaining = grant?.granted ? grantRemainingLabel(grant.expires_at, now) : null

  const selectSession = (id: string) => {
    setSessionsOpen(false)
    if (id === selectedId) return
    abortRef.current?.abort()
    setTurn(null)
    setTurnError(null)
    setActionError(null)
    setSearchParams({ session_id: id })
  }

  const createSession = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST('/api/agent/sessions', { body: {} })
      if (error) throw toApiError(error, response)
      return data as unknown as AgentSession
    },
    onSuccess: async (session) => {
      await queryClient.invalidateQueries({ queryKey: keys.agent.sessions() })
      selectSession(session.session_id)
    },
    onError: (error) => setActionError(toShownError(error)),
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
      if (sessionId === selectedId) {
        abortRef.current?.abort()
        setTurn(null)
        setTurnError(null)
        const next = sessionRows.find((row) => row.session_id !== sessionId)
        setSearchParams(next ? { session_id: next.session_id } : {}, { replace: true })
      }
      toast.success('已删除会话')
      await queryClient.invalidateQueries({ queryKey: keys.agent.sessions() })
    },
    onError: (error) => setActionError(toShownError(error)),
  })
  const revokeGrant = useMutation({
    mutationFn: async (sessionId: string) => {
      const { error, response } = await api.DELETE('/api/agent/sessions/{session_id}/atomic-grant', {
        params: { path: { session_id: sessionId } },
      })
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      toast.success('已撤销屏幕操作授权')
      await queryClient.invalidateQueries({ queryKey: keys.agent.session(selectedId ?? '') })
      setActionError(null)
    },
    onError: (error) => setActionError(toShownError(error)),
  })
  const checkCapabilities = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST('/api/agent/capabilities/check')
      if (error) throw toApiError(error, response)
      return data as unknown as CapabilityResult
    },
    onSuccess: (result) => {
      dismissedReasonRef.current = null
      setCapability(result)
      setActionError(null)
    },
    onError: (error) => setActionError(toShownError(error)),
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
    onError: (error, input) => {
      setActionError(toShownError(error))
      if (toApiError(error).status === 409) {
        void queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), selectedId] })
        void queryClient.invalidateQueries({ queryKey: [...keys.agent.confirmations(), input.id] })
      }
    },
  })

  const lastMessageId = history.at(-1)?.id
  const tailKey = [
    lastMessageId ?? 'none',
    turn
      ? [turn.segments.length, turn.assistantText.length, turn.tools.filter((tool) => tool.result !== undefined).length, turn.pendingConfirmation ?? '', turn.running].join(':')
      : 'idle',
    otherPending.map((item) => item.confirmation_id).join(','),
    turnError?.message ?? '',
    messages.query.status,
  ].join('|')
  const scroll = useConversationScroll({
    container: desktop ? scroller : null,
    content,
    headKey: history[0]?.id,
    tailKey,
    resetKey: selectedId,
  })

  useEffect(() => {
    if (selectedId || !sessionRows.length) return
    setSearchParams({ session_id: sessionRows[0]!.session_id }, { replace: true })
  }, [selectedId, sessionRows, setSearchParams])

  useEffect(() => () => abortRef.current?.abort(), [])

  const confirmDelete = async (session: AgentSession) => {
    const title = sessionTitle(session)
    const ok = await confirm({
      title: `删除会话「${title}」？`,
      description: '会话里的全部消息会一起删除，无法恢复；这个会话的屏幕操作授权也会被撤销。',
      confirmText: '删除会话',
      tone: 'danger',
    })
    if (ok) deleteSession.mutate(session.session_id)
  }

  const send = async () => {
    if (!selectedId || !draft.trim() || turn?.running || resolveConfirmation.isPending) return
    const sessionId = selectedId
    const content = draft.trim()
    setDraft('')
    setActionError(null)
    const controller = new AbortController()
    abortRef.current = controller
    setTurnError(null)
    setTurn({ userText: content, assistantText: '', tools: [], segments: [], pendingConfirmation: null, running: true, error: null })
    scroll.scrollToBottom()
    const handleEvent = (name: string, rawData: unknown) => {
      const data = isRecord(rawData) ? rawData : {}
      if (name === 'tool_start') {
        const trace: ToolTrace = {
          toolCallId: stringValue(data.tool_call_id, requestId()),
          name: stringValue(data.name, '未知工具'),
          arguments: data.arguments,
        }
        setTurn((current) => current ? {
          ...current,
          tools: [...current.tools, trace],
          segments: [...current.segments, { kind: 'tool', toolCallId: trace.toolCallId }],
        } : current)
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
        void queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), sessionId] })
      } else if (name === 'text_delta') {
        const text = stringValue(data.text)
        setTurn((current) => current ? { ...current, assistantText: current.assistantText + text, segments: appendText(current.segments, text) } : current)
      } else if (name === 'error') {
        const message = stringValue(data.message, 'Agent 对话失败。')
        setTurn((current) => current ? { ...current, error: message } : current)
        // 普通回合出错时服务端不会把错误写成消息，这里不单独存下来，实时副本一清空就看不到了。
        setTurnError({ message, code: stringValue(data.code) || undefined })
      } else if (name === 'message_start') {
        const degradedReason = stringValue(data.degraded_reason)
        if (degradedReason && degradedReason !== dismissedReasonRef.current) {
          setCapability((current) => ({
            ...current,
            degraded_reason: degradedReason,
            ...(data.capability_status === 'vision_degraded' ? { vision: false } : {}),
          }))
        }
      } else if (name === 'session_title') {
        // 标题在收流前由服务端补好并随流推来。直接改缓存，标题栏与会话列表立刻从
        // 「新会话」变成真标题；回合结束时的 invalidate 只是兜底重取同一份数据。
        const nextTitle = stringValue(data.title)
        if (!nextTitle) return
        queryClient.setQueryData(keys.agent.session(sessionId), (current: unknown) =>
          isRecord(current) ? { ...current, title: nextTitle } : current)
        queryClient.setQueryData(keys.agent.sessions({ page: 1, size: 50 }), (current: unknown) =>
          isRecord(current) && Array.isArray(current.items)
            ? { ...current, items: current.items.map((item) => (isRecord(item) && item.session_id === sessionId ? { ...item, title: nextTitle } : item)) }
            : current)
      }
    }
    try {
      await streamMessage(sessionId, content, controller.signal, handleEvent)
      setTurn((current) => current ? { ...current, running: false } : current)
    } catch (error) {
      if (controller.signal.aborted) return
      const message = error instanceof Error ? error.message : 'Agent 对话失败。'
      setDraft((current) => current || content)
      setTurnError({ message, code: error instanceof ApiError ? error.code : undefined })
      setTurn((current) => current ? { ...current, running: false, error: message } : current)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      // 无论成功还是失败都要刷新：失败的回合同样会落库（服务端把错误写成 assistant
      // 消息、工具结果和 token 用量也都写了），而失败路径此前什么都不刷新 —— 于是标题
      // 的累计 token 与会话列表停在旧数据，用户必须手动刷新页面才看到，新会话也因此
      // 一直不出现在列表里（新建的会话 last_message_at 为空，排在列表末尾）。
      const settled = controller.signal.aborted
        ? Promise.resolve()
        : Promise.all([
            queryClient.invalidateQueries({ queryKey: keys.agent.messages(sessionId) }),
            queryClient.invalidateQueries({ queryKey: keys.agent.session(sessionId) }),
            queryClient.invalidateQueries({ queryKey: keys.agent.sessions() }),
            queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), sessionId] }),
          ])
      // 本轮内容已落库并回到 history，必须丢弃实时副本，否则同一句话会同时以
      // 本轮气泡和历史气泡出现两遍（工具轨迹同理）。清空时机只能是
      // invalidate 完成之后 —— 提前清会让界面在历史到达前闪空。
      await settled
      setTurn((current) => (current?.pendingConfirmation ? current : null))
    }
  }

  const pickPrompt = (prompt: string) => {
    setDraft(prompt)
    composerRef.current?.focus()
  }

  const dismissCapability = () => {
    dismissedReasonRef.current = capability?.degraded_reason ?? null
    setCapability(null)
  }

  const resolvingFor = (id: string): 'approve' | 'reject' | null => {
    const variables = resolveConfirmation.variables
    if (!resolveConfirmation.isPending || variables?.id !== id) return null
    return variables.approved ? 'approve' : 'reject'
  }
  const resolve = (id: string, approved: boolean) => resolveConfirmation.mutate({ id, approved })

  const busy = Boolean(turn?.running) || resolveConfirmation.isPending
  const title = conversation ? sessionTitle(conversation) : selectedRow ? sessionTitle(selectedRow) : 'Agent'

  const statusItems: ReactNode[] = []
  if (conversation && grantRemaining) {
    statusItems.push(<GrantBar key="grant" remaining={grantRemaining} revoking={revokeGrant.isPending} onRevoke={() => revokeGrant.mutate(conversation.session_id)} />)
  }
  if (checkCapabilities.isPending) {
    statusItems.push(<InlineAlert key="checking" tone="info" icon={null}><span className="flex items-center gap-2"><Spinner />正在检查模型能力…</span></InlineAlert>)
  } else if (capability) {
    statusItems.push(<CapabilityAlert key="capability" capability={capability} onDismiss={dismissCapability} />)
  }
  if (actionError) statusItems.push(<ErrorAlert key="action" error={actionError} onDismiss={() => setActionError(null)} />)
  const statusRegion = statusItems.length ? <div className="space-y-2">{statusItems}</div> : null

  const sessionList = sessions.isPending ? (
    <LoadingState label="正在读取会话…" />
  ) : sessions.error ? (
    <ErrorState message={`读取会话失败：${toApiError(sessions.error).message}`} onRetry={() => void sessions.refetch()} retrying={sessions.isFetching} />
  ) : sessionRows.length === 0 ? (
    <p className="px-3 py-6 text-center text-footnote text-muted-foreground">还没有会话。</p>
  ) : (
    <SessionList
      sessions={sessionRows}
      selectedId={selectedId}
      onSelect={selectSession}
      onDelete={(session) => void confirmDelete(session)}
      deletingId={deleteSession.isPending ? deleteSession.variables ?? null : null}
    />
  )

  const newSessionButton = (className?: string) => (
    <Button type="button" variant="soft" className={className} loading={createSession.isPending} onClick={() => createSession.mutate()}>
      {createSession.isPending ? null : <SquarePen aria-hidden="true" />}
      新建会话
    </Button>
  )

  const noSession = sessions.isPending ? (
    <LoadingState label="正在读取会话…" className="min-h-[40dvh]" />
  ) : sessions.error ? (
    <ErrorState title="读取会话失败" message={toApiError(sessions.error).message} onRetry={() => void sessions.refetch()} retrying={sessions.isFetching} />
  ) : sessionRows.length === 0 ? (
    <EmptyState
      icon={Bot}
      title="还没有会话"
      description="新建会话后，可以用自然语言查询设备与日志、编排任务、创建定时任务，也可以继续分析自动诊断记录。"
      action={newSessionButton()}
      className="min-h-[50dvh]"
    />
  ) : (
    <LoadingState label="正在打开最近的会话…" className="min-h-[40dvh]" />
  )

  const liveConfirmation = turn?.pendingConfirmation ? (
    <>
      <p role="status" className="flex items-center gap-2 text-sm text-warning">
        <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />等待你确认后继续；该操作尚未执行。
      </p>
      <ConfirmationCard confirmationId={turn.pendingConfirmation} busy={resolvingFor(turn.pendingConfirmation)} onResolve={resolve} />
    </>
  ) : null

  const transcript = !selectedId ? noSession : (
    <div className="space-y-4">
      {conversation ? (
        <p className="text-center text-xs text-muted-foreground tabular-nums">{conversation.model || '模型未配置'} · {formatTokenCount(conversation.total_tokens)} Token</p>
      ) : null}
      {selectedSession.error ? <InlineAlert tone="danger">会话读取失败：{toApiError(selectedSession.error).message}</InlineAlert> : null}
      {messages.query.isPending ? <LoadingState label="正在载入对话历史…" /> : null}
      {messages.query.error ? (
        <ErrorState title="消息读取失败" message={toApiError(messages.query.error).message} onRetry={() => void messages.query.refetch()} retrying={messages.query.isFetching} />
      ) : null}
      {messages.hasEarlier ? (
        <div className="flex justify-center">
          <Button type="button" variant="secondary" size="sm" className="rounded-full" loading={messages.loadingEarlier} onClick={() => messages.loadEarlier(scroll.rememberPositionBeforePrepend)}>
            {messages.loadingEarlier ? '正在加载…' : '加载更早的消息'}
          </Button>
        </div>
      ) : null}
      {messages.earlierError ? <InlineAlert tone="danger">读取更早的消息失败：{toApiError(messages.earlierError).message}</InlineAlert> : null}
      {entries.map((entry) => <TranscriptEntryView key={entry.key} entry={entry} />)}
      {messages.query.isSuccess && history.length === 0 && !turn ? <QuickPrompts onPick={pickPrompt} /> : null}
      {turn ? <LiveTurn turn={turn} confirmation={liveConfirmation} /> : null}
      {otherPending.map((item) => (
        <ConfirmationCard key={item.confirmation_id} confirmationId={item.confirmation_id} busy={resolvingFor(item.confirmation_id)} onResolve={resolve} />
      ))}
      {turnError ? <ErrorAlert error={turnError} /> : null}
    </div>
  )

  const composerStatus = turn?.running
    ? turn.pendingConfirmation
      ? <><TriangleAlert aria-hidden="true" className="size-4 shrink-0 text-warning" />请先处理上方的确认</>
      : <><Spinner />Agent 正在处理…</>
    : resolveConfirmation.isPending
      ? <><Spinner />正在提交确认…</>
      : null

  const composer = selectedId ? (
    <Composer
      value={draft}
      onValueChange={setDraft}
      onSend={() => void send()}
      busy={busy}
      status={composerStatus}
      docked={!desktop}
      textareaRef={composerRef}
      overlay={scroll.atBottom ? null : (
        <Button type="button" variant="secondary" size="sm" className="pointer-events-auto rounded-full shadow-float" onClick={() => scroll.scrollToBottom(true)}>
          <ArrowDown aria-hidden="true" />回到底部
        </Button>
      )}
    />
  ) : null

  const actions = (
    <>
      {desktop ? null : (
        <Button type="button" variant="ghost" size="icon" aria-label="会话列表" onClick={() => setSessionsOpen(true)}>
          <MessagesSquare aria-hidden="true" />
        </Button>
      )}
      <ActionMenu
        label="Agent 操作"
        title="Agent 操作"
        actions={[
          { label: '检查模型能力', description: '测试当前模型能否调用工具、识别图片', icon: Gauge, disabled: checkCapabilities.isPending, onSelect: () => checkCapabilities.mutate() },
          { label: '新建会话', icon: SquarePen, disabled: createSession.isPending, onSelect: () => createSession.mutate() },
        ]}
      />
    </>
  )

  if (desktop) {
    return (
      <Page
        title={title}
        width="full"
        actions={actions}
        // 与日志页同一口径：外壳 main 的底部留白是 --nav-offset + 1.5rem，这里刚好占满
        // 其余高度（原来漏了 --nav-offset，比视口高出约 24px，于是整个页面多出一根
        // 纵向滚动条，而对话区自己也在滚动）。只让会话列表与对话区内部滚动。
        className="h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[28rem]"
        contentClassName="flex min-h-0 flex-col"
      >
        <div className="grid min-h-0 flex-1 grid-cols-[18rem_minmax(0,1fr)] gap-6">
          <aside aria-label="Agent 会话" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            <div className="flex shrink-0 items-center justify-between gap-2 border-b py-1.5 pl-4 pr-1.5">
              <h2 className="text-headline font-semibold">会话</h2>
              <Button type="button" variant="ghost" size="icon" aria-label="新建会话" loading={createSession.isPending} onClick={() => createSession.mutate()}>
                {createSession.isPending ? null : <SquarePen aria-hidden="true" />}
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">{sessionList}</div>
          </aside>
          <section aria-label="Agent 对话" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            {statusRegion ? <div className="shrink-0 border-b p-3">{statusRegion}</div> : null}
            <div ref={setScroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <div ref={setContent} className="mx-auto w-full max-w-3xl px-6 py-6">{transcript}</div>
            </div>
            {composer}
          </section>
        </div>
        {confirmElement}
      </Page>
    )
  }

  return (
    <Page title={title} actions={actions} toolbar={statusRegion ?? undefined}>
      <div ref={setContent}>
        <section aria-label="Agent 对话" className="pb-4">{transcript}</section>
        {composer}
      </div>
      <ResponsiveSheet
        open={sessionsOpen}
        onOpenChange={setSessionsOpen}
        title="会话"
        description={sessionRows.length ? `最近 ${sessionRows.length} 个会话` : undefined}
      >
        <div className="space-y-3 pb-1">
          {newSessionButton('w-full')}
          <div className="-mx-2">{sessionList}</div>
        </div>
      </ResponsiveSheet>
      {confirmElement}
    </Page>
  )
}
