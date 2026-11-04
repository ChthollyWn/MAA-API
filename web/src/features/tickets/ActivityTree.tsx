import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Badge, type StatusTone } from '@/components/ui/badge'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { runDetailQuery, ticketRunsQuery } from './api'
import {
  FINISH_REASON_LABELS,
  RUN_KIND_LABELS,
  RUN_STATUS_LABELS,
  formatCost,
  formatDuration,
  workerTitle,
  type RunNode,
} from './format'

export interface ActivityTreeProps {
  ticketId: string
  specTitles: ReadonlyMap<string, string>
}

const RUN_TONES: Record<string, StatusTone> = {
  running: 'primary',
  waiting_human: 'warning',
  succeeded: 'success',
  failed: 'danger',
  interrupted: 'neutral',
  cancelled: 'neutral',
}

/** 运行详情与子运行的树最多三层（服务端同口径），更深的节点不再给展开按钮。 */
const MAX_DEPTH = 3

function ActivityNode({ node, depth, specTitles }: {
  node: RunNode
  depth: number
  specTitles: ReadonlyMap<string, string>
}) {
  const [expanded, setExpanded] = useState(false)
  const detail = useQuery({ ...runDetailQuery(node.runId), enabled: expanded })
  const children = detail.data?.children ?? node.children
  const agent = workerTitle(node.agent, specTitles)
  const kind = RUN_KIND_LABELS[node.kind] ?? node.kind
  const status = RUN_STATUS_LABELS[node.status] ?? node.status
  const reason = node.finishReason
    ? FINISH_REASON_LABELS[node.finishReason] ?? node.finishReason
    : null
  const canExpand = depth < MAX_DEPTH

  return (
    <li>
      <div
        className="rounded-xl border bg-card px-3 py-2.5 shadow-card"
        style={{ marginLeft: depth * 16 }}
      >
        <div className="flex items-start gap-2">
          {canExpand ? (
            <button
              type="button"
              aria-expanded={expanded}
              aria-label={`${expanded ? '收起' : '展开'}「${agent} · ${kind}」的运行`}
              onClick={() => setExpanded((current) => !current)}
              className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronRight
                aria-hidden="true"
                className={expanded ? 'size-4 rotate-90 transition-transform' : 'size-4 transition-transform'}
              />
            </button>
          ) : (
            <span className="size-6 shrink-0" aria-hidden="true" />
          )}
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-sm font-medium">{agent} · {kind}</span>
              <Badge variant={RUN_TONES[node.status] ?? 'neutral'}>{status}</Badge>
              {reason ? <span className="text-footnote text-muted-foreground">{reason}</span> : null}
            </div>
            <p className="text-footnote text-muted-foreground tabular-nums">
              {node.steps} 步 · 输入 {node.promptTokens}（缓存 {node.cachedTokens}）/ 输出 {node.completionTokens} token
              {' · '}
              {formatCost(node.costMicros, node.currency)}
              {' · '}
              {formatDuration(node.startedAt, node.finishedAt)}
            </p>
          </div>
          <Link
            to={`/more/runs/${node.runId}`}
            className="shrink-0 text-footnote font-medium text-primary-text underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            查看追踪
          </Link>
        </div>
        {expanded ? (
          <div className="mt-2 space-y-2 border-t pt-2">
            {detail.isPending ? <LoadingState label="正在读取运行…" /> : null}
            {detail.error ? (
              <InlineAlert tone="danger">读取运行详情失败：{toApiError(detail.error).message}</InlineAlert>
            ) : null}
            {!detail.isPending && !detail.error && children.length === 0 ? (
              <p className="py-1 text-center text-footnote text-muted-foreground">没有子运行</p>
            ) : null}
            {children.length > 0 ? (
              <ul className="space-y-2">
                {children.map((child) => (
                  <ActivityNode key={child.runId} node={child} depth={depth + 1} specTitles={specTitles} />
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  )
}

/** 活动树（M72 §4.6.5）：根运行列表，展开读详情并递归渲染 children。 */
export function ActivityTree({ ticketId, specTitles }: ActivityTreeProps) {
  const runs = useQuery(ticketRunsQuery(ticketId))
  const items = runs.data ?? []

  return (
    <Section title="活动">
      {runs.isPending ? (
        <LoadingState label="正在读取运行记录…" />
      ) : runs.error ? (
        <ErrorState
          message={`读取运行记录失败：${toApiError(runs.error).message}`}
          onRetry={() => void runs.refetch()}
          retrying={runs.isFetching}
        />
      ) : items.length === 0 ? (
        <EmptyState title="还没有运行记录" />
      ) : (
        <ul aria-label="活动树" className="space-y-2">
          {items.map((run) => (
            <ActivityNode key={run.runId} node={run} depth={0} specTitles={specTitles} />
          ))}
        </ul>
      )}
    </Section>
  )
}
