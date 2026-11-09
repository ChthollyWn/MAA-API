import { TriangleAlert } from 'lucide-react'
import { ConfirmationCard } from '@/features/agent/ConfirmationCard'
import { AssistantText, TypingIndicator } from '@/features/agent/MessageViews'
import { QuestionCard } from '@/features/agent/QuestionCard'
import { liveToolView } from '@/features/agent/transcript'
import { ViewArtifactCard } from '@/features/artifacts/ViewArtifactCard'
import { ToolCallBar } from '@/features/agent/ToolCallBar'
import type { ConfirmationResolver } from './card-resolvers'
import { DelegateToolBar } from './DelegateToolBar'
import type { LiveRun, LiveTurn } from './live-turn'
import { ReportCallout } from './ReportCallout'
import { TicketLinkChip } from './TicketLinkChip'
import { artifactIdOf } from './transcript-v3'
import type { Origin } from './types'
import { UserMessage } from './UserMessage'

export interface LiveTurnViewProps {
  turn: LiveTurn
  /** 本轮用户消息还没进历史时，画一个乐观气泡。 */
  showUser: boolean
  specTitles: ReadonlyMap<string, string>
  resolver: ConfirmationResolver
  onQuestionResolved: (questionId: string) => void
  /** 工单已结束：视图卡里的表单只读（M90 §4.6.4）。 */
  ticketEnded?: boolean
}

/** 来源行（M72 §4.6.3）：确认卡自己没有来源，由实时副本在卡片上方补一行。 */
function originText(origin: Origin, specTitles: ReadonlyMap<string, string>): string {
  const agent = origin.agent ? specTitles.get(origin.agent) ?? origin.agent : '代理'
  return origin.ticket_id
    ? `来自工单「${origin.ticket_title ?? '未命名'}」· ${agent}`
    : `来自${agent}`
}

function LiveRunView({ run, turn, specTitles, ticketEnded }: {
  run: LiveRun
  turn: LiveTurn
  specTitles: ReadonlyMap<string, string>
  ticketEnded: boolean
}) {
  return (
    <div className="space-y-3">
      {run.observedStart ? null : (
        <p className="text-footnote text-muted-foreground">接着处理之前的消息</p>
      )}
      {run.segments.map((segment, index) => {
        if (segment.kind === 'text') return <AssistantText key={`text-${index}`} text={segment.text} />
        if (segment.kind === 'report') {
          return (
            <ReportCallout
              key={`report-${segment.reportId}-${index}`}
              status={segment.status}
              summary={segment.summary}
              humanAction={segment.humanAction}
            />
          )
        }
        if (segment.kind === 'ticket') {
          return <TicketLinkChip key={`ticket-${segment.ticketId}-${index}`} ticketId={segment.ticketId} title={segment.title} created />
        }
        const tool = run.tools[segment.toolCallId]
        if (!tool) return null
        const view = liveToolView(tool, run.status === 'running')
        const child = run.delegateRuns[segment.toolCallId]
          ? turn.runs[run.delegateRuns[segment.toolCallId]!]
          : undefined
        if (child) {
          return (
            <DelegateToolBar
              key={`tool-${segment.toolCallId}`}
              tool={view}
              agentTitle={child.agent ? specTitles.get(child.agent) ?? child.agent : null}
              messageCount={child.segments.length}
            >
              <LiveRunView run={child} turn={turn} specTitles={specTitles} ticketEnded={ticketEnded} />
            </DelegateToolBar>
          )
        }
        const artifactId = artifactIdOf(view)
        return (
          <div key={`tool-${segment.toolCallId}`} className="space-y-2">
            <ToolCallBar tool={view} />
            {artifactId ? <ViewArtifactCard artifactId={artifactId} ticketEnded={ticketEnded} /> : null}
          </div>
        )
      })}
    </div>
  )
}

/**
 * 实时副本（M72 §4.6.3）：乐观气泡 → 根运行（中途加入的先写一行「接着处理之前的消息」）
 * → 挂起的确认卡 → 提问卡 → 运行中且没有挂起卡时的打字指示。
 */
export function LiveTurnView({ turn, showUser, specTitles, resolver, onQuestionResolved, ticketEnded = false }: LiveTurnViewProps) {
  return (
    <section aria-label="本轮消息" aria-busy={turn.running} className="space-y-3">
      {showUser ? <UserMessage text={turn.userText} references={[]} /> : null}
      {turn.rootOrder.map((runId) => {
        const run = turn.runs[runId]
        return run ? <LiveRunView key={runId} run={run} turn={turn} specTitles={specTitles} ticketEnded={ticketEnded} /> : null
      })}
      {turn.pendingConfirmation ? (
        <div className="space-y-2">
          {turn.pendingConfirmation.origin ? (
            <p className="text-footnote text-muted-foreground">
              {originText(turn.pendingConfirmation.origin, specTitles)}
            </p>
          ) : null}
          <p role="status" className="flex items-center gap-2 text-sm text-warning">
            <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
            等待你确认，操作还没有执行。
          </p>
          <ConfirmationCard
            confirmationId={turn.pendingConfirmation.confirmationId}
            busy={resolver.busyFor(turn.pendingConfirmation.confirmationId)}
            onResolve={resolver.resolve}
          />
        </div>
      ) : null}
      {turn.pendingQuestion ? (
        <QuestionCard
          key={turn.pendingQuestion.question_id}
          question={turn.pendingQuestion}
          onResolved={onQuestionResolved}
        />
      ) : null}
      {turn.running && !turn.pendingConfirmation && !turn.pendingQuestion ? <TypingIndicator /> : null}
    </section>
  )
}
