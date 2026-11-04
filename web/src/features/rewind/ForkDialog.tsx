import { useId, useState } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Button } from '@/components/ui/button'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import { streamSse } from '@/features/agent/stream'
import { forkTicket } from './api'

export interface ForkDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  ticketId: string
  /** 分叉处的消息 seq（含）。 */
  atSeq: number
}

/**
 * 分叉工单（M80 §4.6）：面板里写一句接下来怎么做，分叉成功后把它发给新工单，
 * 然后跳到新工单详情。
 */
export function ForkDialog({ open, onOpenChange, ticketId, atSeq }: ForkDialogProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const textareaId = useId()
  const [text, setText] = useState('')

  const fork = useMutation({
    mutationFn: async () => forkTicket(ticketId, { at_seq: atSeq }),
    onSuccess: async (detail) => {
      const newTicketId = detail.ticket.id
      const content = text.trim()
      if (content) {
        try {
          await streamSse(
            `/api/agent/tickets/${encodeURIComponent(newTicketId)}/messages`,
            { content, mentions: [] },
            new AbortController().signal,
            () => {},
            { detach: true },
          )
        } catch (error) {
          toast.error(`发送消息失败：${toApiError(error).message}`)
        }
      }
      onOpenChange(false)
      setText('')
      toast.success('已分叉')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(ticketId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.tickets.messagesAll(ticketId) }),
        queryClient.invalidateQueries({ queryKey: keys.agent.orchestrator.state() }),
      ])
      void navigate(`/tickets/${encodeURIComponent(newTicketId)}`)
    },
    onError: (error) => {
      toast.error(`分叉失败：${toApiError(error).message}`)
    },
  })

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="分叉工单"
      description="新工单带着到这条消息为止的记录和同一份委托，原工单不变。"
      size="sm"
      footer={(
        <div className="grid grid-cols-2 gap-2">
          <Button type="button" variant="outline" disabled={fork.isPending} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" loading={fork.isPending} onClick={() => fork.mutate()}>
            分叉工单
          </Button>
        </div>
      )}
    >
      <div className="space-y-2 py-2">
        <label htmlFor={textareaId} className="block text-sm font-medium">接下来怎么做</label>
        <Textarea
          id={textareaId}
          value={text}
          placeholder="例如：换成 4 星干员再试一次"
          onChange={(event) => setText(event.target.value)}
        />
      </div>
    </ResponsiveSheet>
  )
}
