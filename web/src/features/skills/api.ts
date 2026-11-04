import { queryOptions, type QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

export type SkillSummary = components['schemas']['SkillSummary']
export type SkillDetail = components['schemas']['SkillDetail']
export type SkillWriteRequest = components['schemas']['SkillWriteRequest']
export type SpecSummary = components['schemas']['SpecSummary']

const SKILLS_KEY = [...keys.agent.all(), 'skills'] as const

export const skillListKey = [...SKILLS_KEY, 'list'] as const

export function skillDetailKey(skillId: string) {
  return [...SKILLS_KEY, 'detail', skillId] as const
}

export const skillsListQuery = queryOptions({
  queryKey: skillListKey,
  queryFn: async (): Promise<SkillSummary[]> => {
    const { data, error, response } = await api.GET('/api/agent/skills')
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  staleTime: 15_000,
})

export function skillDetailQuery(skillId: string) {
  return queryOptions({
    queryKey: skillDetailKey(skillId),
    queryFn: () => fetchSkillDetail(skillId),
    staleTime: 15_000,
  })
}

export async function fetchSkillDetail(skillId: string): Promise<SkillDetail> {
  const { data, error, response } = await api.GET('/api/agent/skills/{skill_id}', {
    params: { path: { skill_id: skillId } },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function saveSkill(skillId: string, body: SkillWriteRequest): Promise<SkillDetail> {
  const { data, error, response } = await api.PUT('/api/agent/skills/{skill_id}', {
    params: { path: { skill_id: skillId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function deleteSkill(skillId: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/agent/skills/{skill_id}', {
    params: { path: { skill_id: skillId } },
  })
  if (error) throw toApiError(error, response)
}

/**
 * 列表里的启用开关：`PUT` 要求整份生效内容，而列表不带正文，
 * 所以先读一次详情，再改 `enabled` 写回。
 */
export async function setSkillEnabled(skillId: string, enabled: boolean): Promise<SkillDetail> {
  const detail = await fetchSkillDetail(skillId)
  return saveSkill(skillId, {
    title: detail.title,
    description: detail.description,
    body: detail.body,
    tools: detail.tools,
    agents: detail.agents,
    enabled,
  })
}

/** 代理定义列表：技能页用它的 `title` 显示代理名，读一次就不再重取。 */
export const agentSpecsQuery = queryOptions({
  queryKey: keys.agent.specs.all(),
  queryFn: async (): Promise<SpecSummary[]> => {
    const { data, error, response } = await api.GET('/api/agent/specs')
    if (error) throw toApiError(error, response)
    return data?.items ?? []
  },
  staleTime: Infinity,
  retry: false,
})

/** 写入或删除后让列表与这一条的详情一起失效。 */
export function invalidateSkills(queryClient: QueryClient, skillId?: string): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: skillListKey }),
    skillId ? queryClient.invalidateQueries({ queryKey: skillDetailKey(skillId) }) : Promise.resolve(),
  ])
}
