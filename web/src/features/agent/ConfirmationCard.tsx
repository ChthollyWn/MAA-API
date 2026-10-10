import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ShieldAlert, X } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { diffValueText, eliteLabel, fieldLabel } from '@/features/account/format'
import { CopilotGrantCard, parseCopilotGrantPayload, type CopilotGrantOptions } from '@/features/copilots/CopilotGrantCard'
import { expiryLabel, pretty } from './format'
import type { AgentConfirmation } from './types'
export interface ConfirmationCardProps {
  confirmationId: string
  /** 正在提交的是哪个决定；两个按钮都会禁用，只在被点的那个上显示加载。 */
  busy: 'approve' | 'reject' | null
  /**
   * 决定结果。第三个参数只有 `grant_copilot_attempts` 会带：用户在卡片上改过的
   * 尝试次数 / 成功标准 / 是否允许助战（规格 §9.6）。调用方必须把这三项送进
   * `POST /api/confirmations/{confirmation_id}` 的覆盖参数里，否则它们会被忽略。
   */
  onResolve: (id: string, approved: boolean, overrides?: CopilotGrantOptions) => void
}

/**
 * 账号数据动作的卡片标题（规格 §7.4、§7.7）；这两类动作不把 payload 当普通 JSON 展开，
 * 而是渲染逐字段差异 / 计划摘要。
 */
const ACCOUNT_TOOL_TITLES: Record<string, string> = {
  update_operator_box: '修改账号干员数据',
  start_upgrade_plan: '启动养成计划',
  update_user_profile: '修改用户档案',
}

interface ProfileDiffRow {
  section: string
  from: unknown
  to: unknown
  mode: string
}

interface OperatorDiffRow {
  operator: string
  field: string
  from: unknown
  to: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** `update_operator_box` 的 `payload.diff`：`[{operator, field, from, to}]`。 */
export function parseOperatorDiff(payload: Record<string, unknown>): OperatorDiffRow[] {
  const rows = Array.isArray(payload.diff) ? payload.diff : []
  return rows.flatMap((row) => {
    if (!isRecord(row) || typeof row.operator !== 'string' || typeof row.field !== 'string') return []
    return [{ operator: row.operator, field: row.field, from: row.from, to: row.to }]
  })
}

/** `update_user_profile` 的 `payload.diff`：`[{section, from, to, mode}]`（§8.3）。 */
export function parseProfileDiff(payload: Record<string, unknown>): ProfileDiffRow[] {
  const rows = Array.isArray(payload.diff) ? payload.diff : []
  return rows.flatMap((row) => {
    if (!isRecord(row) || typeof row.section !== 'string') return []
    return [
      {
        section: row.section,
        from: row.from,
        to: row.to,
        mode: typeof row.mode === 'string' ? row.mode : 'replace',
      },
    ]
  })
}

function reasonText(payload: Record<string, unknown>): string | null {
  const args = isRecord(payload.arguments) ? payload.arguments : null
  return args && typeof args.reason === 'string' && args.reason.trim() ? args.reason : null
}

function masteryText(value: unknown): string | null {
  if (!isRecord(value)) return null
  const parts = ['1', '2', '3'].flatMap((slot) => {
    const level = Number(value[slot] ?? 0)
    return level > 0 ? [`S${slot} ${level}`] : []
  })
  return parts.length ? parts.join(' · ') : null
}

/** 一个养成目标的人话描述：精二 · 技能 7 · S2 3 · 模组 X2。 */
export function upgradeTargetText(target: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof target.elite === 'number') parts.push(eliteLabel(target.elite))
  if (typeof target.skill_level === 'number') parts.push(`技能 ${target.skill_level}`)
  const masteries = masteryText(target.masteries)
  if (masteries) parts.push(masteries)
  const modules = (Array.isArray(target.modules) ? target.modules : []).flatMap((module) => {
    if (!isRecord(module) || typeof module.type !== 'string') return []
    const level = Number(module.level ?? 0)
    return level > 0 ? [`模组 ${module.type}${level}`] : []
  })
  if (modules.length) parts.push(modules.join(' '))
  return parts.length ? parts.join(' · ') : '不改变养成目标'
}

interface UpgradePlanView {
  summary: string | null
  targets: { name: string; goal: string }[]
  runTimes: string[]
}

