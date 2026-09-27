import { useMutation } from '@tanstack/react-query'
import { CircleCheck, CircleX, Sparkles, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { errorText } from './format'

interface CapabilityResult {
  model?: string
  tool_calling?: boolean
  vision?: boolean
  ready?: boolean
  status?: string
  degraded_reason?: string | null
}

/** Agent 分区顶部：按已保存的 LLM 设置检查工具调用与视觉输入。 */
export function AgentCapabilityCheck() {
  const check = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST('/api/agent/capabilities/check')
      if (error) throw toApiError(error, response)
      return data as unknown as CapabilityResult
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
        <div role="status" className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={result.tool_calling ? 'success' : 'danger'}>
              {result.tool_calling ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
              工具调用：{result.tool_calling ? '可用' : '不可用'}
            </Badge>
            <Badge variant={result.vision ? 'success' : 'warning'}>
              {result.vision ? <CircleCheck aria-hidden="true" /> : <TriangleAlert aria-hidden="true" />}
              视觉输入：{result.vision ? '可用' : '不可用'}
            </Badge>
            {result.model ? <span className="text-footnote text-muted-foreground">模型：{result.model}</span> : null}
          </div>
          {result.degraded_reason ? <p className="text-footnote text-muted-foreground">{result.degraded_reason}</p> : null}
        </div>
      ) : null}
    </section>
  )
}
