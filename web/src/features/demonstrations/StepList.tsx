/** 示教步骤列表：序号、动作、坐标、延迟、状态与操作前后两张缩略图（M89 §4.6）。 */

import { toApiError } from '@/api/errors'
import { Badge, type StatusTone } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/states'
import { useAuthedImage } from '@/components/ui/use-authed-image'
import type { DemonstrationStepOut } from './useDemonstrations'

const ACTION_LABELS: Record<DemonstrationStepOut['action'], string> = {
  click: '点击',
  swipe: '滑动',
  long_press: '长按',
}

function pointText(step: DemonstrationStepOut): string {
  if (step.action === 'swipe') {
    return `(${step.x1 ?? '—'}, ${step.y1 ?? '—'}) → (${step.x2 ?? '—'}, ${step.y2 ?? '—'})`
  }
  const base = `(${step.x ?? '—'}, ${step.y ?? '—'})`
  return step.duration_ms === null || step.duration_ms === undefined
    ? base
    : `${base} · ${step.duration_ms} 毫秒`
}

function stepStatus(step: DemonstrationStepOut): { label: string; tone: StatusTone } {
  return step.screenshot_id && step.result_screenshot_id
    ? { label: '已记录', tone: 'success' }
    : { label: '截图缺失', tone: 'warning' }
}

function Thumbnail({ id, alt }: { id: string | null | undefined; alt: string }) {
  const image = useAuthedImage(id ? { kind: 'screenshot', id, size: 'thumb' } : null)
  const error = image.error ? toApiError(image.error) : null
  const expired = error?.status === 404 || error?.status === 410

  return (
    <div
      ref={image.ref}
      className="grid size-20 shrink-0 place-items-center overflow-hidden rounded-md border bg-surface-2"
    >
      {image.url ? (
        <img alt={alt} className="size-full object-cover" src={image.url} />
      ) : id == null ? (
        <span className="px-1 text-center text-[0.625rem] leading-3 text-muted-foreground">截图缺失</span>
      ) : expired ? (
        <span className="px-1 text-center text-[0.625rem] leading-3 text-muted-foreground">
          <span className="block font-medium">截图已过期</span>
          只保留 7 天，不影响已注册的任务
        </span>
      ) : error ? (
        <span className="px-1 text-center text-[0.625rem] leading-3 text-muted-foreground">截图读取失败</span>
      ) : (
        <Spinner />
      )}
    </div>
  )
}

export function StepList({ steps }: { steps: DemonstrationStepOut[] }) {
  if (steps.length === 0) return null

  return (
    <section className="space-y-2" aria-label="示教步骤">
      <h3 className="px-1 text-footnote font-medium text-muted-foreground">步骤（{steps.length} 步）</h3>
      <div className="divide-y divide-border overflow-hidden rounded-xl border bg-card shadow-card">
        {steps.map((step) => {
          const status = stepStatus(step)
          return (
            <div key={step.seq} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
              <span className="w-5 shrink-0 text-footnote tabular-nums text-muted-foreground">{step.seq}</span>
              <Badge variant="neutral">{ACTION_LABELS[step.action]}</Badge>
              <span className="min-w-0 flex-1 text-sm tabular-nums">{pointText(step)}</span>
              <span className="text-footnote text-muted-foreground">延迟 {step.delay_ms} 毫秒</span>
              <Badge variant={status.tone}>{status.label}</Badge>
              <div className="flex shrink-0 items-center gap-1.5">
                <Thumbnail id={step.screenshot_id} alt={`第 ${step.seq} 步操作前截图`} />
                <Thumbnail id={step.result_screenshot_id} alt={`第 ${step.seq} 步操作后截图`} />
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

export default StepList
