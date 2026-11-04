import { useMemo } from 'react'
import { queryOptions, useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { AgentOrigin } from './types'

/**
 * 代理定义列表：确认卡与提问卡把 origin 里的 `agent` 换成面向用户的 `title`。
 * 定义在进程内不会变，一次取到就不再重取；读不到只是退回原名，不重试。
 */
export const agentSpecsQuery = queryOptions({
  queryKey: keys.agent.specs.all(),
  queryFn: async () => {
    const { data, error, response } = await api.GET('/api/agent/specs')
    if (error) throw toApiError(error, response)
    return data?.items ?? []
  },
  staleTime: Infinity,
  retry: false,
})

/**
 * 代理名 → 展示名。没有 origin 的确认/提问不该额外发请求，所以由调用方传 `enabled`。
 * 读不到定义或找不到这个名字时返回原名；名字为空返回「代理」（originLabel 用）。
 */
export function useAgentTitle(enabled: boolean): (name: string | null | undefined) => string {
  const specs = useQuery({ ...agentSpecsQuery, enabled })
  return useMemo(() => {
    const titles = new Map((specs.data ?? []).map((spec) => [spec.name, spec.title]))
    return (name) => (name ? titles.get(name) ?? name : '代理')
  }, [specs.data])
}

/**
 * 确认卡与提问卡上的一行来源文案（M71 §4.6）。界面只有「有工单」与「只有代理」
 * 两种，不加句号；`origin` 为空（REST 直接发起）返回 `null`，由调用方保留原样。
 */
export function originLabel(
  origin: AgentOrigin | null | undefined,
  title: (name: string | null | undefined) => string,
): string | null {
  if (!origin) return null
  if (origin.ticket_id) {
    return `来自工单「${origin.ticket_title ?? '未命名工单'}」（${title(origin.agent)}）`
  }
  return `来自${title(origin.agent)}`
}
