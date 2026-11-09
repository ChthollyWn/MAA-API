/** 示教页的数据读取与三个写操作（M89 规格 §4.6）。 */

import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

export type DemonstrationOut = components['schemas']['DemonstrationOut']
export type DemonstrationPage = components['schemas']['DemonstrationPage']
export type DemonstrationConvertOut = components['schemas']['DemonstrationConvertOut']
export type DemonstrationStepOut = components['schemas']['DemonstrationStepOut']
export type DemonstrationStatus = components['schemas']['DemonstrationStatus']

export interface DemonstrationFilters {
  status?: DemonstrationStatus
  page?: number
  size?: number
  /** query key 需要把它当普通过滤对象参与哈希。 */
  [key: string]: unknown
}

/** 列表固定读第一页；正常使用不会在几天里攒出超过 20 条示教。 */
export const DEMONSTRATION_FILTERS: Readonly<DemonstrationFilters> = { page: 1, size: 20 }

export interface DeviceStatus {
  state: string
  resolution: { width: number; height: number } | null
}

export async function fetchDemonstrations(
  filters: DemonstrationFilters = DEMONSTRATION_FILTERS,
  signal?: AbortSignal,
): Promise<DemonstrationPage> {
  const { data, error, response } = await api.GET('/api/agent/demonstrations', {
    params: {
      query: {
        ...(filters.status === undefined ? {} : { status: filters.status }),
        page: filters.page ?? 1,
        size: filters.size ?? 20,
      },
    },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

async function startDemonstration(title?: string): Promise<DemonstrationOut> {
  const { data, error, response } = await api.POST('/api/agent/demonstrations', {
    body: title === undefined ? {} : { title },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

async function stopDemonstration(id: string, discard: boolean): Promise<DemonstrationOut> {
  const { data, error, response } = await api.POST('/api/agent/demonstrations/{id}/stop', {
    params: { path: { id } },
    body: { discard },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

async function convertDemonstration(id: string, register: boolean): Promise<DemonstrationConvertOut> {
  const { data, error, response } = await api.POST('/api/agent/demonstrations/{id}/convert', {
    params: { path: { id } },
    body: { register },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function fetchDeviceStatus(signal?: AbortSignal): Promise<DeviceStatus> {
  const { data, error, response } = await api.GET('/api/device/status', { signal })
  if (error || !data) throw toApiError(error, response)
  const raw = data as Record<string, unknown>
  const state = typeof raw.state === 'string' ? raw.state : 'unknown'
  const resolution = raw.resolution
  let size: DeviceStatus['resolution'] = null
  if (resolution && typeof resolution === 'object') {
    const width = Number((resolution as Record<string, unknown>).width)
    const height = Number((resolution as Record<string, unknown>).height)
    if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
      size = { width, height }
    }
  }
  return { state, resolution: size }
}

/** 设备连接状态与分辨率；开始示教与画布映射都依赖它。 */
export const deviceStatusQuery = queryOptions({
  queryKey: keys.device.status(),
  queryFn: ({ signal }) => fetchDeviceStatus(signal),
  staleTime: 5_000,
  refetchInterval: 5_000,
  retry: false,
})

/** 示教列表；有录制中的行时每 2 秒刷新，步骤随录制增长。 */
export function useDemonstrations(filters: DemonstrationFilters = DEMONSTRATION_FILTERS) {
  return useQuery({
    queryKey: keys.agent.demonstrations(filters),
    queryFn: ({ signal }) => fetchDemonstrations(filters, signal),
    staleTime: 2_000,
    retry: false,
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((item) => item.status === 'recording') ? 2_000 : false,
  })
}

function useInvalidateDemonstrations() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: keys.agent.demonstrationsAll() })
  }
}

export function useStartDemonstration() {
  const invalidate = useInvalidateDemonstrations()
  return useMutation({
    mutationFn: (title?: string) => startDemonstration(title),
    onSuccess: invalidate,
  })
}

export function useStopDemonstration() {
  const invalidate = useInvalidateDemonstrations()
  return useMutation({
    mutationFn: (payload: { id: string; discard: boolean }) =>
      stopDemonstration(payload.id, payload.discard),
    onSuccess: invalidate,
  })
}

export function useConvertDemonstration() {
  const invalidate = useInvalidateDemonstrations()
  return useMutation({
    mutationFn: (payload: { id: string; register: boolean }) =>
      convertDemonstration(payload.id, payload.register),
    onSuccess: invalidate,
  })
}
