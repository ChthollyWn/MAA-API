import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/** `POST /api/agent/brief/run` 的响应：新建的简报工单与产物。 */
export type BriefRunResult = components['schemas']['BriefRunResult']

/** 立即生成简报：不受一天一张限制，前端在请求进行中禁用按钮（M84 §4.5.13）。 */
export async function runBrief(): Promise<BriefRunResult> {
  const { data, error, response } = await api.POST('/api/agent/brief/run')
  if (error || !data) throw toApiError(error, response)
  return data
}

/**
 * `POST /api/agent/brief/run`。成功后新工单要出现在工单列表里，简报产物也会
 * 多一份，所以一起失效这两片缓存；失败原因交给调用方弹 Toast。
 */
export function useRunBrief() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: runBrief,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() })
      void queryClient.invalidateQueries({ queryKey: keys.agent.artifactsAll() })
    },
  })
}