/** `start_upgrade_plan` 的 `payload.summary` / `targets` / `run_times`。 */
export function parseUpgradePlan(payload: Record<string, unknown>): UpgradePlanView {
  const targets = (Array.isArray(payload.targets) ? payload.targets : []).flatMap((target) => {
    if (!isRecord(target) || typeof target.operator !== 'string') return []
    return [{ name: target.operator, goal: upgradeTargetText(target) }]
  })
  const runTimes = (Array.isArray(payload.run_times) ? payload.run_times : []).filter(
    (value): value is string => typeof value === 'string' && value.length > 0,
  )
  return {
    summary: typeof payload.summary === 'string' && payload.summary.trim() ? payload.summary : null,
    targets,
    runTimes,
  }
}

/** 对话里的待确认卡：读确认详情、查看参数，批准并继续或拒绝。 */
export function ConfirmationCard({ confirmationId, busy, onResolve }: ConfirmationCardProps) {
  const titleId = useId()
  const confirmation = useQuery({
    queryKey: [...keys.agent.confirmations(), confirmationId],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/confirmations/{confirmation_id}', {
        params: { path: { confirmation_id: confirmationId } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as AgentConfirmation
    },
    retry: false,
  })
  const detail = confirmation.data
  const payload = detail?.payload ?? {}
  const toolName = typeof payload.tool_name === 'string' ? payload.tool_name : detail?.action ?? ''
  const accountTitle = ACCOUNT_TOOL_TITLES[toolName]
  // 账号数据的两类确认（§7.4、§7.7）：渲染差异/摘要，不展开成 JSON。
  const operatorDiff = toolName === 'update_operator_box' ? parseOperatorDiff(payload) : []
  const upgradePlan = toolName === 'start_upgrade_plan' ? parseUpgradePlan(payload) : null
  const profileDiff = toolName === 'update_user_profile' ? parseProfileDiff(payload) : []
  const reason = reasonText(payload)
  const grant = detail?.action === 'grant_atomic_ops'
  // 作业授权（§9.6、§9.10）：三项可改，改过的值存在本地，批准时作为覆盖参数带回。
  const copilotGrant = detail?.action === 'grant_copilot_attempts' ? parseCopilotGrantPayload(payload) : null
  const [grantOverrides, setGrantOverrides] = useState<CopilotGrantOptions | null>(null)
  const grantChoices: CopilotGrantOptions | null = copilotGrant
    ? grantOverrides ?? { attempts: copilotGrant.attempts, success: copilotGrant.success, support: copilotGrant.support }
    : null
  const heading = grant ? '需要确认此操作' : copilotGrant ? '作业执行授权' : accountTitle ?? '需要确认此操作'
  const approved = detail?.status === 'approved'
  const rejected = detail?.status === 'rejected' || detail?.status === 'expired'
  const grantMinutes = Math.floor(Number(detail?.payload.window_seconds) / 60)
  const unavailable = busy !== null || confirmation.isLoading || !detail

  return (
    <article aria-labelledby={titleId} aria-live="polite" className="overflow-hidden rounded-xl border bg-card shadow-card">
      <div className="space-y-3 p-4">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-warning-soft text-warning">
            <ShieldAlert aria-hidden="true" className="size-[1.125rem]" />
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 id={titleId} className="text-[0.9375rem] font-semibold leading-6">
                {heading}
              </h3>
              <Badge variant={approved ? 'success' : rejected ? 'danger' : 'warning'} className="tabular-nums">
                {approved ? '已批准' : rejected ? '已拒绝或过期' : expiryLabel(detail?.expires_at)}
              </Badge>
            </div>
            <p className="break-words text-sm text-muted-foreground">
              {detail
                ? grant
                  ? '会话级屏幕操作授权'
                  : copilotGrant
                    ? detail.reason || '批准后 Agent 可以在这一关自动重试，次数用完就结束。'
                    : detail.reason
                : '正在读取确认详情…'}
            </p>
          </div>
        </div>
        {confirmation.error ? <InlineAlert tone="danger">读取确认详情失败：{toApiError(confirmation.error).message}</InlineAlert> : null}
        {grant && !approved && !rejected ? (
          <p className="text-sm">
            批准后本会话获得{grantMinutes > 0 ? ` ${grantMinutes} 分钟` : '限时'}屏幕操作授权，期间的点击、滑动、长按、输入和按键都会记入审计。
          </p>
        ) : null}
        {accountTitle && reason ? <p className="break-words text-sm">理由：{reason}</p> : null}
        {operatorDiff.length ? (
          <div className="space-y-1.5">
            <p className="text-sm font-medium">逐字段差异</p>
            <ul aria-label="逐字段差异" className="divide-y overflow-hidden rounded-lg border">
              {operatorDiff.map((row, index) => (
                <li key={`${row.operator}-${row.field}-${index}`} className="flex items-center gap-3 p-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{row.operator}</p>
                    <p className="text-footnote text-muted-foreground">{fieldLabel(row.field)}</p>
                  </div>
                  <p className="shrink-0 text-right text-footnote tabular-nums">
                    <span className="text-muted-foreground line-through">{diffValueText(row.field, row.from)}</span>
                    <span aria-hidden="true" className="px-1 text-muted-foreground">→</span>
                    <span className="font-medium">{diffValueText(row.field, row.to)}</span>
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {profileDiff.length ? (
          <div className="space-y-1.5">
            <p className="text-sm font-medium">用户档案改动</p>
            <ul aria-label="用户档案改动" className="space-y-2">
              {profileDiff.map((row, index) => (
                <li key={`${row.section}-${index}`} className="rounded-lg border p-2.5">
                  <p className="text-sm font-medium">
                    {row.section}
                    <span className="pl-2 text-footnote font-normal text-muted-foreground">
                      {row.mode === 'append' ? '追加' : '替换'}
                    </span>
                  </p>
                  {row.from ? (
                    <p className="whitespace-pre-wrap break-words text-footnote text-muted-foreground line-through">
                      {String(row.from)}
                    </p>
                  ) : null}
                  <p className="whitespace-pre-wrap break-words text-footnote">{String(row.to ?? '')}</p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {upgradePlan ? (
          <div className="space-y-1.5">
            <p className="text-sm font-medium">养成计划摘要</p>
            {upgradePlan.summary ? <p className="break-words text-sm">{upgradePlan.summary}</p> : null}
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-footnote">
              <dt className="text-muted-foreground">目标</dt>
              <dd className="tabular-nums">{upgradePlan.targets.length} 个</dd>
              <dt className="text-muted-foreground">每日时刻</dt>
              <dd className="tabular-nums">{upgradePlan.runTimes.length ? upgradePlan.runTimes.join('、') : '—'}</dd>
            </dl>
            {upgradePlan.targets.length ? (
              <ul aria-label="养成目标" className="divide-y overflow-hidden rounded-lg border">
                {upgradePlan.targets.map((target) => (
                  <li key={target.name} className="flex items-center gap-3 p-2.5 text-footnote">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{target.name}</span>
                    <span className="shrink-0 text-muted-foreground">{target.goal}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        {copilotGrant && grantChoices ? (
          <CopilotGrantCard
            request={copilotGrant}
            value={grantChoices}
            onChange={setGrantOverrides}
            disabled={unavailable}
          />
        ) : null}
        {!accountTitle && !copilotGrant && detail?.payload.arguments ? (
          <details className="group rounded-lg bg-surface-2/70">
            <summary className="flex min-h-10 cursor-pointer list-none items-center px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">查看操作参数</summary>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-border/70 px-3 py-2 font-mono text-xs leading-5">{pretty(detail.payload.arguments)}</pre>
          </details>
        ) : null}
        {approved ? (
          <p className="text-sm text-success">已批准，Agent 正在继续处理。</p>
        ) : rejected ? (
          <p className="text-sm text-muted-foreground">确认已结束，本轮不会重试该操作。</p>
        ) : (
          <p className="text-footnote text-muted-foreground">拒绝或过期后不会重试这个操作。</p>
        )}
      </div>
      {approved || rejected ? null : (
        <div className="grid grid-cols-2 gap-2 border-t bg-surface-2/40 p-3">
          <Button type="button" variant="outline" disabled={unavailable} loading={busy === 'reject'} onClick={() => onResolve(confirmationId, false)}>
            {busy === 'reject' ? null : <X aria-hidden="true" />}
            拒绝
          </Button>
          <Button type="button" disabled={unavailable} loading={busy === 'approve'} onClick={() => onResolve(confirmationId, true, grantChoices ?? undefined)}>
            {busy === 'approve' ? null : <Check aria-hidden="true" />}
            {busy === 'approve' ? '正在处理…' : '批准并继续'}
          </Button>
        </div>
      )}
    </article>
  )
}
