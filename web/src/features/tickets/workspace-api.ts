import { queryOptions } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'
import { downloadName } from '@/features/artifacts/format'

/**
 * M75 §4.6 的计划与产物查询。
 *
 * 两个 GET 都用 `api` 客户端（X-Token 鉴权）；计划只随 `plan_updated` 失效，
 * 产物列表只随 `artifact_created` 失效，见 `realtime/query-bridge.ts`。
 */
export type PlanView = components['schemas']['PlanOut']
export type PlanItemView = components['schemas']['PlanItemOut']
export type ArtifactView = components['schemas']['ArtifactOut']
export type ArtifactDetailView = components['schemas']['ArtifactDetailOut']

const ARTIFACT_PAGE_SIZE = 50

/** Worker 会话当前未结束的计划；没有计划时服务端返回 `null`。 */
export function planQueryOptions(sessionId: string | null | undefined) {
  return queryOptions({
    queryKey: keys.agent.plan(sessionId ?? ''),
    queryFn: async (): Promise<PlanView | null> => {
      const { data, error, response } = await api.GET('/api/agent/sessions/{session_id}/plan', {
        params: { path: { session_id: sessionId as string } },
      })
      if (error) throw toApiError(error, response)
      return data ?? null
    },
    enabled: Boolean(sessionId),
    retry: false,
  })
}

export interface PlanApprovalInput {
  approved: boolean
  /** 退回时必填；批准时省略。 */
  reason?: string
}

/** 批准或退回（`POST .../plan/approval`，M75 §4.3）。 */
export async function submitPlanApproval(
  sessionId: string,
  input: PlanApprovalInput,
): Promise<PlanView> {
  const { data, error, response } = await api.POST(
    '/api/agent/sessions/{session_id}/plan/approval',
    {
      params: { path: { session_id: sessionId } },
      body: {
        approved: input.approved,
        ...(input.reason ? { reason: input.reason } : {}),
      },
    },
  )
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 一个工单的产物元数据；列表不返回正文。 */
export function artifactsQueryOptions(ticketId: string) {
  return queryOptions({
    queryKey: keys.agent.artifacts(ticketId),
    queryFn: async (): Promise<ArtifactView[]> => {
      const { data, error, response } = await api.GET('/api/agent/artifacts', {
        params: { query: { ticket_id: ticketId, page: 1, size: ARTIFACT_PAGE_SIZE } },
      })
      if (error) throw toApiError(error, response)
      return data?.items ?? []
    },
    retry: false,
  })
}

/** 抽屉里的正文；正文最多 64 KiB，超出时 `truncated` 为 true。 */
export function artifactDetailQueryOptions(artifactId: string | null | undefined) {
  return queryOptions({
    queryKey: keys.agent.artifact(artifactId ?? ''),
    queryFn: async (): Promise<ArtifactDetailView> => {
      const { data, error, response } = await api.GET('/api/agent/artifacts/{artifact_id}', {
        params: { path: { artifact_id: artifactId as string } },
      })
      if (error || !data) throw toApiError(error, response)
      return data
    },
    enabled: Boolean(artifactId),
    retry: false,
  })
}

/**
 * 下载原始字节（`GET .../content`）。鉴权走 `api` 客户端，所以不能换成裸
 * `<a href>`；取到 Blob 后在前端拼一个对象 URL 存成文件。
 */
export async function saveArtifactFile(
  artifact: Pick<ArtifactDetailView, 'id' | 'title' | 'mime'>,
): Promise<string> {
  const { data, error, response } = await api.GET('/api/agent/artifacts/{artifact_id}/content', {
    params: { path: { artifact_id: artifact.id } },
    parseAs: 'blob',
  })
  if (error || !data) throw toApiError(error, response)
  const blob = data as unknown as Blob
  const filename = downloadName(artifact.title, artifact.mime)
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.rel = 'noopener'
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
  return filename
}
