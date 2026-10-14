import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ShieldAlert, X } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import {
  compactText,
  finiteNumber,
  parsePlanStages,
  planRunsTotal,
  upgradeTargetText,
  type PlanStageSummary,
} from '@/features/account/format'
import { CopilotGrantCard, parseCopilotGrantPayload, type CopilotGrantOptions } from '@/features/copilots/CopilotGrantCard'
import { expiryLabel, pretty } from './format'
import type { AgentConfirmation } from './types'

// `upgradeTargetText` 的实现搬到了 `@/features/account/format`（账号页的「养成计划」区块共用），
// 这里继续导出同名函数，避免既有引用断掉。
export { upgradeTargetText }
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
 * 账号数据动作的卡片标题（规格 §7.4、§7.7）；这三类动作不把 payload 当普通 JSON 展开，
 * 而是渲染计划摘要 / 档案改动。M47 删掉 `update_operator_box` 之后，干员数据不再有
 * 需要人工确认的写入动作（docs/13 §8.70）。
 */
const ACCOUNT_TOOL_TITLES: Record<string, string> = {
  start_upgrade_plan: '启动养成计划',
  update_user_profile: '修改用户档案',
  sync_skland_training: '同步森空岛练度',
}

/** 账号数据类动作的判定：确认卡片与「需要人工确认」面板据此走结构化明细而不是 JSON 展开。 */
export function isAccountTool(toolName: string): boolean {
  return Boolean(ACCOUNT_TOOL_TITLES[toolName])
}

