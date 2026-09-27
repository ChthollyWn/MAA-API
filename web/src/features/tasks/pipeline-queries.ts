import { queryOptions, useQueries } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { explicitTitle, pipelineIdOf, taskNamesTitle, type PipelineRow } from './pipeline-format'

export function pipelineDetailQuery(pipelineId: string) {
  return queryOptions({
    queryKey: keys.pipelines.detail(pipelineId),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: pipelineId } } })
      if (error) throw toApiError(error, response)
      return data as PipelineRow
    },
    retry: false,
  })
}

export async function cancelPipeline(pipelineId: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: pipelineId } } })
  if (error) throw toApiError(error, response)
}

/**
 * 列表接口不带任务明细；没有名字的流水线逐条读取详情，用任务类型组合当标题。
 * 与详情页共用缓存，点进详情时不必再等。
 */
export function useUntitledPipelineNames(rows: readonly PipelineRow[]): ReadonlyMap<string, string> {
  const ids = [...new Set(rows.filter((row) => !explicitTitle(row)).map(pipelineIdOf).filter(Boolean))]
  const results = useQueries({
    queries: ids.map((id) => ({ ...pipelineDetailQuery(id), staleTime: 5 * 60 * 1000 })),
  })
  const names = new Map<string, string>()
  results.forEach((result, index) => {
    const name = taskNamesTitle(result.data?.tasks)
    if (name) names.set(ids[index], name)
  })
  return names
}
