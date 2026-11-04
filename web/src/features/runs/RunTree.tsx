import { Link } from 'react-router'
import { Badge } from '@/components/ui/badge'
import type { RunDetail } from '@/features/runs/api'
import {
  agentTitle, finishReasonLabel, formatDuration, formatMicros, formatTokens,
  runDurationMs, runKindLabel, runStatusLabel, runStatusTone,
} from '@/features/runs/format'
import { cn } from '@/lib/utils'

interface RunTreeProps {
  run: RunDetail
  /** 当前页面打开的运行；高亮它，其余节点链到各自详情。 */
  currentRunId: string
  titles: ReadonlyMap<string, string>
}

/** 运行树：递归画出子运行，当前运行高亮。深度由后端限制在 3 层。 */
export function RunTree({ run, currentRunId, titles }: RunTreeProps) {
  const renderNode = (node: RunDetail) => {
    const current = node.run_id === currentRunId
    const duration = runDurationMs(node)
    return (
      <li key={node.run_id}>
        <Link
          to={`/more/runs/${node.run_id}`}
          aria-current={current ? 'page' : undefined}
          className={cn(
            'block rounded-lg border p-3 transition-colors hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            current ? 'border-primary/40 bg-primary-soft/50' : 'border-border bg-card',
          )}
        >
          <span className="flex items-start justify-between gap-2">
            <span className="min-w-0 truncate text-sm font-semibold">{agentTitle(node.agent_name, titles)}</span>
            <Badge variant={runStatusTone(node.status)}>{runStatusLabel(node.status)}</Badge>
          </span>
          <span className="mt-0.5 block text-footnote text-muted-foreground">
            {runKindLabel(node.kind)} · {node.steps} 步 · {formatDuration(duration)} · {formatMicros(node.cost_micros, node.currency)}
          </span>
          <span className="mt-0.5 block text-footnote text-muted-foreground tabular-nums">
            {formatTokens(node.prompt_tokens)} 输入 · {formatTokens(node.cached_tokens)} 缓存 · {formatTokens(node.completion_tokens)} 输出
            {node.finish_reason ? ` · ${finishReasonLabel(node.finish_reason)}` : ''}
          </span>
        </Link>
        {node.children && node.children.length > 0 ? (
          <ol className="ml-3 mt-2 space-y-2 border-l border-border pl-3">
            {node.children.map(renderNode)}
          </ol>
        ) : null}
      </li>
    )
  }

  return <ol className="space-y-2" aria-label="运行树">{renderNode(run)}</ol>
}

export default RunTree
