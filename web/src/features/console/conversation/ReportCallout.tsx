import { Badge } from '@/components/ui/badge'
import type { HumanAction } from './types'

export interface ReportCalloutProps {
  status: string
  summary: string
  humanAction: HumanAction | null
}

/** 汇报状态文案（M72 §4.6.5 的取值表；M72 T9 的 `tickets/format.ts` 建好后可统一）。 */
const REPORT_STATUS_LABELS: Record<string, string> = {
  progress: '进展',
  waiting: '等你处理',
  ready: '已完成',
  blocked: '受阻',
  failed: '失败',
  dropped: '已放弃',
}

const ACTION_LABELS: Record<HumanAction['type'], string> = {
  confirmation: '需要你确认',
  question: '需要你回答',
  decision: '需要你决定',
}

const STATUS_VARIANTS: Record<string, 'primary' | 'warning' | 'success' | 'danger' | 'neutral'> = {
  progress: 'primary',
  waiting: 'warning',
  ready: 'success',
  blocked: 'danger',
  failed: 'danger',
  dropped: 'neutral',
}

/** 汇报条目：Worker 每轮结束时写的一条摘要；挂在工具参数上的汇报不再画工具条。 */
export function ReportCallout({ status, summary, humanAction }: ReportCalloutProps) {
  return (
    <article className="rounded-xl border bg-card px-3.5 py-3 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">汇报</span>
        <Badge variant={STATUS_VARIANTS[status] ?? 'neutral'}>{REPORT_STATUS_LABELS[status] ?? status}</Badge>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{summary}</p>
      {humanAction ? (
        <p className="mt-1.5 break-words text-footnote text-warning">
          {ACTION_LABELS[humanAction.type]}：{humanAction.prompt}
        </p>
      ) : null}
    </article>
  )
}
