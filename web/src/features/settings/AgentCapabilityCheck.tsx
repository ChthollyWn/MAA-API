import { useMutation } from '@tanstack/react-query'
import { CircleCheck, CircleHelp, CircleX, Sparkles } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { errorText } from './format'

/** `POST /api/agent/model-profiles/{name}/probe` 的响应体。 */
interface ProbeResult {
  latency_ms: number
  supports_tools: boolean | null
  supports_vision: boolean | null
  supports_json_schema: boolean | null
}

/** 探测结果是三态：`null` 表示这次没有探出确定结论，写成「未知」。 */
function AvailabilityBadge({ label, value }: { label: string; value: boolean | null }) {
  if (value === null) {
    return <Badge variant="neutral"><CircleHelp aria-hidden="true" />{label}：未知</Badge>
  }
  return value
    ? <Badge variant="success"><CircleCheck aria-hidden="true" />{label}：可用</Badge>
    : <Badge variant="danger"><CircleX aria-hidden="true" />{label}：不可用</Badge>
}

/** Agent 分区顶部：探测 default 模型档案的工具调用、视觉输入与结构化输出。 */
export function AgentCapabilityCheck() {
  const check = useMutation({
    mutationFn: async (): Promise<ProbeResult> => {
      const { data, error, response } = await api.POST('/api/agent/model-profiles/{name}/probe', {
        params: { path: { name: 'default' } },
      })
      if (error) throw toApiError(error, response)
      return data as ProbeResult
    },
  })
  const result = check.data

  return (
    <section aria-labelledby="agent-capability-title" className="space-y-3 rounded-xl border bg-card p-4 shadow-card">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
          <Sparkles aria-hidden="true" className="size-[1.125rem]" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="agent-capability-title" className="text-[0.9375rem] font-semibold leading-5">模型能力</h2>
          <p className="mt-0.5 text-footnote text-muted-foreground">按已保存的设置检查模型能否调用工具、读取截图，不会操作设备。</p>
        </div>
      </div>
      <Button type="button" variant="secondary" className="w-full sm:w-auto" loading={check.isPending} onClick={() => check.mutate()}>
        检查模型能力
      </Button>
      {check.isError ? (
        <InlineAlert tone="danger">
          能力检查失败：{errorText(check.error).replace(/[。.]$/, '')}。请确认 API 地址、密钥和模型名称后重试。
        </InlineAlert>
      ) : null}
      {result ? (
        <div role="status" className="flex flex-wrap items-center gap-2">
          {/* 工具调用探测只有「探到 / 没探到」两种结论（§4.6.6 的 T11 第 7 步）。 */}
          <AvailabilityBadge label="工具调用" value={result.supports_tools === true} />
          <AvailabilityBadge label="视觉输入" value={result.supports_vision} />
          <AvailabilityBadge label="结构化输出" value={result.supports_json_schema} />
          <span className="text-footnote text-muted-foreground">响应 {result.latency_ms} ms</span>
        </div>
      ) : null}
    </section>
  )
}
