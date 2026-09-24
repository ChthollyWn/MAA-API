import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import type { LogRecord } from '@/realtime/events'

export type LogLevelFilter = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface LogHistoryFilters {
  levels: LogLevelFilter[]
  keyword: string
  since: number | undefined
  until: number | undefined
}

interface HistoryPage {
  items: LogRecord[]
  nextCursor: number | null
  hasMore: boolean
}

interface HistoryState {
  signature: string
  records: LogRecord[]
  cursor: number | null
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null
  backfillLoading: boolean
  backfillError: string | null
  backfillResolved: boolean
  recoveredCount: number
}

const PAGE_SIZE = 100

function asRecord(value: unknown): LogRecord | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<LogRecord>
  if (!Number.isSafeInteger(candidate.id) || typeof candidate.source !== 'string') return null
  return candidate as LogRecord
}

function readPage(value: unknown): HistoryPage {
  if (!value || typeof value !== 'object') return { items: [], nextCursor: null, hasMore: false }
  const response = value as { items?: unknown; page?: { next_cursor?: unknown; has_more?: unknown } }
  const items = Array.isArray(response.items)
    ? response.items.map(asRecord).filter((row): row is LogRecord => row !== null)
    : []
  const cursor = response.page?.next_cursor
  return {
    items,
    nextCursor: Number.isSafeInteger(cursor) ? Number(cursor) : null,
    hasMore: response.page?.has_more === true,
  }
}

function mergeRecords(current: readonly LogRecord[], incoming: readonly LogRecord[]): LogRecord[] {
  const records = new Map<number, LogRecord>()
  for (const record of current) records.set(record.id, record)
  for (const record of incoming) records.set(record.id, record)
  return [...records.values()].sort((left, right) => left.id - right.id)
}

function filtersSignature(filters: LogHistoryFilters): string {
  return JSON.stringify([
    [...filters.levels].sort(),
    filters.keyword.trim(),
    filters.since ?? null,
    filters.until ?? null,
  ])
}

async function requestPage(filters: LogHistoryFilters, cursor: {
  beforeId?: number
  afterId?: number
}, signal?: AbortSignal, order: 'asc' | 'desc' = 'desc'): Promise<HistoryPage> {
  const query: Record<string, unknown> = {
    order,
    size: PAGE_SIZE,
  }
  const keyword = filters.keyword.trim()
  if (keyword) query.q = keyword
  if (filters.since !== undefined) query.since = filters.since
  if (filters.until !== undefined) query.until = filters.until
  if (cursor.beforeId !== undefined) query.before_id = cursor.beforeId
  if (cursor.afterId !== undefined) query.after_id = cursor.afterId

  const { data, error, response } = await api.GET('/api/system/logs', {
    params: { query },
    signal,
  })
  if (error) throw toApiError(error, response)
  return readPage(data)
}

function initialState(signature = ''): HistoryState {
  return {
    signature,
    records: [],
    cursor: null,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: null,
    backfillLoading: false,
    backfillError: null,
    backfillResolved: false,
    recoveredCount: 0,
  }
}

