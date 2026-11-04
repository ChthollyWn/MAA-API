import { Link } from 'react-router'
import { ArrowUpRight, Flag, Info, Radio, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ConversationEntry, EventKind } from './transcript-v3'

type EventEntry = Extract<ConversationEntry, { kind: 'event' }>

export interface InboxEventCardProps {
  entry: EventEntry
}

const EVENT_TITLES: Record<EventKind, string> = {
  report: '工单汇报',
  thread_event: '事件',
  system: '系统消息',
  trigger: '事件',
}

const EVENT_ICONS: Record<EventKind, typeof Flag> = {
  report: Flag,
  thread_event: Radio,
  system: Info,
  trigger: TriangleAlert,
}

/** 收件箱转出的消息（工单汇报、事件、系统通知）：卡片而不是用户气泡（M72 §4.5.4）。 */
export function InboxEventCard({ entry }: InboxEventCardProps) {
  const Icon = EVENT_ICONS[entry.eventKind]
  return (
    <article className="rounded-xl border bg-surface-2/60 px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
        <h3 className="text-sm font-medium text-foreground">{EVENT_TITLES[entry.eventKind]}</h3>
        {entry.link ? (
          <Link
            to={entry.link.to}
            className={cn(
              'ml-auto inline-flex items-center gap-0.5 text-footnote font-medium text-primary-text',
              'underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            )}
          >
            {entry.link.label}
            <ArrowUpRight aria-hidden="true" className="size-3.5" />
          </Link>
        ) : null}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{entry.body}</p>
    </article>
  )
}
