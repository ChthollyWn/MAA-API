import { Trash2 } from 'lucide-react'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatSessionTime, sessionTitle } from './format'
import type { AgentSession } from './types'

export interface SessionListProps {
  sessions: readonly AgentSession[]
  selectedId: string | null
  onSelect: (sessionId: string) => void
  onDelete: (session: AgentSession) => void
  deletingId: string | null
}

/** 会话列表：标题、自动诊断徽章、消息数、模型与更新时间；删除收在「更多操作」里。 */
export function SessionList({ sessions, selectedId, onSelect, onDelete, deletingId }: SessionListProps) {
  return (
    <ul className="space-y-0.5">
      {sessions.map((session) => {
        const title = sessionTitle(session)
        const selected = session.session_id === selectedId
        const updatedAt = session.last_message_at ?? session.updated_at
        const time = formatSessionTime(updatedAt)
        return (
          <li key={session.session_id} className={cn('flex items-center gap-1 rounded-xl pr-1 transition-colors', selected ? 'bg-primary-soft' : 'hover:bg-surface-2/70')}>
            <button
              type="button"
              aria-current={selected ? 'page' : undefined}
              onClick={() => onSelect(session.session_id)}
              className="min-h-14 min-w-0 flex-1 rounded-xl px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <span className="flex items-baseline gap-2">
                <span className={cn('min-w-0 flex-1 truncate text-[0.9375rem] font-medium', selected && 'text-primary-text')}>{title}</span>
                {time ? <time dateTime={updatedAt ?? undefined} className="shrink-0 text-xs text-muted-foreground tabular-nums">{time}</time> : null}
              </span>
              <span className="mt-1 flex min-w-0 items-center gap-1.5">
                {session.incident_key ? <Badge variant="warning">自动诊断</Badge> : null}
                <span className="min-w-0 truncate text-footnote text-muted-foreground">{session.message_count ?? 0} 条消息 · {session.model || '模型未配置'}</span>
              </span>
            </button>
            <ActionMenu
              label={`会话「${title}」的更多操作`}
              title="会话操作"
              description={title}
              disabled={deletingId === session.session_id}
              actions={[{ label: '删除会话', icon: Trash2, tone: 'danger', onSelect: () => onDelete(session) }]}
            />
          </li>
        )
      })}
    </ul>
  )
}
