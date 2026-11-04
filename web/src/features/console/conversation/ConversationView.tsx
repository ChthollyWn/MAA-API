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
}

const noop = () => {}

function entryAgentName(tool: ToolView): string | null {
  if (!isRecord(tool.arguments) || typeof tool.arguments.agent !== 'string') return null
  return tool.arguments.agent
}

function ConversationEntryView({ entry, subRuns, specTitles, currentTicketId }: {
  entry: ConversationEntry
  subRuns: ReadonlyMap<string, V3Message[]>
  specTitles: ReadonlyMap<string, string>
  currentTicketId?: string
}): ReactNode {
  if (entry.kind === 'user') {
    return <UserMessage text={entry.text} references={entry.references} messageId={entry.message.id} />
  }
  if (entry.kind === 'event') return <InboxEventCard entry={entry} />
  if (entry.kind === 'assistant') {
    return <AssistantText text={entry.message.content ?? ''} messageId={entry.message.id} />
  }
  if (entry.kind === 'report') {
    return <ReportCallout status={entry.status} summary={entry.summary} humanAction={entry.humanAction} />
  }

  const agent = entryAgentName(entry.tool)
  if (entry.subRunId) {
    const messages = subRuns.get(entry.subRunId) ?? []
    const children = buildConversationEntries(messages, { mainSessionId: null, currentTicketId }).entries
    return (
      <DelegateToolBar
        tool={entry.tool}
        agentTitle={agent ? specTitles.get(agent) ?? agent : null}
        messageCount={messages.length}
      >
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

/**
 * 会话历史视图（M72 §4.6.3）：只收数据，不依赖 `useConversation`，旧会话查看器也能用。
 * 渲染顺序固定为「加载更早」→ 历史条目 → 实时副本 → 排队气泡 → leftovers → 错误。
 * M74 起检查点标记按 `insertMarkers` 插在历史条目之间；有标记时按标记把历史切块渲染，
 * 以免把工具调用与其结果拆到标记两侧。
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
}: ConversationViewProps) {
  const currentTicketId = scope.kind === 'ticket' ? scope.ticketId : undefined
  const { blocks, subRuns } = useMemo(() => {
    const full = buildConversationEntries(history, { mainSessionId, currentTicketId })
    const main = mainSessionId
      ? history.filter((message) => message.session_id === mainSessionId)
      : history
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
    return {
      blocks: pending.map((entry) => ({
        ...entry,
        entries: buildConversationEntries(entry.messages, { mainSessionId, currentTicketId }).entries,
      })),
      subRuns: full.subRuns,
    }
  }, [history, mainSessionId, currentTicketId, checkpoints])
  const entryCount = blocks.reduce((count, block) => count + block.entries.length, 0)

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
          {block.entries.map((entry) => (
            <ConversationEntryView
              key={entry.key}
              entry={entry}
              subRuns={subRuns}
              specTitles={specTitles}
              currentTicketId={currentTicketId}
            />
          ))}
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
