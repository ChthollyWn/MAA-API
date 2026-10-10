import { Badge } from '@/components/ui/badge'
import { Field } from '@/components/ui/field'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { SwitchRow } from '@/components/ui/switch'
import { successLabel, supportLabel, windowText } from './format'

/**
 * `grant_copilot_attempts` 的确认卡片视图（规格 §9.6、§9.10）。
 *
 * 卡片上可以改三项——尝试次数（1–10）、成功标准（三星 / 通关）、是否允许助战补位；
 * 有效期只展示、不给改。批准时把这三项作为覆盖参数带回确认接口。
 */

export type CopilotSuccessStandard = 'three_star' | 'clear'
export type CopilotSupportMode = 'fill_one' | 'none'

/** 尝试次数选择范围（§9.6）。 */
export const MIN_ATTEMPTS = 1
export const MAX_ATTEMPTS = 10
export const DEFAULT_ATTEMPTS = 3
export const DEFAULT_SUCCESS: CopilotSuccessStandard = 'three_star'
export const DEFAULT_SUPPORT: CopilotSupportMode = 'fill_one'

export interface CopilotGrantOptions {
  attempts: number
  success: CopilotSuccessStandard
  support: CopilotSupportMode
}

export interface CopilotGrantRequest extends CopilotGrantOptions {
  sessionId: string | null
  stageId: string | null
  /** 显示用的关卡代号（`1-7`）；缺失时退回 `stageId`。 */
  stageCode: string | null
  /** 有效期（分钟）；payload 里没有时为 null，卡片显示「服务端配置的时长」。 */
  windowMinutes: number | null
  /** 触发这次授权的工具：`run_copilot_attempt` 或 `submit_pipeline`。 */
  toolName: string | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function text(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

function attemptsOf(value: unknown, fallback = DEFAULT_ATTEMPTS): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(Math.trunc(parsed), MIN_ATTEMPTS), MAX_ATTEMPTS)
}

function successOf(value: unknown): CopilotSuccessStandard {
  return value === 'clear' || value === 'three_star' ? value : DEFAULT_SUCCESS
}

function supportOf(value: unknown): CopilotSupportMode {
  return value === 'none' || value === 'fill_one' ? value : DEFAULT_SUPPORT
}

/**
 * 确认详情里的授权 payload → 卡片输入（§9.6）。
 *
 * 字段以 policy 写进 `extra_payload` 的为准（`attempts` / `success` / `support` /
 * `tool_name`），`session_id`、`stage_id`、`stage_code`、`window_minutes` 由确认层补；
 * 缺项退回 `payload.arguments`（模型调用工具时给的预填值），再缺就用默认值。
 * 认不出的字段一律忽略，永不抛异常。
 */
export function parseCopilotGrantPayload(payload: Record<string, unknown>): CopilotGrantRequest {
  const args = isRecord(payload.arguments) ? payload.arguments : {}
  const seconds = typeof payload.window_seconds === 'number' ? payload.window_seconds : Number(payload.window_seconds)
  const minutes = typeof payload.window_minutes === 'number'
    ? payload.window_minutes
    : Number.isFinite(seconds) && seconds > 0
      ? Math.round(seconds / 60)
      : null
  return {
    attempts: attemptsOf(payload.attempts ?? args.grant_attempts),
    success: successOf(payload.success ?? args.success),
    support: supportOf(payload.support ?? args.support),
    sessionId: text(payload.session_id) ?? text(args.session_id),
    stageId: text(payload.stage_id) ?? text(args.stage_id),
    stageCode: text(payload.stage_code) ?? text(args.stage_code),
    windowMinutes: minutes && minutes > 0 ? minutes : null,
    toolName: text(payload.tool_name) ?? text(args.tool_name),
  }
}

const ATTEMPT_OPTIONS: readonly number[] = Array.from(
  { length: MAX_ATTEMPTS - MIN_ATTEMPTS + 1 },
  (_, index) => MIN_ATTEMPTS + index,
)

const SUCCESS_OPTIONS = [
  { value: 'three_star' as const, label: successLabel('three_star') },
  { value: 'clear' as const, label: successLabel('clear') },
]

export interface CopilotGrantCardProps {
  /** 后端给的预填值与只读信息。 */
  request: CopilotGrantRequest
  /** 用户在卡片上改过的三项；没改过时等于 `request` 的前三项。 */
  value: CopilotGrantOptions
  onChange: (next: CopilotGrantOptions) => void
  /** 正在提交或确认已结束：控件全部只读。 */
  disabled?: boolean
}

/** 授权卡片的主体：三项可改、有效期只展示；批准 / 拒绝按钮由确认卡片提供。 */
export function CopilotGrantCard({ request, value, onChange, disabled = false }: CopilotGrantCardProps) {
  const set = <K extends keyof CopilotGrantOptions>(key: K, next: CopilotGrantOptions[K]) =>
    onChange({ ...value, [key]: next })
  const stage = request.stageCode ?? request.stageId

  return (
    <div className="space-y-4 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">授权关卡</span>
        <Badge variant="outline" data-testid="copilot-grant-stage">{stage ? `关卡 ${stage}` : '关卡未标明'}</Badge>
        <span className="ml-auto text-footnote text-muted-foreground">
          {successLabel(value.success)} · {supportLabel(value.support)}
        </span>
      </div>

      <Field
        label="尝试次数"
        htmlFor="copilot-grant-attempts"
        description={`最多 ${value.attempts} 次；次数用完授权就结束，需要重新申请。`}
      >
        <NativeSelect
          id="copilot-grant-attempts"
          aria-label="尝试次数"
          disabled={disabled}
          value={String(value.attempts)}
          onChange={(event) => set('attempts', attemptsOf(event.currentTarget.value))}
        >
          {ATTEMPT_OPTIONS.map((count) => (
            <option key={count} value={count}>{count} 次</option>
          ))}
        </NativeSelect>
      </Field>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">成功标准</legend>
        <SegmentedControl
          ariaLabel="成功标准"
          fullWidth
          disabled={disabled}
          value={value.success}
          onValueChange={(next) => set('success', successOf(next))}
          options={SUCCESS_OPTIONS}
        />
        <p className="text-footnote text-muted-foreground">
          {value.success === 'three_star'
            ? '必须三星才算成功；二星与通关不计。'
            : '通关即可：二星或三星都算成功。'}
        </p>
      </fieldset>

      <SwitchRow
        label="允许助战补位"
        description="作业里只缺一名干员时借一个助战；借不到算一次失败的尝试。"
        checked={value.support === 'fill_one'}
        disabled={disabled}
        onCheckedChange={(checked) => set('support', checked ? 'fill_one' : 'none')}
      />

      <p className="text-footnote text-muted-foreground" data-testid="copilot-grant-window">
        {windowText(request.windowMinutes)}
      </p>
      {request.toolName === 'submit_pipeline' ? (
        <p className="text-footnote text-muted-foreground">这次授权来自含作业任务的流水线提交。</p>
      ) : null}
    </div>
  )
}
