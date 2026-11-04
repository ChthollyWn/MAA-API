import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { ArrowDown, Bot, MessagesSquare } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { useSpecTitles } from '@/features/console/api'
import { ConversationView } from '@/features/console/conversation/ConversationView'
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media'
import { SessionList } from './SessionList'
import { ErrorAlert } from './StatusAlerts'
import { formatTokenCount, sessionTitle, toShownError } from './format'
import type { AgentSession, PageEnvelope, ShownError } from './types'
import { useConversationScroll } from './use-conversation-scroll'
import { useMessageWindow } from './use-message-window'

/**
 * 旧会话只读查看器（M72 §4.6.6）：v2 Agent 页删掉全部写交互后剩下的浏览部分。
 * 会话列表、`?session_id=`、删除会话、分页与滚动沿用 v2；消息改由 `ConversationView`
 * 渲染，归档总控会话里的收件箱消息因此也是事件卡片。
 */
export default function LegacySessionsPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('session_id')
  const desktop = useMediaQuery(DESKTOP_QUERY)
  const [confirm, confirmElement] = useConfirm()
  const [actionError, setActionError] = useState<ShownError | null>(null)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const [content, setContent] = useState<HTMLElement | null>(null)
  const specTitles = useSpecTitles()

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

  const sessionRows = useMemo(() => sessions.data?.items ?? [], [sessions.data?.items])
  const conversation = selectedSession.data
  const selectedRow = sessionRows.find((row) => row.session_id === selectedId)
  const history = messages.items

  const selectSession = (id: string) => {
    setSessionsOpen(false)
    if (id === selectedId) return
    setActionError(null)
    setSearchParams({ session_id: id })
  }

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
        const next = sessionRows.find((row) => row.session_id !== sessionId)
        setSearchParams(next ? { session_id: next.session_id } : {}, { replace: true })
      }
      toast.success('已删除会话')
      await queryClient.invalidateQueries({ queryKey: keys.agent.sessions() })
    },
    onError: (error) => setActionError(toShownError(error)),
  })

  const lastMessageId = history.at(-1)?.id
  const tailKey = [
    lastMessageId ?? 'none',
    messages.query.status,
    messages.hasEarlier ? 'earlier' : '',
    messages.earlierError ? 'earlier-error' : '',
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

  useEffect(() => {
    setActionError(null)
  }, [selectedId])

  const confirmDelete = async (session: AgentSession) => {
    const title = sessionTitle(session)
    const ok = await confirm({
      title: `删除会话「${title}」？`,
      description: '会话里的全部消息会一起删除，无法恢复；屏幕操作授权也会一并撤销。',
      confirmText: '删除会话',
      tone: 'danger',
    })
    if (ok) deleteSession.mutate(session.session_id)
  }

  const sessionList = sessions.isPending ? (
    <LoadingState label="正在读取会话…" />
  ) : sessions.error ? (
    <ErrorState message={`读取会话失败：${toApiError(sessions.error).message}`} onRetry={() => void sessions.refetch()} retrying={sessions.isFetching} />
  ) : sessionRows.length === 0 ? (
    <p className="px-3 py-6 text-center text-footnote text-muted-foreground">还没有会话</p>
  ) : (
    <SessionList
      sessions={sessionRows}
      selectedId={selectedId}
      onSelect={selectSession}
      onDelete={(session) => void confirmDelete(session)}
      deletingId={deleteSession.isPending ? deleteSession.variables ?? null : null}
    />
  )

  const noSession = sessions.isPending ? (
    <LoadingState label="正在读取会话…" className="min-h-[40dvh]" />
  ) : sessions.error ? (
    <ErrorState title="读取会话失败" message={toApiError(sessions.error).message} onRetry={() => void sessions.refetch()} retrying={sessions.isFetching} />
  ) : sessionRows.length === 0 ? (
    <EmptyState
      icon={Bot}
      title="还没有旧会话"
      description="新的会话在指挥台进行"
      action={<Button asChild><Link to="/">前往指挥台</Link></Button>}
      className="min-h-[50dvh]"
    />
  ) : (
    <LoadingState label="正在打开最近的会话…" className="min-h-[40dvh]" />
  )

  const transcript = !selectedId ? noSession : (
    <div className="space-y-4">
      {conversation ? (
        <p className="text-center text-xs text-muted-foreground tabular-nums">{conversation.model || '模型未配置'} · {formatTokenCount(conversation.total_tokens)} Token</p>
      ) : null}
      {selectedSession.error ? <InlineAlert tone="danger">读取会话失败：{toApiError(selectedSession.error).message}</InlineAlert> : null}
      {messages.query.error ? (
        <ErrorState title="读取消息失败" message={toApiError(messages.query.error).message} onRetry={() => void messages.query.refetch()} retrying={messages.query.isFetching} />
      ) : messages.query.isPending ? <LoadingState label="正在载入历史消息…" /> : null}
      <ConversationView
        scope={{ kind: 'orchestrator' }}
        history={history}
        mainSessionId={null}
        specTitles={specTitles}
        earlier={{
          hasEarlier: messages.hasEarlier,
          loading: messages.loadingEarlier,
          error: messages.earlierError,
          onLoad: () => messages.loadEarlier(scroll.rememberPositionBeforePrepend),
        }}
      />
      {actionError ? <ErrorAlert error={actionError} onDismiss={() => setActionError(null)} /> : null}
    </div>
  )

  const scrollToBottomButton = scroll.atBottom ? null : (
    <div className="flex justify-center pt-4">
      <Button type="button" variant="secondary" size="sm" className="rounded-full shadow-float" onClick={() => scroll.scrollToBottom(true)}>
        <ArrowDown aria-hidden="true" />回到底部
      </Button>
    </div>
  )

  const readOnlyNotice = (
    <InlineAlert
      tone="info"
      title="这些会话只能查看"
      action={<Button asChild variant="link" size="sm"><Link to="/">前往指挥台</Link></Button>}
    >
      指挥台上线前的会话，以及重新开始前的总控会话。
    </InlineAlert>
  )

  const title = conversation ? sessionTitle(conversation) : selectedRow ? sessionTitle(selectedRow) : '旧会话'

  if (desktop) {
    return (
      <Page
        title={title}
        back="/more"
        width="full"
        // 与 v2 Agent 页同一口径：只让会话列表与对话区内部滚动。
        className="h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[28rem]"
        contentClassName="flex min-h-0 flex-col"
      >
        <div className="shrink-0 pb-4">{readOnlyNotice}</div>
        <div className="grid min-h-0 flex-1 grid-cols-[18rem_minmax(0,1fr)] gap-6">
          <aside aria-label="旧会话列表" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            <div className="flex shrink-0 items-center justify-between gap-2 border-b py-1.5 pl-4 pr-1.5">
              <h2 className="text-headline font-semibold">会话</h2>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">{sessionList}</div>
          </aside>
          <section aria-label="旧会话消息区" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            <div ref={setScroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <div ref={setContent} className="mx-auto w-full max-w-3xl px-6 py-6">
                {transcript}
                {scrollToBottomButton}
              </div>
            </div>
          </section>
        </div>
        {confirmElement}
      </Page>
    )
  }

  return (
    <Page
      title={title}
      back="/more"
      actions={(
        <Button type="button" variant="ghost" size="icon" aria-label="会话列表" onClick={() => setSessionsOpen(true)}>
          <MessagesSquare aria-hidden="true" />
        </Button>
      )}
    >
      <div className="space-y-4 pb-4">
        {readOnlyNotice}
        <section ref={setContent} aria-label="旧会话消息区">{transcript}</section>
        {scrollToBottomButton}
      </div>
      <ResponsiveSheet
        open={sessionsOpen}
        onOpenChange={setSessionsOpen}
        title="会话"
        description={sessionRows.length ? `最近 ${sessionRows.length} 个会话` : undefined}
      >
        <div className="-mx-2">{sessionList}</div>
      </ResponsiveSheet>
      {confirmElement}
    </Page>
  )
}