export function useLogHistory(filters: LogHistoryFilters, lastSeenId: number) {
  const signature = useMemo(
    () => filtersSignature(filters),
    [filters.levels, filters.keyword, filters.since, filters.until],
  )
  const [state, setState] = useState<HistoryState>(() => initialState(signature))
  const generation = useRef(0)
  const filtersRef = useRef(filters)
  filtersRef.current = filters

  useEffect(() => {
    const currentGeneration = ++generation.current
    const controller = new AbortController()
    setState({ ...initialState(signature), loading: true })

    void (async () => {
      const latest = await requestPage(filters, {}, controller.signal)
      const missed: LogRecord[] = []
      let afterId = lastSeenId
      if (lastSeenId > 0) {
        while (true) {
          const page = await requestPage(filters, { afterId }, controller.signal, 'asc')
          missed.push(...page.items)
          const nextAfter = page.items.at(-1)?.id
          if (!page.hasMore || nextAfter === undefined || nextAfter <= afterId) break
          afterId = nextAfter
        }
      }
      if (generation.current !== currentGeneration) return
      setState({
        ...initialState(signature),
        records: mergeRecords(latest.items, missed),
        cursor: latest.nextCursor,
        hasMore: latest.hasMore,
      })
    })().catch((error: unknown) => {
      if (generation.current !== currentGeneration || controller.signal.aborted) return
      setState({ ...initialState(signature), error: toApiError(error).message })
    })

    return () => {
      controller.abort()
      generation.current += 1
    }
  }, [filters.levels, filters.keyword, filters.since, filters.until, lastSeenId, signature])

  const retry = useCallback(() => {
    const currentGeneration = ++generation.current
    setState((current) => ({ ...current, loading: true, error: null }))
    void requestPage(filtersRef.current, {}).then((page) => {
      if (generation.current !== currentGeneration) return
      setState({
        ...initialState(signature),
        records: mergeRecords([], page.items),
        cursor: page.nextCursor,
        hasMore: page.hasMore,
      })
    }).catch((error: unknown) => {
      if (generation.current !== currentGeneration) return
      setState({ ...initialState(signature), error: toApiError(error).message })
    })
  }, [signature])

  const loadOlder = useCallback(async () => {
    if (
      state.signature !== signature || state.loading || state.loadingMore ||
      !state.hasMore || state.cursor === null
    ) return
    const currentGeneration = generation.current
    const beforeId = state.cursor
    setState((current) => ({ ...current, loadingMore: true }))
    try {
      const page = await requestPage(filtersRef.current, { beforeId })
      if (generation.current !== currentGeneration) return
      setState((current) => current.signature !== signature ? current : ({
        ...current,
        records: mergeRecords(current.records, page.items),
        cursor: page.nextCursor,
        hasMore: page.hasMore,
        loadingMore: false,
      }))
    } catch (error) {
      if (generation.current !== currentGeneration) return
      setState((current) => current.signature !== signature ? current : ({
        ...current,
        loadingMore: false,
        error: toApiError(error).message,
      }))
    }
  }, [signature, state])

  const loadTruncatedHistory = useCallback(async (liveRecords: readonly LogRecord[]) => {
    if (state.backfillLoading || state.backfillResolved || lastSeenId < 0) return
    const currentGeneration = generation.current
    const beforeId = liveRecords.reduce<number | undefined>((minimum, record) =>
      minimum === undefined ? record.id : Math.min(minimum, record.id), undefined)
    let afterId = lastSeenId
    const recovered: LogRecord[] = []
    setState((current) => ({ ...current, backfillLoading: true, backfillError: null }))

    try {
      while (true) {
        const page = await requestPage(filtersRef.current, { afterId, beforeId }, undefined, 'asc')
        recovered.push(...page.items)
        const nextAfter = page.items.at(-1)?.id
        if (!page.hasMore || nextAfter === undefined || nextAfter <= afterId) break
        afterId = nextAfter
      }
      if (generation.current !== currentGeneration) return
      setState((current) => current.signature !== signature ? current : ({
        ...current,
        records: mergeRecords(current.records, recovered),
        backfillLoading: false,
        backfillResolved: true,
        recoveredCount: recovered.length,
      }))
    } catch (error) {
      if (generation.current !== currentGeneration) return
      setState((current) => current.signature !== signature ? current : ({
        ...current,
        backfillLoading: false,
        backfillError: toApiError(error).message,
      }))
    }
  }, [lastSeenId, signature, state.backfillLoading, state.backfillResolved])

  const ready = state.signature === signature
  const records = ready ? state.records : []
  return {
    records,
    hasMore: ready && state.hasMore,
    loading: !ready || state.loading,
    loadingMore: ready && state.loadingMore,
    error: ready ? state.error : null,
    retry,
    loadOlder,
    loadTruncatedHistory,
    backfillLoading: ready && state.backfillLoading,
    backfillError: ready ? state.backfillError : null,
    backfillResolved: ready && state.backfillResolved,
    recoveredCount: ready ? state.recoveredCount : 0,
  }
}
