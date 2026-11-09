import { useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Spinner } from '@/components/ui/states'
import {
  KIND_LABELS,
  attentionMemoriesQuery,
  describeMemoryError,
  invalidateMemories,
  reviewMemory,
  type MemoryItem,
} from '@/features/memory/api'

export interface AttentionMemoryCardProps {
  count: number
  previews: readonly string[]
}

/**
 * 「待你处理」里的记忆条目（spec `console-attention-inline` §4.3）：
 * 折叠时是 N 条待审核的汇总，展开后逐条批准/拒绝；改后批准与编辑留在记忆页。
 */
export function AttentionMemoryCard({ count, previews }: AttentionMemoryCardProps) {
  const [expanded, setExpanded] = useState(false)
  const queryClient = useQueryClient()
  const memories = useQuery(attentionMemoriesQuery)
  const pending = (memories.data ?? []).filter((memory) => memory.status === 'proposed')
  const review = useMutation({
    mutationFn: (input: { memory: MemoryItem; approved: boolean }) =>
      reviewMemory(input.memory.id, { approved: input.approved }),
    onSuccess: () => invalidateMemories(queryClient),
  })

  const busyFor = (id: string) => review.isPending && review.variables?.memory.id === id
  const errorFor = (id: string) => {
    if (!review.error || review.variables?.memory.id !== id) return null
    return describeMemoryError(review.error)
  }

  return (
    <article className="rounded-xl border bg-card px-3.5 py-3 shadow-card">
      <header className="flex items-start justify-between gap-2">
        <span className="min-w-0 break-words text-sm font-medium text-foreground">{count} 条记忆待审核</span>
        <Badge variant="primary">记忆</Badge>
      </header>
      {expanded ? null : previews.map((preview) => (
        <span key={preview} className="mt-1 block truncate text-footnote text-muted-foreground">
          {preview}
        </span>
      ))}
      {expanded ? (
        <ul className="mt-3 space-y-2.5">
          {pending.map((memory) => (
            <li key={memory.id} data-memory-id={memory.id} className="rounded-lg bg-surface-2 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{KIND_LABELS[memory.kind]}</Badge>
                {memory.subject ? <Badge variant="outline">{memory.subject}</Badge> : null}
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words text-sm">{memory.content}</p>
              {errorFor(memory.id) ? (
                <InlineAlert tone="danger" className="mt-2">{errorFor(memory.id)}</InlineAlert>
              ) : null}
              <div className="mt-2 flex items-center gap-2">
                <Button size="sm" disabled={busyFor(memory.id)} onClick={() => review.mutate({ memory, approved: true })}>
                  批准
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyFor(memory.id)}
                  onClick={() => review.mutate({ memory, approved: false })}
                >
                  拒绝
                </Button>
                {busyFor(memory.id) ? <Spinner className="text-muted-foreground" /> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="soft" onClick={() => setExpanded((value) => !value)}>
          {expanded ? '收起' : '展开审核'}
        </Button>
        <Link
          to="/more/memories"
          className="text-footnote text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          去记忆页
        </Link>
      </div>
    </article>
  )
}