interface ProfileDiffRow {
  section: string
  from: unknown
  to: unknown
  mode: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
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

/** 预演里的任务项：后端给 `{name, stage?, times?, medicine?, stone?, expiring_medicine?}`。 */
export interface PreviewTaskView {
  name: string
  /** `Fight` 的关卡代号；没有时为 null。 */
  stage: string | null
  times: number | null
  /** 其余字段的「标签 值」小字（用药、碎石、连续作战等）。 */
  extras: string[]
}

/** 任务字段 → 中文标签；没登记的键原样用键名，免得以后新增字段被吞掉。 */
const TASK_FIELD_LABELS: Record<string, string> = {
  medicine: '理智药',
  stone: '源石',
  expiring_medicine: '临期理智药',
  series: '连续作战',
}

/** `preview.medicine` → 「今天 理智药 0、源石 0、临期理智药 999」；读不出返回 null。 */
export function medicineText(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (!isRecord(value)) return compactText(value)
  const labels: Record<string, string> = {
    today: '今天 理智药',
    stone: '源石',
    expiring_medicine: '临期理智药',
    daily_expiring_medicine: '每日 临期理智药',
  }
  const parts = Object.entries(value).map(([key, item]) => `${labels[key] ?? key} ${compactText(item)}`)
  return parts.length ? parts.join('、') : null
}

/** 一个任务项；形状不认识时降级成一行文本，字段缺失也不抛错。 */
export function parsePreviewTask(raw: unknown): PreviewTaskView | null {
  if (raw === null || raw === undefined) return null
  if (!isRecord(raw)) return { name: compactText(raw), stage: null, times: null, extras: [] }
  const extras = Object.entries(raw).flatMap(([key, value]) => {
    if (key === 'name' || key === 'stage' || key === 'times') return []
    return [`${TASK_FIELD_LABELS[key] ?? key} ${compactText(value)}`]
  })
  return {
    name: typeof raw.name === 'string' && raw.name.trim() ? raw.name : '未命名任务',
    stage: raw.stage === null || raw.stage === undefined ? null : compactText(raw.stage),
    times: finiteNumber(raw.times),
    extras,
  }
}

/** `preview.conflicts` 的一条（`{schedule_id, name}`）；读不出返回 null。 */
export function conflictText(raw: unknown): string | null {
  if (raw === null || raw === undefined) return null
  if (!isRecord(raw)) return compactText(raw)
  const name = typeof raw.name === 'string' ? raw.name.trim() : ''
  const scheduleId = raw.schedule_id === null || raw.schedule_id === undefined ? '' : compactText(raw.schedule_id)
  if (name && scheduleId) return `${name}（${scheduleId}）`
  if (name || scheduleId) return name || scheduleId
  const fallback = compactText(raw)
  return fallback === '{}' ? null : fallback
}

/** `preview.unobtainable` 的一条（`{name, amount, have, missing, reason, hint}`）。 */
export function unobtainableText(raw: unknown): string | null {
  if (raw === null || raw === undefined) return null
  if (!isRecord(raw)) return compactText(raw)
  const name = typeof raw.name === 'string' && raw.name.trim() ? raw.name : '未知材料'
  const missing = finiteNumber(raw.missing)
  const counts = [
    finiteNumber(raw.amount) === null ? null : `需求 ${finiteNumber(raw.amount)}`,
    finiteNumber(raw.have) === null ? null : `已有 ${finiteNumber(raw.have)}`,
  ].filter((item): item is string => item !== null)
  const tail = [raw.reason, raw.hint].flatMap((item) =>
    typeof item === 'string' && item.trim() ? [item] : [],
  )
  return `${name}${missing === null ? '' : `：缺 ${missing}`}${counts.length ? `（${counts.join('，')}）` : ''}${tail.length ? `——${tail.join('；')}` : ''}`
}

/** `preview`（§7.7）的渲染模型；不是映射时返回 null，卡片只是少一段内容。 */
export interface UpgradePreviewView {
  todayTasks: PreviewTaskView[]
  dailyTemplate: PreviewTaskView[]
  medicine: string | null
  daysEstimated: number | null
  totalSanity: number | null
  stages: PlanStageSummary[]
  conflicts: string[]
  unobtainable: string[]
  notes: string[]
}

function textList(value: unknown, mapper: (row: unknown) => string | null): string[] {
  return (Array.isArray(value) ? value : []).flatMap((row) => {
    const text = mapper(row)
    return text ? [text] : []
  })
}

function taskList(value: unknown): PreviewTaskView[] {
  return (Array.isArray(value) ? value : []).flatMap((row) => {
    const task = parsePreviewTask(row)
    return task ? [task] : []
  })
}

/** 读不出任何字段的行整条丢掉，而不是渲染一个空行。 */
function noteList(value: unknown): string[] {
  return textList(value, (row) => {
    const text = compactText(row)
    return text === '—' ? null : text
  })
}

export function parseUpgradePreview(value: unknown): UpgradePreviewView | null {
  if (!isRecord(value)) return null
  return {
    todayTasks: taskList(value.today_tasks),
    dailyTemplate: taskList(value.daily_template),
    medicine: medicineText(value.medicine),
    daysEstimated: finiteNumber(value.days_estimated),
    totalSanity: finiteNumber(value.total_sanity),
    stages: parsePlanStages(value.stages),
    conflicts: textList(value.conflicts, conflictText),
    unobtainable: textList(value.unobtainable, unobtainableText),
    notes: noteList(value.notes),
  }
}

interface UpgradePlanView {
  summary: string | null
  targets: { name: string; goal: string }[]
  runTimes: string[]
}

/** `start_upgrade_plan` 的 `payload.summary` / `targets` / `run_times`。 */
export function parseUpgradePlan(payload: Record<string, unknown>): UpgradePlanView {
  const targets = (Array.isArray(payload.targets) ? payload.targets : []).flatMap((target) => {
    if (!isRecord(target)) return []
    const name = typeof target.operator === 'string' ? target.operator : typeof target.name === 'string' ? target.name : null
    if (!name) return []
    return [{ name, goal: upgradeTargetText(target) }]
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

function TaskList({ title, tasks }: { title: string; tasks: PreviewTaskView[] }) {
  if (!tasks.length) return null
  return (
    <div className="space-y-1">
      <p className="text-footnote font-medium">{title}</p>
      <ul aria-label={title} className="divide-y overflow-hidden rounded-lg border">
        {tasks.map((task, index) => (
          <li key={`${task.name}-${index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 p-2.5">
            <span className="text-sm font-medium">{task.name}</span>
            {task.stage ? <span className="text-footnote text-muted-foreground">关卡 {task.stage}</span> : null}
            {task.times === null ? null : <span className="text-footnote tabular-nums text-muted-foreground">×{task.times}</span>}
            {task.extras.length ? (
              <span className="min-w-0 flex-1 text-right text-footnote text-muted-foreground">{task.extras.join(' · ')}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 后端 `policy.py` 的 `CONFLICT_REASON_PREFIX`；理由里已经有了就不再单独渲染一遍。 */
const CONFLICT_REASON_PREFIX = '与现有定时任务争抢理智'

/**
 * 计划预演的明细（§7.7 第 1 条）：今天要提交的刷关清单、每日定时任务模板、用药口径、
 * 预计天数与剩余场次，以及与现有定时任务争抢理智的提示。预演拿不到时这一段整体不渲染，
 * 由调用方决定怎么说明。
 *
 * `hideConflicts`：后端已经把同一句冲突提示拼进 `reason`（`policy.py` 的
 * `_conflict_reasons()`），调用方显示 reason 时传 true，免得同一句话在卡片里出现两遍。
 */
function UpgradePreviewDetails({ preview, hideConflicts }: { preview: UpgradePreviewView; hideConflicts?: boolean }) {
  const runs = planRunsTotal(preview.stages)
  const estimate = [
    preview.daysEstimated === null ? null : `预计 ${preview.daysEstimated} 天`,
    preview.totalSanity === null ? null : `共 ${preview.totalSanity} 理智`,
    runs === null ? null : `剩余 ${runs} 场`,
  ].filter((item): item is string => item !== null)
  return (
    <div className="space-y-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-footnote">
        <dt className="text-muted-foreground">预计</dt>
        <dd className="tabular-nums">{estimate.length ? estimate.join(' · ') : '—'}</dd>
        <dt className="text-muted-foreground">用药</dt>
        <dd className="break-words">{preview.medicine ?? '—'}</dd>
      </dl>
      <TaskList title="今天要提交的刷关清单" tasks={preview.todayTasks} />
      <TaskList title="每日定时任务模板" tasks={preview.dailyTemplate} />
      {preview.stages.length ? (
        <div className="space-y-1">
          <p className="text-footnote font-medium">关卡与场次</p>
          <ul aria-label="关卡与场次" className="divide-y overflow-hidden rounded-lg border">
            {preview.stages.map((stage, index) => (
              <li key={`${stage.label}-${index}`} className="flex items-baseline gap-2 p-2.5 text-footnote">
                <span className="text-sm font-medium">{stage.label}</span>
                {stage.runs === null ? null : <span className="tabular-nums text-muted-foreground">×{stage.runs}</span>}
                <span className="min-w-0 flex-1 text-right text-muted-foreground">
                  {[
                    stage.items.length ? stage.items.join('、') : null,
                    stage.sanity === null ? null : `${stage.sanity} 理智`,
                  ]
                    .filter((item): item is string => item !== null)
                    .join(' · ')}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.conflicts.length && !hideConflicts ? (
        <InlineAlert tone="warning">与现有定时任务争抢理智：{preview.conflicts.join('、')}</InlineAlert>
      ) : null}
      {preview.unobtainable.length ? (
        <div className="space-y-1">
          <p className="text-footnote font-medium">刷不出来的材料</p>
          <ul aria-label="刷不出来的材料" className="space-y-1 text-footnote text-muted-foreground">
            {preview.unobtainable.map((item, index) => <li key={index} className="break-words">{item}</li>)}
          </ul>
        </div>
      ) : null}
      {preview.notes.length ? (
        <div className="space-y-1">
          <p className="text-footnote font-medium">预演说明</p>
          <ul aria-label="预演说明" className="space-y-1 text-footnote text-muted-foreground">
            {preview.notes.map((item, index) => <li key={index} className="break-words">{item}</li>)}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/**
 * 账号数据动作的明细（§7.4、§7.7）：逐字段差异、用户档案改动、养成计划摘要与预演。
 * 确认卡片与「需要人工确认」面板共用，两边对同一份 payload 渲染一致的内容
 * （面板此前把这类 payload 当 JSON 展开）。认不出的动作返回 null。
 */
export function AccountActionDetails({
  payload,
  toolName,
  reason,
}: {
  payload: Record<string, unknown>
  toolName: string
  /** 已显示的确认理由；后端把冲突提示拼在这里，命中时不再重复渲染一遍。 */
  reason?: string | null
}) {
  const profileDiff = toolName === 'update_user_profile' ? parseProfileDiff(payload) : []
  const upgradePlan = toolName === 'start_upgrade_plan' ? parseUpgradePlan(payload) : null
  const preview = parseUpgradePreview(payload.preview)
  const conflictsInReason = Boolean(reason && reason.includes(CONFLICT_REASON_PREFIX))
  // 预演失败时后端只给一句话（`preview_error`）：保留摘要，末尾轻描淡写地说明预演不可用。
  const previewError = typeof payload.preview_error === 'string' && payload.preview_error.trim() ? payload.preview_error : null
  if (!profileDiff.length && !upgradePlan) return null
  return (
    <div className="space-y-3">
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
          {preview ? <UpgradePreviewDetails preview={preview} hideConflicts={conflictsInReason} /> : null}
          {!preview && previewError ? (
            <p className="break-words text-footnote text-muted-foreground">预演不可用：{previewError}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
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
        {/* 冲突提示已经拼在 `detail.reason` 里（就是上面那段说明），别再重复渲染一遍。 */}
        {accountTitle ? <AccountActionDetails payload={payload} toolName={toolName} reason={detail?.reason} /> : null}
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
