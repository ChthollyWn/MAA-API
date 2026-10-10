import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { ArrowDown, Bot, CircleHelp, Gauge, MessagesSquare, SquarePen, TriangleAlert } from 'lucide-react'
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
import { QuestionCard } from './QuestionCard'
import { SessionList } from './SessionList'
import { CapabilityAlert, ErrorAlert, GrantBar } from './StatusAlerts'
import type { CopilotGrantOptions } from '@/features/copilots/CopilotGrantCard'
import { formatTokenCount, grantRemainingLabel, isRecord, parseQuestionOptions, sessionTitle, stringValue, toShownError } from './format'
import { requestId, streamMessage } from './stream'
import { buildTranscript } from './transcript'
import type {
  AgentConfirmation,
  AgentQuestion,
  AgentQuestionPrompt,
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
  // 被输入栏那个「停止」打断的本地流。`send()` 的 catch 见到 signal.aborted 会早退，
  // 收尾（失效查询、丢弃实时副本）只能由 finally 认出「这是主动停止」才会继续做（M22）。
  const interruptedControllerRef = useRef<AbortController | null>(null)
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
    // 刷新/切页/换设备回来后，本会话是否还有一轮在跑只能问后端（本地没有那条流）。
    // 与待确认、待答查询同一节奏：10 秒一次（M22 规格 §3.5）。
    refetchInterval: 10_000,
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
  // 本会话待答的提问：刷新/切页回来仍能看到（与 pending confirmations 同一节奏）。
  const pendingQuestions = useQuery({
    queryKey: [...keys.agent.pendingQuestions(), selectedId],
    enabled: Boolean(selectedId),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/questions', {
        params: { query: { session_id: selectedId!, status: 'pending', page: 1, size: 100 } },
      })
      if (error) throw toApiError(error, response)
      return (data as unknown as PageEnvelope<AgentQuestion>).items
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
  // 已经在本轮工具条下方展示过的那张卡不再重复渲染（刷新后本轮没了，才由这里接管）。
  const otherQuestions = (pendingQuestions.data ?? []).filter((item) => item.question_id !== turn?.pendingQuestion?.question_id)
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
    mutationFn: async (input: { id: string; approved: boolean; overrides?: CopilotGrantOptions }) => {
      const { error, response } = await api.POST('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: input.id } },
        // 作业授权（M29 §9.6）：批准时可以把尝试次数、成功标准与是否允许助战一起改掉。
        body: { approved: input.approved, ...(input.overrides ? { overrides: input.overrides } : {}) },
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
  /**
   * 提问被用户处理掉（提交/跳过，或发现已被别的标签页处理）之后：清掉本轮那张卡，
   * 并把这条从待答缓存里摘掉 —— 只靠失效重取的话，已答的卡会闪一下再消失。
   */
  const resolveQuestion = (questionId: string) => {
    setTurn((current) => current?.pendingQuestion?.question_id === questionId
      ? { ...current, pendingQuestion: null }
      : current)
    queryClient.setQueryData([...keys.agent.pendingQuestions(), selectedId], (current: unknown) =>
      Array.isArray(current)
        ? current.filter((item) => !(isRecord(item) && item.question_id === questionId))
        : current)
    void queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingQuestions(), selectedId] })
  }

  /**
   * 一轮结束（成功、失败或被中断）后要刷新的五组查询：本轮已经落库，会话累计用量、
   * 列表顺序、挂起卡片都可能变了。
   */
  const invalidateTurnQueries = (sessionId: string) => Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.agent.messages(sessionId) }),
    queryClient.invalidateQueries({ queryKey: keys.agent.session(sessionId) }),
    queryClient.invalidateQueries({ queryKey: keys.agent.sessions() }),
    queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingConfirmations(), sessionId] }),
    queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingQuestions(), sessionId] }),
  ])

  /**
   * 停止本轮（M22）。后端幂等：没有在跑的一轮时返回 `interrupted=false`，不是错误。
   *
   * 本地必须自己收尾：`send()` 的 catch 见到 `signal.aborted` 会早退，`running` 会
   * 永久停在 true（这个既有 bug 一并修）。这里显式置 `running:false`、标记本轮为已
   * 中断、撤下挂起的确认/提问卡（后端已把确认置 rejected、提问置 cancelled），再失效
   * 那五组查询让历史接上。
   */
  const stopTurn = useMutation({
    mutationFn: async (sessionId: string) => {
      const { data, error, response } = await api.POST('/api/agent/sessions/{session_id}/interrupt', {
        params: { path: { session_id: sessionId } },
      })
      if (error) throw toApiError(error, response)
      return data
    },
    onSuccess: async (_result, sessionId) => {
      const controller = abortRef.current
      if (controller) {
        // 先登记再 abort：finally 靠这个 ref 认出本轮是主动停止，照常失效与清场。
        interruptedControllerRef.current = controller
        controller.abort()
      }
      setTurn((current) => current ? {
        ...current,
        running: false,
        interrupted: true,
        pendingConfirmation: null,
        pendingQuestion: null,
      } : current)
      await invalidateTurnQueries(sessionId)
      setTurn((current) => (current?.pendingConfirmation || current?.pendingQuestion ? current : null))
      setActionError(null)
    },
    onError: (error) => setActionError(toShownError(error)),
  })

  const lastMessageId = history.at(-1)?.id
  const tailKey = [
    lastMessageId ?? 'none',
    turn
      ? [turn.segments.length, turn.assistantText.length, turn.tools.filter((tool) => tool.result !== undefined).length, turn.pendingConfirmation ?? '', turn.pendingQuestion?.question_id ?? '', turn.running].join(':')
      : 'idle',
    otherPending.map((item) => item.confirmation_id).join(','),
    otherQuestions.map((item) => item.question_id).join(','),
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
      description: '会话里的全部消息会一起删除，无法恢复；屏幕操作授权也会一并撤销。',
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
    setTurn({ userText: content, assistantText: '', tools: [], segments: [], pendingConfirmation: null, pendingQuestion: null, running: true, error: null })
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
        // ask_user 的结果一到，「本轮还挂着提问」就不成立了：答案可能来自别的标签页，
        // 超时/跳过更是本轮到此为止。不清的话实时副本会连同整轮内容一直挂在历史下面。
        const questionResult = isRecord(data.result) ? data.result : null
        const resolvedQuestionId = stringValue(data.name) === 'ask_user' ? stringValue(questionResult?.question_id) : ''
        setTurn((current) => current ? {
          ...current,
          pendingQuestion: resolvedQuestionId && current.pendingQuestion?.question_id === resolvedQuestionId
            ? null
            : current.pendingQuestion,
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
      } else if (name === 'question_required') {
        const questionId = stringValue(data.question_id)
        if (!questionId) return
        // 本轮挂起等用户选择。服务端在 tool_start 之后推这条，卡就放在那次工具调用下方。
        const prompt: AgentQuestionPrompt = {
          question_id: questionId,
          question: stringValue(data.question),
          options: parseQuestionOptions(data.options),
          multiple: data.multiple === true,
          expires_at: stringValue(data.expires_at),
        }
        setTurn((current) => current ? { ...current, pendingQuestion: prompt } : current)
        void queryClient.invalidateQueries({ queryKey: [...keys.agent.pendingQuestions(), sessionId] })
      } else if (name === 'text_delta') {
        const text = stringValue(data.text)
        setTurn((current) => current ? { ...current, assistantText: current.assistantText + text, segments: appendText(current.segments, text) } : current)
      } else if (name === 'interrupted') {
        // 本轮被中断（用户在本标签页点了停止，或别的标签页/设备停的）。这不是错误：
        // 状态行只说「本轮已停止」，随后的 message_end 与关流照常收尾，不做任何报错。
        // 挂起的确认/提问卡也要撤下——后端已把它们置为终态，留着就是一张死卡。
        setTurn((current) => current ? {
          ...current,
          running: false,
          interrupted: true,
          pendingConfirmation: null,
          pendingQuestion: null,
        } : current)
      } else if (name === 'message_end') {
        // 服务端已经给这一轮定论（收流紧随其后）：状态行不必再等连接关闭，免得
        // 「Agent 正在处理…」多挂一拍。M22 起 message_end 也可能是 interrupted 的收尾。
        setTurn((current) => current ? { ...current, running: false } : current)
      } else if (name === 'error') {
        const message = stringValue(data.message, '本轮回复失败，请重试。')
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
      const code = error instanceof ApiError ? error.code : undefined
      // 409 AGENT_SESSION_BUSY：这个会话还有一轮在跑（别处发的，或本地 turn_running
      // 还没刷到）。文案必须指向输入栏那个「停止」，否则用户只能对着「会话忙」发呆。
      const message = code === 'AGENT_SESSION_BUSY'
        ? '该会话仍有一轮在跑，可先停止它'
        : error instanceof Error ? error.message : '本轮回复失败，请重试。'
      setDraft((current) => current || content)
      setTurnError({ message, code })
      setTurn((current) => current ? { ...current, running: false, error: message } : current)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      // 主动停止走的是 abort：catch 会早退，但这一轮同样落了库，失效不能跳过（M22）。
      const interruptedHere = interruptedControllerRef.current === controller
      if (interruptedHere) interruptedControllerRef.current = null
      // 无论成功还是失败都要刷新：失败的回合同样会落库（服务端把错误写成 assistant
      // 消息、工具结果和 token 用量也都写了），而失败路径此前什么都不刷新 —— 于是标题
      // 的累计 token 与会话列表停在旧数据，用户必须手动刷新页面才看到，新会话也因此
      // 一直不出现在列表里（新建的会话 last_message_at 为空，排在列表末尾）。
      const settled = controller.signal.aborted && !interruptedHere
        ? Promise.resolve()
        : invalidateTurnQueries(sessionId)
      // 本轮内容已落库并回到 history，必须丢弃实时副本，否则同一句话会同时以
      // 本轮气泡和历史气泡出现两遍（工具轨迹同理）。清空时机只能是
      // invalidate 完成之后 —— 提前清会让界面在历史到达前闪空。
      await settled
      // 收尾期间用户可能已经发出下一条（message_end 一到输入栏就恢复可用）：那一轮
      // 登记了新的 controller，这一轮的收尾不能把它的界面抹掉。
      if (abortRef.current === null) {
        // 还挂着确认或提问的一轮保留：卡要留在原位等用户处理（提问结束时会由
        // tool_result 分支摘掉，所以这里不会把已经结束的一轮永久留下）。
        setTurn((current) => (current?.pendingConfirmation || current?.pendingQuestion ? current : null))
      }
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
  const resolve = (id: string, approved: boolean, overrides?: CopilotGrantOptions) =>
    resolveConfirmation.mutate({ id, approved, overrides })

  const stopping = stopTurn.isPending
  // 后端说这一轮还在跑、本地却没有它的流（刷新、切页、换设备回来）：输入栏同样要能停。
  const backgroundRunning = Boolean(conversation?.turn_running) && !turn?.running
  // 只有真有一轮可停时才把发送按钮换成「停止」；busy 仅来自提交确认时保持禁用的发送。
  const canStop = Boolean(turn?.running) || backgroundRunning || stopping
  const busy = Boolean(turn?.running) || backgroundRunning || stopping || resolveConfirmation.isPending
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
      description="说出你的目标就行，比如「看看设备连上没有」。"
      action={newSessionButton()}
      className="min-h-[50dvh]"
    />
  ) : (
    <LoadingState label="正在打开最近的会话…" className="min-h-[40dvh]" />
  )

  const liveConfirmation = turn?.pendingConfirmation ? (
    <>
      <p role="status" className="flex items-center gap-2 text-sm text-warning">
        <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />等待你确认，操作还没有执行。
      </p>
      <ConfirmationCard confirmationId={turn.pendingConfirmation} busy={resolvingFor(turn.pendingConfirmation)} onResolve={resolve} />
    </>
  ) : null

  const liveQuestion = turn?.pendingQuestion ? (
    <QuestionCard key={turn.pendingQuestion.question_id} question={turn.pendingQuestion} onResolved={resolveQuestion} />
  ) : null

  const transcript = !selectedId ? noSession : (
    <div className="space-y-4">
      {conversation ? (
        <p className="text-center text-xs text-muted-foreground tabular-nums">{conversation.model || '模型未配置'} · {formatTokenCount(conversation.total_tokens)} Token</p>
      ) : null}
      {selectedSession.error ? <InlineAlert tone="danger">读取会话失败：{toApiError(selectedSession.error).message}</InlineAlert> : null}
      {messages.query.isPending ? <LoadingState label="正在载入历史消息…" /> : null}
      {messages.query.error ? (
        <ErrorState title="读取消息失败" message={toApiError(messages.query.error).message} onRetry={() => void messages.query.refetch()} retrying={messages.query.isFetching} />
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
      {turn ? <LiveTurn turn={turn} confirmation={liveConfirmation} question={liveQuestion} /> : null}
      {otherPending.map((item) => (
        <ConfirmationCard key={item.confirmation_id} confirmationId={item.confirmation_id} busy={resolvingFor(item.confirmation_id)} onResolve={resolve} />
      ))}
      {otherQuestions.map((item) => (
        <QuestionCard key={item.question_id} question={item} onResolved={resolveQuestion} />
      ))}
      {turnError ? <ErrorAlert error={turnError} /> : null}
    </div>
  )

  // 确认优先：确认会决定提问所在那批工具能不能执行，提问的结果本来就排在它后面。
  const composerStatus = (() => {
    if (stopping) return <><Spinner />正在停止本轮…</>
    if (turn?.running) {
      if (turn.pendingConfirmation) return <><TriangleAlert aria-hidden="true" className="size-4 shrink-0 text-warning" />请先处理待确认的操作</>
      if (turn.pendingQuestion) return <><CircleHelp aria-hidden="true" className="size-4 shrink-0 text-primary-text" />请先回答等待中的提问</>
      return <><Spinner />Agent 正在处理…</>
    }
    // 中断是收尾，不是失败：只报「本轮已停止」，真正的结果（含中断标记）在历史里。
    if (turn?.interrupted) return <>本轮已停止</>
    if (backgroundRunning) return <><Spinner />本轮仍在后台执行</>
    if (resolveConfirmation.isPending) return <><Spinner />正在提交确认…</>
    return null
  })()

  const composer = selectedId ? (
    <Composer
      value={draft}
      onValueChange={setDraft}
      onSend={() => void send()}
      busy={busy}
      onStop={canStop ? () => stopTurn.mutate(selectedId) : undefined}
      stopping={stopping}
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
          <aside aria-label="Agent 会话列表" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
            <div className="flex shrink-0 items-center justify-between gap-2 border-b py-1.5 pl-4 pr-1.5">
              <h2 className="text-headline font-semibold">会话</h2>
              <Button type="button" variant="ghost" size="icon" aria-label="新建会话" loading={createSession.isPending} onClick={() => createSession.mutate()}>
                {createSession.isPending ? null : <SquarePen aria-hidden="true" />}
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">{sessionList}</div>
          </aside>
          <section aria-label="Agent 消息区" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card">
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
        <section aria-label="Agent 消息区" className="pb-4">{transcript}</section>
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
