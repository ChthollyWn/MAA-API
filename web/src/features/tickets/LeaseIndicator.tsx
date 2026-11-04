import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { toast } from '@/components/ui/toaster'
import type { LeaseView } from './format'

export interface LeaseIndicatorProps {
  ticketId: string
  lease: LeaseView | undefined
  /** 别的工单占着设备时，那个工单的标题（来自它的详情查询）。 */
  holderTitle: string | null
}

function twoDigits(value: number): string {
  return String(value).padStart(2, '0')
}

/** 租约剩余时间 `mm:ss`；没有有效期时返回 null。 */
function remainingLabel(expiresAt: string | null, now: number): string | null {
  if (!expiresAt) return null
  const expires = Date.parse(expiresAt)
  if (!Number.isFinite(expires)) return null
  const seconds = Math.max(0, Math.floor((expires - now) / 1000))
  return `${twoDigits(Math.floor(seconds / 60))}:${twoDigits(seconds % 60)}`
}

/**
 * 设备租约指示（M72 §4.6.5）：本工单持有 → 用途、剩余时间与「释放设备」；
 * 别的工单持有 → 只写持有者；没有租约 → 不渲染。
 */
export function LeaseIndicator({ ticketId, lease, holderTitle }: LeaseIndicatorProps) {
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const [now, setNow] = useState(() => Date.now())
  const own = lease?.held === true && lease.ticketId === ticketId
  const expiresAt = lease?.expiresAt ?? null

  useEffect(() => {
    if (!own || !expiresAt) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [own, expiresAt])

  const release = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE('/api/agent/device-lease')
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: keys.agent.deviceLease() })
      toast.success('已释放设备')
    },
  })

  if (!lease?.held) return null

  if (!own) {
    return (
      <p className="rounded-xl border bg-surface-2/60 px-3.5 py-3 text-sm text-muted-foreground">
        设备正被工单「{holderTitle ?? '其他工单'}」使用
      </p>
    )
  }

  const remaining = remainingLabel(expiresAt, now)
  const text = [
    '正在使用设备',
    lease.purpose,
    remaining ? `剩余 ${remaining}` : '',
  ].filter(Boolean).join(' · ')

  const confirmRelease = async () => {
    if (release.isPending) return
    const accepted = await confirm({
      title: '释放设备？',
      description: '持有设备的工单下次操作设备时会收到「设备已被接管」。',
      confirmText: '释放设备',
      tone: 'danger',
    })
    if (accepted) release.mutate()
  }

  return (
    <div className="space-y-2 rounded-xl border border-warning/30 bg-warning-soft/60 px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-warning">{text}</p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          loading={release.isPending}
          onClick={() => void confirmRelease()}
        >
          释放设备
        </Button>
      </div>
      {release.error ? (
        <InlineAlert tone="danger">释放设备失败：{toApiError(release.error).message}</InlineAlert>
      ) : null}
      {confirmElement}
    </div>
  )
}
