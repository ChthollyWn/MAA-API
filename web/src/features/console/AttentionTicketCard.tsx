import { useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import { streamSse } from '@/features/agent/stream'
import { TICKET_STATUS_LABELS, type TicketView } from './api'

export interface AttentionTicketCardProps {
  ticket: TicketView
}

/**
 * 「待你处理」里的工单条目（spec `console-attention-inline` §4.4）：
 * 标题可点开工单；有 `human_action.prompt` 时就地回复（写成工单消息，发给 Worker），
 * 回复走 SSE 但本端不读流（detach），发送结果由 Worker 下一轮汇报与 WS 失效刷新。
 */
export function AttentionTicketCard({ ticket }: AttentionTicketCardProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const queryClient = useQueryClient()
  const send = useMutation({
    mutationFn: async (content: string) => {
      await streamSse(
        `/api/agent/tickets/${encodeURIComponent(ticket.id)}/messages`,
        { content },
        new AbortController().signal,
        () => {},
        { detach: true },
      )
    },
    onSuccess: async () => {
      setDraft('')
      setOpen(false)
      toast.success('已发给工单')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.messagesAll(ticket.id) }),
      ])
    },
    onError: async (error) => {
      // 工单已结束时本地卡片要尽快按刷新结果消失/更新。
      if (toApiError(error).status === 409) {
        await queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() })
      }
    },
  })
  const failure = send.error ? toApiError(send.error) : null
  const prompt = ticket.humanAction?.prompt ?? ticket.summary
  const canReply = Boolean(ticket.humanAction?.prompt)

  return (
    <article className="rounded-xl border bg-card px-3.5 py-3 shadow-card">
      <header className="flex items-start justify-between gap-2">
        <Link
          to={`/tickets/${ticket.id}`}
          className="min-w-0 break-words text-sm font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {ticket.title}
        </Link>
        <Badge variant={ticket.status === 'blocked' ? 'danger' : 'warning'}>
          {TICKET_STATUS_LABELS[ticket.status] ?? ticket.status}
        </Badge>
      </header>
      {prompt ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-footnote text-muted-foreground">{prompt}</p>
      ) : null}
      {failure ? <InlineAlert tone="danger" className="mt-2">发送消息失败：{failure.message}</InlineAlert> : null}
      {open ? (
        <div className="mt-2 space-y-2">
          <Textarea
            aria-label="回复工单"
            rows={2}
            maxLength={32_000}
            placeholder="回复工单…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              aria-label="发送回复"
              loading={send.isPending}
              disabled={send.isPending || draft.trim().length === 0}
              onClick={() => send.mutate(draft.trim())}
            >
              发送
            </Button>
            <Button size="sm" variant="outline" disabled={send.isPending} onClick={() => setOpen(false)}>
              取消
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {canReply ? (
            <Button size="sm" variant="soft" onClick={() => setOpen(true)}>
              回复
            </Button>
          ) : null}
          <Link
            to={`/tickets/${ticket.id}`}
            className="text-footnote text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            打开工单
          </Link>
        </div>
      )}
    </article>
  )
}
