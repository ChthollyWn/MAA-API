import { queryOptions } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

export type SpecSummary = components['schemas']['SpecSummary']
export type SpecDetail = components['schemas']['SpecDetail']
export type ModelRolesView = components['schemas']['ModelRolesView']

/** 代理定义列表：定义随服务版本发布，读一次就不再重取。 */
export const specsQuery = queryOptions({
  queryKey: keys.agent.specs.all(),
  queryFn: async (): Promise<SpecSummary[]> => {
    const { data, error, response } = await api.GET('/api/agent/specs')
    if (error) throw toApiError(error, response)
    return data?.items ?? []
  },
  staleTime: Infinity,
  retry: false,
})

/** 一个代理定义的全部字段与提示词正文。 */
export const specDetailQuery = (name: string) => queryOptions({
  queryKey: keys.agent.specs.detail(name),
  queryFn: async (): Promise<SpecDetail> => {
    const { data, error, response } = await api.GET('/api/agent/specs/{name}', {
      params: { path: { name } },
    })
    if (error) throw toApiError(error, response)
    return data as SpecDetail
  },
  staleTime: Infinity,
  retry: false,
})

/** 九个模型角色当前解析到的档案与模型；详情页据此显示「实际模型」。 */
export const modelRolesQuery = queryOptions({
  queryKey: keys.agent.modelRoles(),
  queryFn: async (): Promise<ModelRolesView> => {
    const { data, error, response } = await api.GET('/api/agent/model-roles')
    if (error) throw toApiError(error, response)
    return data ?? { items: [] }
  },
  retry: false,
})
