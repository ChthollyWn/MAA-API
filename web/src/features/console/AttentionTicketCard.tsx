import { useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import type { AgentQuestion } from '@/features/agent/types'
import { streamSse } from '@/features/agent/stream'
import { TICKET_STATUS_LABELS, expiredQuestionsQuery, type TicketView } from './api'

export interface AttentionTicketCardProps {
  ticket: TicketView
}

interface ChoiceOption {
  label: string
  description?: string | null
}

/**
 * 工单卡上的固定选项：`human_action.type` 能确定「确认/拒绝」语义时直接给按钮，
 * 免得让用户在输入框里自己组织一句裁决（维护性补丁 questions-no-expiry §4.6）。
 */
function quickOptions(type: string | null | undefined): ChoiceOption[] {
  if (type === 'confirmation') return [{ label: '批准' }, { label: '拒绝' }]
  if (type === 'decision') return [{ label: '同意' }, { label: '不同意' }]
  return []
}

/** 与该工单关联的最近一条历史失效提问的选项（按创建时间取最新的一条）。 */
function questionOptions(ticket: TicketView, questions: readonly AgentQuestion[]): ChoiceOption[] {
  if (ticket.status === 'waiting' || ticket.status === 'blocked') {
    const related = questions
      .filter((question) => question.origin?.ticket_id === ticket.id)
      .filter((question) => question.multiple === false && question.options.length > 0)
      .sort((left, right) => (right.created_at ?? '').localeCompare(left.created_at ?? ''))[0]
    if (related) return related.options
  }
  return []
}

/**
 * 「待你处理」里的工单条目（spec `console-attention-inline` §4.4、`questions-no-expiry` §4.6）：
 * 选项优先——先用关联历史提问的选项或 `human_action.type` 的固定按钮，再退到自由文本；
 * 所有答复都写成工单消息发给 Worker（SSE 只受理不读流），结果由 Worker 下一轮汇报刷新。
 */
export function AttentionTicketCard({ ticket }: AttentionTicketCardProps) {
  const [formOpen, setFormOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const queryClient = useQueryClient()
  const expired = useQuery(expiredQuestionsQuery)
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
      setFormOpen(false)
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
  const questionChoices = questionOptions(ticket, expired.data ?? [])
  const choices = questionChoices.length > 0 ? questionChoices : quickOptions(ticket.humanAction?.type)
  const showForm = formOpen || (choices.length === 0 && canReply)

  const sendChoice = (label: string) => {
    if (send.isPending) return
    send.mutate(label)
  }

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
      {choices.length > 0 ? (
        <ul aria-label="工单选项" className="mt-2 space-y-1.5">
          {choices.map((option) => (
            <li key={option.label}>
              <button
                type="button"
                disabled={send.isPending}
                onClick={() => sendChoice(option.label)}
                className="flex w-full items-start gap-3 rounded-lg border border-border bg-surface px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45"
              >
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-sm font-medium">{option.label}</span>
                  {option.description ? (
                    <span className="mt-0.5 block break-words text-footnote text-muted-foreground">{option.description}</span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {showForm ? (
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
            {choices.length > 0 ? (
              <Button size="sm" variant="outline" disabled={send.isPending} onClick={() => setFormOpen(false)}>
                取消
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {choices.length > 0 && canReply ? (
          <Button size="sm" variant="soft" disabled={send.isPending} onClick={() => setFormOpen((value) => !value)}>
            其他回复
          </Button>
        ) : null}
        <Link
          to={`/tickets/${ticket.id}`}
          className="text-footnote text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          打开工单
        </Link>
      </div>
    </article>
  )
}
