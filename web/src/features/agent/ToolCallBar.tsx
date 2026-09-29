import { useMemo } from 'react'
import { ChevronDown, LoaderCircle, Wrench } from 'lucide-react'
import { cn } from '@/lib/utils'
import { pretty, toolLabel } from './format'
import { MediaCards } from './MediaCards'
import { ToolAttachmentView } from './ToolAttachment'
import { mediaCardsOf, toolScreenshotAttachment } from './media'
import type { ToolStatus, ToolView } from './transcript'

const statusText: Record<ToolStatus, { label: string; className: string }> = {
  running: { label: '执行中', className: 'text-primary-text' },
  awaiting: { label: '等待确认', className: 'text-warning' },
  done: { label: '已完成', className: 'text-success' },
  failed: { label: '失败', className: 'text-danger' },
  missing: { label: '未返回结果', className: 'text-muted-foreground' },
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs leading-5">{pretty(value)}</pre>
    </div>
  )
}

/** 一次工具调用的紧凑折叠条：工具名与状态，展开看截图附件、参数与结果 JSON。 */
export function ToolCallBar({ tool }: { tool: ToolView }) {
  const status = statusText[tool.status]
  const label = tool.standalone ? undefined : toolLabel(tool.name)
  // 媒体卡是「发给用户看」的内容，必须渲染在 `<details>` 之外：工具条整体默认折叠，
  // 放里面就等于看不见。截图归档（`get_screenshot`）反过来只当工具卡内的附件。
  const cards = useMemo(() => mediaCardsOf(tool), [tool.result])
  const attachment = useMemo(() => toolScreenshotAttachment(tool.result), [tool.result])
  return (
    <div className="space-y-2">
      {cards.length > 0 ? <MediaCards cards={cards} /> : null}
      <details className="group rounded-xl border bg-card">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2.5 rounded-xl px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <Wrench aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
          <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
            {tool.standalone ? (
              <span className="truncate text-sm font-medium">工具结果 · {tool.name}</span>
            ) : (
              <>
                {label ? <span className="text-sm font-medium">{label}</span> : null}
                <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">{tool.name}</span>
              </>
            )}
          </span>
          <span className={cn('flex shrink-0 items-center gap-1 text-xs font-medium', status.className)}>
            {tool.status === 'running' ? <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none" /> : null}
            {status.label}
          </span>
          <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-180" />
        </summary>
        <div className="space-y-3 border-t px-3 py-3">
          {tool.errorMessage ? <p className="break-words text-sm text-danger">{tool.errorMessage}</p> : null}
          {attachment ? <ToolAttachmentView attachment={attachment} /> : null}
          {tool.standalone ? null : <JsonBlock label="参数" value={tool.arguments ?? {}} />}
          {tool.result !== undefined ? <JsonBlock label="结果" value={tool.result} /> : null}
          {tool.auditId ? <p className="text-xs text-muted-foreground tabular-nums">审计 #{tool.auditId}</p> : null}
        </div>
      </details>
    </div>
  )
}
