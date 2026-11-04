import { useCallback, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { GitBranch, RefreshCw, SquarePen, Undo2 } from 'lucide-react'
import { keys } from '@/api/keys'
import { ActionMenu, type ActionSheetAction } from '@/components/ui/action-sheet'
import type { V3Message } from '@/features/console/conversation/types'
import { cn } from '@/lib/utils'
import { EditResend } from './EditResend'
import { ForkDialog } from './ForkDialog'
import { rewindEffectDescription, useRewindFlow } from './RewindDialog'
import { messageActions, type MessageKind } from './targets'

/** 运行中四个动作统一禁用的说明（M80 §4.6）。 */
export const RUNNING_DESCRIPTION = '正在运行，停下后再操作'

export interface MessageActionsProps {
  message: V3Message
  /** 同一会话的全部消息（含已被取代的），用来算 `toSeq` 与受影响的条数。 */
  messages: readonly V3Message[]
  kind: MessageKind
  /** 会话此刻真有一轮在跑：四个动作都禁用。 */
  running: boolean
  sessionId: string | null
  /** 工单页才有；「分叉」要用。 */
  ticketId?: string
  /** 重发用 M72 已有的发消息函数（总控/工单各自的 SSE 端点）。 */
  onResend: (text: string) => Promise<unknown> | unknown
  children: ReactNode
}

/**
 * 一条消息旁的动作菜单（M80 §4.6）：回到这里、重新生成、编辑后重发、分叉。
 *
 * 回溯与重新生成先 `dry_run` 再决定要不要弹确认框；「重新生成」在没有副作用时
 * 直接回溯并重发，不打扰用户。编辑把气泡换成 `Textarea`，分叉打开面板。
 */
export function MessageActions({
  message,
  messages,
  kind,
  running,
  sessionId,
  ticketId,
  onResend,
  children,
}: MessageActionsProps) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [forkOpen, setForkOpen] = useState(false)
  const actions = messageActions(messages, message, { kind, running })
  const { rewindTo, regenerate, edit, forkAt } = actions

  const onRewound = useCallback(() => {
    if (sessionId) {
      void queryClient.invalidateQueries({ queryKey: keys.agent.messages(sessionId) })
    }
    if (ticketId) {
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.messagesAll(ticketId) })
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.detail(ticketId) })
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() })
    } else {
      void queryClient.invalidateQueries({ queryKey: keys.agent.orchestrator.messages() })
      void queryClient.invalidateQueries({ queryKey: keys.agent.orchestrator.state() })
    }
  }, [queryClient, sessionId, ticketId])

  const rewind = useRewindFlow({ sessionId, onRewound })

  // 确认框里的「之后的 n 条」：本地窗口是尾部完整的，按 seq 数就行（不含已被取代的）。
  const countAfter = (seq: number) => messages.filter(
    (item) => item.seq > seq && !item.superseded_at,
  ).length

  const items: ActionSheetAction[] = []
  if (rewindTo !== undefined) {
    items.push({
      label: '回到这里',
      icon: Undo2,
      ...(running ? { description: RUNNING_DESCRIPTION } : {}),
      disabled: running,
      onSelect: () => {
        void rewind.run({
          toSeq: rewindTo,
          successToast: '已回溯',
          buildConfirm: (dryRun) => ({
            title: '回到这条消息？',
            description: rewindEffectDescription(
              `之后的 ${dryRun.superseded} 条消息会折叠，不再进上下文。`,
              dryRun.side_effects,
            ),
            confirmText: '回到这里',
          }),
        })
      },
    })
  }
  if (regenerate) {
    items.push({
      label: '重新生成',
      icon: RefreshCw,
      ...(running ? { description: RUNNING_DESCRIPTION } : {}),
      disabled: running,
      onSelect: () => {
        void (async () => {
          const result = await rewind.run({
            toSeq: regenerate.toSeq,
            buildConfirm: (dryRun) => (dryRun.side_effects.length === 0 ? null : {
              title: '重新生成这条回复？',
              description: rewindEffectDescription(
                `这条回复之后的 ${countAfter(message.seq)} 条消息会折叠。`,
                dryRun.side_effects,
              ),
              confirmText: '重新生成',
            }),
          })
          if (result) await onResend(regenerate.content)
        })()
      },
    })
  }
  if (edit) {
    items.push({
      label: '编辑后重发',
      icon: SquarePen,
      ...(running ? { description: RUNNING_DESCRIPTION } : {}),
      disabled: running,
      onSelect: () => setEditing(true),
    })
  }
  if (forkAt !== undefined && ticketId) {
    items.push({
      label: '分叉',
      icon: GitBranch,
      ...(running ? { description: RUNNING_DESCRIPTION } : {}),
      disabled: running,
      onSelect: () => setForkOpen(true),
    })
  }

  const submitEdit = async (text: string): Promise<boolean> => {
    if (!edit) return false
    const result = await rewind.run({
      toSeq: edit.toSeq,
      buildConfirm: (dryRun) => (dryRun.side_effects.length === 0 ? null : {
        title: '改写后重发？',
        description: rewindEffectDescription(
          `原消息和之后的 ${countAfter(message.seq)} 条消息会折叠。`,
          dryRun.side_effects,
        ),
        confirmText: '重发',
      }),
    })
    if (!result) return false
    setEditing(false)
    await onResend(text)
    return true
  }

  return (
    <div className="space-y-1">
      {editing && edit
        ? (
          <EditResend
            initialText={edit.content}
            pending={rewind.pending}
            onCancel={() => setEditing(false)}
            onSubmit={submitEdit}
          />
        )
        : children}
      {!editing && items.length > 0 ? (
        <div className={cn('flex', message.role === 'user' ? 'justify-end' : 'justify-start')}>
          <ActionMenu
            label="消息操作"
            title="消息操作"
            actions={items}
            variant="ghost"
            size="icon-sm"
          />
        </div>
      ) : null}
      {rewind.dialog}
      {forkAt !== undefined && ticketId ? (
        <ForkDialog open={forkOpen} onOpenChange={setForkOpen} ticketId={ticketId} atSeq={forkAt} />
      ) : null}
    </div>
  )
}
