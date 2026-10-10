import { ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { grantRemainingLabel } from '@/features/agent/format'
import type { CopilotGrantView } from './api'
import { successLabel, supportLabel } from './format'

/**
 * 作业授权条（规格 §9.6、§9.10）：与屏幕操作授权条并列，显示授权的关卡、剩余次数、
 * 剩余时间与「撤销」。只是展示层——数据由调用方从会话 wire 的 `copilot_grant` 取，
 * `now` 由调用方的计时器推进（和 `GrantBar` 一样的用法）。
 */
export interface CopilotGrantBarProps {
  grant: CopilotGrantView
  /** 当前时刻（毫秒）；不推进的话剩余时间是静止的。 */
  now: number
  revoking: boolean
  onRevoke: () => void
}

export function CopilotGrantBar({ grant, now, revoking, onRevoke }: CopilotGrantBarProps) {
  const remaining = grant.expiresAt ? grantRemainingLabel(grant.expiresAt, now) : null
  const attempts = grant.attemptsTotal > 0
    ? `剩余 ${grant.remainingAttempts}/${grant.attemptsTotal} 次`
    : '剩余次数未知'
  const stage = grant.stageCode ?? grant.stageId
  return (
    <div
      data-testid="copilot-grant-bar"
      className="flex min-h-11 items-center gap-2.5 rounded-xl bg-primary-soft py-0.5 pl-3.5 pr-1 text-sm"
    >
      <ShieldCheck aria-hidden="true" className="size-[1.125rem] shrink-0 text-primary-text" />
      <p
        className="min-w-0 flex-1 truncate"
        title={`${successLabel(grant.success)} · ${supportLabel(grant.support)}`}
      >
        <span className="font-medium">已授权作业尝试</span>
        <span aria-hidden="true" className="px-1.5 text-muted-foreground">·</span>
        <span className="text-muted-foreground tabular-nums">
          {stage ? `关卡 ${stage}` : '关卡未标明'} · {attempts} · {remaining ?? '已过期'}
        </span>
      </p>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label="撤销作业授权"
        loading={revoking}
        onClick={onRevoke}
      >
        撤销
      </Button>
    </div>
  )
}
