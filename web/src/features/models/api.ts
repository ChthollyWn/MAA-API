import { queryOptions, useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

export type ModelProfile = components['schemas']['ModelProfileView']
export type ModelProfileCreate = components['schemas']['ModelProfileCreate']
export type ModelProfileUpdate = components['schemas']['ModelProfileUpdate']
export type ModelRole = components['schemas']['ModelRoleItem']
export type ProbeResult = components['schemas']['ProbeResultView']

/** 档案增删改与检测都会同时影响列表与角色卡：角色卡按档案名生成选项。 */
function invalidateProfiles(queryClient: QueryClient): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.agent.modelProfiles() }),
    queryClient.invalidateQueries({ queryKey: keys.agent.modelRoles() }),
  ])
}

export const modelProfilesQueryOptions = queryOptions({
  queryKey: keys.agent.modelProfiles(),
  queryFn: async (): Promise<ModelProfile[]> => {
    const { data, error, response } = await api.GET('/api/agent/model-profiles')
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  staleTime: 15_000,
})

export const modelRolesQueryOptions = queryOptions({
  queryKey: keys.agent.modelRoles(),
  queryFn: async (): Promise<ModelRole[]> => {
    const { data, error, response } = await api.GET('/api/agent/model-roles')
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  staleTime: 15_000,
})

export function useCreateProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: ModelProfileCreate): Promise<ModelProfile> => {
      const { data, error, response } = await api.POST('/api/agent/model-profiles', { body })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: () => invalidateProfiles(queryClient),
  })
}

export function useUpdateProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ name, body }: { name: string; body: ModelProfileUpdate }): Promise<ModelProfile> => {
      const { data, error, response } = await api.PUT('/api/agent/model-profiles/{name}', {
        params: { path: { name } },
        body,
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: () => invalidateProfiles(queryClient),
  })
}

export function useDeleteProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string): Promise<void> => {
      const { error, response } = await api.DELETE('/api/agent/model-profiles/{name}', {
        params: { path: { name } },
      })
      if (error) throw toApiError(error, response)
    },
    onSuccess: () => invalidateProfiles(queryClient),
  })
}

export function useProbeProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string): Promise<ProbeResult> => {
      const { data, error, response } = await api.POST('/api/agent/model-profiles/{name}/probe', {
        params: { path: { name } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    onSuccess: () => invalidateProfiles(queryClient),
  })
}

export function useUpdateRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ role, profile }: { role: string; profile: string }): Promise<void> => {
      const { error, response } = await api.PUT('/api/settings', {
        body: { items: { [`llm.role_${role}`]: profile } },
      })
      if (error) throw toApiError(error, response)
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agent.modelRoles() }),
        queryClient.invalidateQueries({ queryKey: keys.settings.all() }),
      ])
    },
  })
}
