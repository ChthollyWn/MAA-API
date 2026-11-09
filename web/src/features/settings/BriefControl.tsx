import { FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Section } from '@/components/ui/section'
import { toast } from '@/components/ui/toaster'
import { useRunBrief } from '@/features/brief/api'
import { errorText } from './format'

/**
 * 设置 › LLM 与 Agent 的「每日简报」入口（M84 §4.6）。
 *
 * 按钮走 `POST /api/agent/brief/run`，不受「一天一张」限制，所以请求进行中禁用，
 * 避免连点建出多张工单（§4.5.13）。成功后 Toast 只说明已开始，简报本身稍后
 * 由 analyst 工单产出。
 */
export function BriefControl() {
  const brief = useRunBrief()

  return (
    <Section title="每日简报">
      <div className="rounded-xl border bg-card p-4 shadow-card">
        <p className="text-footnote text-muted-foreground">
          现在就生成一份简报，工单里会出现「每日简报」的 analyst 任务。
        </p>
        <Button
          type="button"
          variant="secondary"
          className="mt-3 w-full sm:w-auto"
          loading={brief.isPending}
          onClick={() => brief.mutate(undefined, {
            onSuccess: () => toast.success('已开始生成简报'),
            onError: (error) => toast.error(`生成简报失败：${errorText(error)}`),
          })}
        >
          <FileText aria-hidden="true" />
          生成今日简报
        </Button>
      </div>
    </Section>
  )
}
