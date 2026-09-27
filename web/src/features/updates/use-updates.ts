import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { keys } from '@/api/keys'
import type {
  UpdateHistoryResponse, UpdateOptions, UpdateRecord, UpdateStatusResponse, UpdateTarget,
} from '@/features/updates/types'
import { errorMessage, interruptsPipeline, recordIsActive } from '@/features/updates/types'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'

export const HISTORY_PAGE = { page: 1, size: 20 } as const

const detailKey = (id: string | null) => [...keys.updates.all(), 'detail', id] as const

function apiError(error: unknown): Error {
  return new Error(errorMessage(error))
}

async function fetchRecord(id: string, emptyMessage: string): Promise<UpdateRecord> {
  const { data, error } = await api.GET('/api/updates/{update_id}', { params: { path: { update_id: id } } })
  if (error) throw apiError(error)
  if (!data) throw new Error(emptyMessage)
  return data as unknown as UpdateRecord
}

async function postUpdate(target: UpdateTarget, options: UpdateOptions): Promise<UpdateRecord> {
  const interrupt = interruptsPipeline(target, options)
  const result = target === 'core'
    ? await api.POST('/api/updates/core', { body: { channel: 'stable', force: false, force_interrupt: interrupt } })
    : target === 'resource'
      ? await api.POST('/api/updates/resource', {
        body: {
          channel: options.resourceChannel,
          force: false,
          force_interrupt: interrupt,
          reload_mode: options.reloadMode,
        },
      })
      : await api.POST('/api/updates/game', {
        body: { channel: options.gameChannel, force: false, force_interrupt: interrupt },
      })
  if (result.error) throw apiError(result.error)
  const record = result.data as unknown as UpdateRecord | undefined
  if (!record) throw new Error('更新请求已提交，但接口没有返回记录编号。')
  return record
}

/** 热更新页的数据：状态、历史、进行中的记录与详情，以及检查、开始、取消、重试四个操作。 */
export function useUpdates() {
  const queryClient = useQueryClient()
  const realtime = useRealtimeStatus()
  const [historyDetailId, setHistoryDetailId] = useState<string | null>(null)
  const [acceptingRecord, setAcceptingRecord] = useState<UpdateRecord | null>(null)
  const previousRunningId = useRef<string | null>(null)

  const statusQuery = useQuery({
    queryKey: keys.updates.status(),
    queryFn: async () => {
      const { data, error } = await api.GET('/api/updates/status')
      if (error) throw apiError(error)
      if (!data) throw new Error('更新状态接口没有返回数据。')
      return data as unknown as UpdateStatusResponse
    },
    // WS 在线时由 update_progress 使其失效；连接中或离线时轮询，才能发现别处发起的更新。
    refetchInterval: realtime.status === 'CONNECTED' ? false : 5000,
  })

  const historyQuery = useQuery({
    queryKey: [...keys.updates.all(), HISTORY_PAGE],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/updates', { params: { query: HISTORY_PAGE } })
      if (error) throw apiError(error)
      if (!data) throw new Error('更新记录接口没有返回数据。')
      return data as unknown as UpdateHistoryResponse
    },
  })

  const status = statusQuery.data
  const candidate = status?.running ?? acceptingRecord
  const activeCandidate = recordIsActive(candidate) ? candidate : null
  const activeId = activeCandidate?.id ?? null

  const detailQuery = useQuery({
    queryKey: detailKey(activeId),
    enabled: activeId !== null,
    queryFn: () => fetchRecord(activeId!, '更新进度接口没有返回数据。'),
    refetchInterval: activeId ? 2000 : false,
  })

  const historyDetailQuery = useQuery({
    queryKey: detailKey(historyDetailId),
    enabled: historyDetailId !== null,
    queryFn: () => fetchRecord(historyDetailId!, '更新详情接口没有返回数据。'),
  })

  useEffect(() => {
    const running = status?.running
    if (!running?.id) return
    queryClient.setQueryData<UpdateRecord>(detailKey(running.id), (previous) => ({
      ...previous,
      ...running,
    }))
  }, [queryClient, status?.running])

  useEffect(() => {
    const runningId = status?.running?.id ?? null
    if (previousRunningId.current && !runningId) {
      void queryClient.invalidateQueries({ queryKey: keys.updates.all() })
    }
    previousRunningId.current = runningId
  }, [queryClient, status?.running?.id])

  const activeRecord = useMemo(() => {
    if (!activeId) return null
    const detail = detailQuery.data
    if (detail?.id === activeId) return detail
    return activeCandidate?.id === activeId ? activeCandidate : null
  }, [activeCandidate, activeId, detailQuery.data])

  useEffect(() => {
    if (acceptingRecord && !recordIsActive(acceptingRecord)) setAcceptingRecord(null)
  }, [acceptingRecord])

  useEffect(() => {
    if (detailQuery.data && !recordIsActive(detailQuery.data)) {
      setAcceptingRecord((current) => current?.id === detailQuery.data?.id ? null : current)
    }
  }, [detailQuery.data])

  const refreshAfterAccept = async (record: UpdateRecord) => {
    setAcceptingRecord(record)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.updates.status() }),
      queryClient.invalidateQueries({ queryKey: keys.updates.all() }),
    ])
    if (!recordIsActive(record)) setAcceptingRecord(null)
  }

  const check = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.GET('/api/updates/status', { params: { query: { refresh: true } } })
      if (error) throw apiError(error)
      if (!data) throw new Error('更新状态接口没有返回数据。')
      return data as unknown as UpdateStatusResponse
    },
    onSuccess: (data) => queryClient.setQueryData(keys.updates.status(), data),
  })

  const submit = useMutation({
    mutationFn: ({ target, options }: { target: UpdateTarget; options: UpdateOptions }) => postUpdate(target, options),
    onSuccess: refreshAfterAccept,
    onError: () => queryClient.invalidateQueries({ queryKey: keys.updates.status() }),
  })

  const cancel = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.DELETE('/api/updates/{update_id}', { params: { path: { update_id: id } } })
      if (error) throw apiError(error)
      return id
    },
    onSuccess: (id) => Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.updates.status() }),
      queryClient.invalidateQueries({ queryKey: detailKey(id) }),
      queryClient.invalidateQueries({ queryKey: keys.updates.all() }),
    ]),
  })

  const retry = useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await api.POST('/api/updates/{update_id}/retry', { params: { path: { update_id: id } } })
      if (error) throw apiError(error)
      const record = data as unknown as UpdateRecord | undefined
      if (!record) throw new Error('重试请求已提交，但接口没有返回记录编号。')
      return record
    },
    onSuccess: refreshAfterAccept,
  })

  return {
    statusQuery,
    historyQuery,
    detailQuery,
    historyDetailQuery,
    historyDetailId,
    setHistoryDetailId,
    activeRecord,
    check,
    submit,
    cancel,
    retry,
  }
}
