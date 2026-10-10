import { useMemo } from 'react'
import { ChevronDown, LoaderCircle, Wrench } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatFetchedAt, isRecord, parseToolSources, pretty, toolLabel, toolRegionNote, type ToolSource } from './format'
import { MediaCards } from './MediaCards'
import { ToolAttachmentView } from './ToolAttachment'
import { mediaCardsOf, toolScreenshotAttachment } from './media'
import type { ToolStatus, ToolView } from './transcript'

const statusText: Record<ToolStatus, { label: string; className: string }> = {
  running: { label: '执行中', className: 'text-primary-text' },
  awaiting: { label: '等待确认', className: 'text-warning' },
  done: { label: '完成', className: 'text-success' },
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

/** 参数先报个数，JSON 折在二级折叠区：一眼看到的是「这次调用干了什么」，不是参数表。 */
function ArgumentsBlock({ value }: { value: unknown }) {
  const count = isRecord(value) ? Object.keys(value).length : 0
  return (
    <details className="space-y-1">
      <summary className="list-none text-xs font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        {count > 0 ? `这次调用带了 ${count} 个参数` : '这次调用没有参数'}
      </summary>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs leading-5">{pretty(value)}</pre>
    </details>
  )
}

/** 一条来源：来源名、页面标题（白名单主机才给链接）、抓取时间与「缓存」/「旧数据」。 */
function SourceTag({ source }: { source: ToolSource }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className="font-medium text-foreground">{source.label}</span>
      {source.title ? (
        source.url ? (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer noopener"
            className="min-w-0 break-words text-primary-text underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {source.title}
          </a>
        ) : (
          <span className="min-w-0 break-words">{source.title}</span>
        )
      ) : null}
      {source.fetchedAt ? (
        <time dateTime={source.fetchedAt} className="tabular-nums text-muted-foreground">抓取于 {formatFetchedAt(source.fetchedAt)}</time>
      ) : null}
      {source.cached ? <Badge variant="neutral">缓存</Badge> : null}
      {source.stale ? <Badge variant="warning">旧数据</Badge> : null}
    </li>
  )
}

/**
 * 一次工具结果的来源标签（规格 §4.2、§5.12）：与媒体卡同一层级。工具条默认折叠，
 * 放进 `<details>` 就等于看不见。没有来源也没有外服提示时什么都不渲染。
 */
export function SourceTags({ sources, regionNote }: { sources: readonly ToolSource[]; regionNote?: string | null }) {
  if (sources.length === 0 && !regionNote) return null
  return (
    <div data-testid="tool-sources" className="space-y-1 rounded-xl border bg-card px-3 py-2 text-footnote">
      {sources.length > 0 ? (
        <ul aria-label="资料来源" className="space-y-1">
          {sources.map((source, index) => <SourceTag key={`${source.kind}-${source.label}-${index}`} source={source} />)}
        </ul>
      ) : null}
      {regionNote ? <p className="break-words text-warning">{regionNote}</p> : null}
    </div>
  )
}

/** 一次工具调用的紧凑折叠条：中文动作名与状态，展开看截图附件、英文函数名、参数与结果 JSON。 */
export function ToolCallBar({ tool }: { tool: ToolView }) {
  const status = statusText[tool.status]
  // 折叠条上只给中文动作名；认不出的工具才有必要露出英文函数名。
  const label = toolLabel(tool.name)
  const heading = tool.standalone ? `工具结果 · ${label ?? tool.name}` : label ?? tool.name
  // 媒体卡是「发给用户看」的内容，必须渲染在 `<details>` 之外：工具条整体默认折叠，
  // 放里面就等于看不见。截图归档（`get_screenshot`）反过来只当工具卡内的附件。
  const cards = useMemo(() => mediaCardsOf(tool), [tool.result])
  const attachment = useMemo(() => toolScreenshotAttachment(tool.result), [tool.result])
  // 来源标签同理（规格 §4.2）：它回答「这话是谁说的」，折叠起来就失去了意义。
  const sources = useMemo(() => parseToolSources(tool.result), [tool.result])
  const regionNote = useMemo(() => toolRegionNote(tool.result), [tool.result])
  return (
    <div className="space-y-2">
      {cards.length > 0 ? <MediaCards cards={cards} /> : null}
      <SourceTags sources={sources} regionNote={regionNote} />
      <details className="group rounded-xl border bg-card">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2.5 rounded-xl px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <Wrench aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
          <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
            <span className="min-w-0 truncate text-sm font-medium">{heading}</span>
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
          <p className="min-w-0 truncate font-mono text-xs text-muted-foreground">{tool.name}</p>
          {tool.standalone ? null : <ArgumentsBlock value={tool.arguments ?? {}} />}
          {tool.result !== undefined ? <JsonBlock label="结果" value={tool.result} /> : null}
          {tool.auditId ? <p className="text-xs text-muted-foreground tabular-nums">审计 #{tool.auditId}</p> : null}
        </div>
      </details>
    </div>
  )
}
