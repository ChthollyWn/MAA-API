import { queryOptions, useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { schedulesQuery } from '@/features/schedules/queries'
import { explicitTitle, sourceLabel, type PipelineRow } from './pipeline-format'

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

function withTaskCount(label: string, row: PipelineRow): string {
  const count = typeof row.task_count === 'number' && row.task_count > 0 ? row.task_count : null
  return count ? `${label} · ${count} 项` : label
}

/** 没有名字的流水线的标题：定时触发的用定时任务名，其余用「来源 + 任务数」。 */
export function fallbackPipelineTitle(row: PipelineRow, scheduleNames: ReadonlyMap<string, string>): string {
  const scheduleName = typeof row.schedule_id === 'string' ? scheduleNames.get(row.schedule_id) : undefined
  if (scheduleName) return `定时 · ${scheduleName}`
  const source = sourceLabel(row.source)
  return withTaskCount(`${source}${/[A-Za-z]$/.test(source) ? ' ' : ''}流水线`, row)
}

/**
 * 列表接口不带任务明细，逐条读详情会让一页历史多出二十个请求。
 * 这里只借用已缓存的定时任务列表（一次请求）给定时触发的流水线命名。
 */
export function usePipelineTitle(rows: readonly PipelineRow[]): (row: PipelineRow) => string {
  const needsSchedules = rows.some((row) => !explicitTitle(row) && typeof row.schedule_id === 'string')
  const schedules = useQuery({ ...schedulesQuery, enabled: needsSchedules })
  const scheduleNames = new Map((schedules.data ?? []).map((schedule) => [schedule.id, schedule.name]))
  return (row) => explicitTitle(row) ?? fallbackPipelineTitle(row, scheduleNames)
}
