import { Fragment, useMemo, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { toApiError } from '@/api/errors'
import { isRecord } from '@/features/agent/format'
import { AssistantText } from '@/features/agent/MessageViews'
import { ErrorAlert } from '@/features/agent/StatusAlerts'
import type { ToolView } from '@/features/agent/transcript'
import { ToolCallBar } from '@/features/agent/ToolCallBar'
import { CompactionMarker } from '@/features/compaction/CompactionMarker'
import type { CheckpointView } from '@/features/compaction/api'
import { insertMarkers } from '@/features/compaction/insert-markers'
import { MessageActions } from '@/features/rewind/MessageActions'
import { SupersededFold } from '@/features/rewind/SupersededFold'
import type { MessageKind } from '@/features/rewind/targets'
import type { ConfirmationResolver } from './card-resolvers'
import { DelegateToolBar } from './DelegateToolBar'
import { InboxEventCard } from './InboxEventCard'
import type { LiveTurn } from './live-turn'
import { LiveTurnView } from './LiveTurnView'
import { ReportCallout } from './ReportCallout'
import { TicketLinkChip, toolTicketLink } from './TicketLinkChip'
import { buildConversationEntries, type ConversationEntry } from './transcript-v3'
import type { ConversationScope, V3Message } from './types'
import { UserMessage } from './UserMessage'
import type { ShownError } from '@/features/agent/types'

/** 消息上的四个回溯动作（M80 §4.6）由页面注入；不传时消息列表与 M79 一致。 */
export interface ConversationActionsConfig {
  kind: MessageKind
  /** 会话此刻真有一轮在跑：四个动作都禁用。 */
  running: boolean
  sessionId: string | null
  /** 工单页才有；「分叉」要用。 */
  ticketId?: string
  /** 重发用 M72 已有的发消息函数。 */
  onResend: (text: string) => Promise<unknown> | unknown
}

export interface ConversationViewProps {
  scope: ConversationScope
  /** 已经过 `filterLiveHistory` 的可见历史。 */
  history: readonly V3Message[]
  mainSessionId: string | null
  specTitles: ReadonlyMap<string, string>
  /** 压缩检查点（M74 §4.6）；不传时与 M73 的渲染一致。 */
  checkpoints?: readonly CheckpointView[]
  live?: LiveTurn | null
  showOptimisticUser?: boolean
  queued?: readonly { localId: string; text: string }[]
  /** 不传时实时副本里的卡片不渲染（只读场景）。 */
  resolver?: ConfirmationResolver
  onQuestionResolved?: (questionId: string) => void
  earlier?: { hasEarlier: boolean; loading: boolean; error: unknown; onLoad: () => void }
  error?: ShownError | null
  emptyState?: ReactNode
  /** 工单页：本工单刷新后仍挂着的确认卡、提问卡。 */
  leftovers?: ReactNode
  /** M80：消息旁的四个动作；不传时不渲染动作菜单，也不折叠被取代的消息。 */
  actions?: ConversationActionsConfig
}

const noop = () => {}

function entryAgentName(tool: ToolView): string | null {
  if (!isRecord(tool.arguments) || typeof tool.arguments.agent !== 'string') return null
  return tool.arguments.agent
}

function ConversationEntryView({ entry, subRuns, specTitles, currentTicketId, actions, messages }: {
  entry: ConversationEntry
  subRuns: ReadonlyMap<string, V3Message[]>
  specTitles: ReadonlyMap<string, string>
  currentTicketId?: string
  actions?: ConversationActionsConfig
  messages?: readonly V3Message[]
}): ReactNode {
  const withActions = (message: V3Message, node: ReactNode): ReactNode => (
    actions && messages
      ? (
        <MessageActions
          message={message}
          messages={messages}
          kind={actions.kind}
          running={actions.running}
          sessionId={actions.sessionId}
          ticketId={actions.ticketId}
          onResend={actions.onResend}
        >
          {node}
        </MessageActions>
      )
      : node
  )

  if (entry.kind === 'user') {
    return withActions(entry.message, <UserMessage text={entry.text} references={entry.references} messageId={entry.message.id} />)
  }
  if (entry.kind === 'event') {
    // 收件箱转出的消息也是合法回溯目标（role=user），但不可编辑。
    return withActions(entry.message, <InboxEventCard entry={entry} />)
  }
  if (entry.kind === 'assistant') {
    return withActions(entry.message, <AssistantText text={entry.message.content ?? ''} messageId={entry.message.id} />)
  }
  if (entry.kind === 'report') {
    return <ReportCallout status={entry.status} summary={entry.summary} humanAction={entry.humanAction} />
  }

  const agent = entryAgentName(entry.tool)
  if (entry.subRunId) {
    const childMessages = subRuns.get(entry.subRunId) ?? []
    const children = buildConversationEntries(childMessages, { mainSessionId: null, currentTicketId }).entries
    return (
      <DelegateToolBar
        tool={entry.tool}
        agentTitle={agent ? specTitles.get(agent) ?? agent : null}
        messageCount={childMessages.length}
      >
        {/* 子代理会话不支持回溯（§2 非目标），里面的消息不带动作菜单。 */}
        {children.map((child) => (
          <ConversationEntryView
            key={child.key}
            entry={child}
            subRuns={subRuns}
            specTitles={specTitles}
            currentTicketId={currentTicketId}
          />
        ))}
      </DelegateToolBar>
    )
  }

  const link = toolTicketLink(entry.tool)
  return (
    <div className="space-y-2">
      <ToolCallBar tool={entry.tool} />
      {link ? <TicketLinkChip ticketId={link.ticketId} title={link.title} /> : null}
    </div>
  )
}

interface ConversationGroup {
  key: string
  /** 这一组是连续的被取代消息：整组折叠、不显示动作菜单。 */
  superseded: boolean
  count: number
  entries: ConversationEntry[]
}

/**
 * 会话历史视图（M72 §4.6.3）：只收数据，不依赖 `useConversation`，旧会话查看器也能用。
 * 渲染顺序固定为「加载更早」→ 历史条目 → 实时副本 → 排队气泡 → leftovers → 错误。
 * M74 起检查点标记按 `insertMarkers` 插在历史条目之间；有标记时按标记把历史切块渲染，
 * 以免把工具调用与其结果拆到标记两侧。M80 起连续的被取代消息合并成一个折叠条。
 */
export function ConversationView({
  scope,
  history,
  mainSessionId,
  specTitles,
  checkpoints,
  live,
  showOptimisticUser,
  queued,
  resolver,
  onQuestionResolved,
  earlier,
  error,
  emptyState,
  leftovers,
  actions,
}: ConversationViewProps) {
  const currentTicketId = scope.kind === 'ticket' ? scope.ticketId : undefined
  const { blocks, subRuns, mainMessages } = useMemo(() => {
    const full = buildConversationEntries(history, { mainSessionId, currentTicketId })
    const main = mainSessionId
      ? history.filter((message) => message.session_id === mainSessionId)
      : [...history]
    const pending: { key: string; checkpoint: CheckpointView | null; messages: V3Message[] }[] = []
    let block: (typeof pending)[number] | null = null
    for (const item of insertMarkers(main, checkpoints ?? [])) {
      if (item.kind === 'checkpoint') {
        if (block) pending.push(block)
        block = { key: item.key, checkpoint: item.checkpoint, messages: [] }
      } else {
        if (!block) block = { key: item.key, checkpoint: null, messages: [] }
        block.messages.push(item.message)
      }
    }
    if (block) pending.push(block)

    // 连续的被取代消息合成一组（§4.6）；组内再走一次配对，边界不会切断工具批次
    // （回溯目标表不允许把一次调用与它的结果拆开）。
    const groupsOf = (messages: V3Message[]): ConversationGroup[] => {
      const runs: { key: string; superseded: boolean; messages: V3Message[] }[] = []
      for (const message of messages) {
        const superseded = Boolean(message.superseded_at)
        const last = runs.at(-1)
        if (last && last.superseded === superseded) last.messages.push(message)
        else runs.push({ key: `fold-${message.id}`, superseded, messages: [message] })
      }
      return runs.map((run) => ({
        key: run.key,
        superseded: run.superseded,
        count: run.messages.length,
        entries: buildConversationEntries(run.messages, { mainSessionId, currentTicketId }).entries,
      }))
    }

    return {
      blocks: pending.map((item) => ({
        key: item.key,
        checkpoint: item.checkpoint,
        groups: groupsOf(item.messages),
      })),
      subRuns: full.subRuns,
      mainMessages: main,
    }
  }, [history, mainSessionId, currentTicketId, checkpoints])
  const entryCount = blocks.reduce(
    (count, block) => count + block.groups.reduce((sum, group) => sum + group.entries.length, 0),
    0,
  )

  return (
    <div className="space-y-4">
      {earlier?.hasEarlier ? (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="rounded-full"
            loading={earlier.loading}
            onClick={earlier.onLoad}
          >
            {earlier.loading ? '正在加载…' : '加载更早的消息'}
          </Button>
        </div>
      ) : null}
      {earlier?.error ? (
        <InlineAlert tone="danger">读取更早的消息失败：{toApiError(earlier.error).message}</InlineAlert>
      ) : null}
      {blocks.map((block) => (
        <Fragment key={block.key}>
          {block.checkpoint ? <CompactionMarker checkpoint={block.checkpoint} /> : null}
          {block.groups.map((group) => (group.superseded && actions ? (
            <SupersededFold key={group.key} count={group.count}>
              {group.entries.map((entry) => (
                <ConversationEntryView
                  key={entry.key}
                  entry={entry}
                  subRuns={subRuns}
                  specTitles={specTitles}
                  currentTicketId={currentTicketId}
                />
              ))}
            </SupersededFold>
          ) : (
            <Fragment key={group.key}>
              {group.entries.map((entry) => (
                <ConversationEntryView
                  key={entry.key}
                  entry={entry}
                  subRuns={subRuns}
                  specTitles={specTitles}
                  currentTicketId={currentTicketId}
                  actions={actions}
                  messages={mainMessages}
                />
              ))}
            </Fragment>
          )))}
        </Fragment>
      ))}
      {entryCount === 0 && !live ? emptyState : null}
      {live && resolver ? (
        <LiveTurnView
          turn={live}
          showUser={showOptimisticUser ?? true}
          specTitles={specTitles}
          resolver={resolver}
          onQuestionResolved={onQuestionResolved ?? noop}
        />
      ) : null}
      {queued?.map((item) => (
        <UserMessage key={item.localId} text={item.text} references={[]} queued />
      ))}
      {leftovers}
      {error ? <ErrorAlert error={error} /> : null}
    </div>
  )
}
