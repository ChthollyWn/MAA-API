import { create } from 'zustand'
import type { LogRecord } from '@/realtime/events'

export const LOG_BUFFER_CAPACITY = 2_000

export type LogSource = string

interface LogBufferState {
  bySource: Record<LogSource, LogRecord[]>
  append: (record: LogRecord) => void
  appendMany: (records: readonly LogRecord[]) => void
  clear: (source?: LogSource) => void
}

const pending = new Map<LogSource, LogRecord[]>()
let frame: number | ReturnType<typeof setTimeout> | undefined

function requestBatchFlush(): void {
  if (frame !== undefined) return
  const flush = () => {
    frame = undefined
    useLogBuffer.getState().flushPending()
  }

  if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(flush)
  else frame = setTimeout(flush, 16)
}

function queueRecords(records: readonly LogRecord[]): void {
  for (const record of records) {
    if (!record || typeof record.source !== 'string') continue
    const batch = pending.get(record.source) ?? []
    batch.push(record)
    pending.set(record.source, batch)
  }
  if (pending.size > 0) requestBatchFlush()
}

export const useLogBuffer = create<LogBufferState & { flushPending: () => void }>((set) => ({
  bySource: {},
  append: (record) => queueRecords([record]),
  appendMany: (records) => queueRecords(records),
  clear: (source) => {
    if (source === undefined) {
      pending.clear()
      set({ bySource: {} })
      return
    }
    pending.delete(source)
    set((state) => {
      const bySource = { ...state.bySource }
      delete bySource[source]
      return { bySource }
    })
  },
  flushPending: () => {
    if (pending.size === 0) return
    const batches = new Map(pending)
    pending.clear()
    set((state) => {
      const bySource = { ...state.bySource }
      for (const [source, records] of batches) {
        bySource[source] = [...(bySource[source] ?? []), ...records].slice(-LOG_BUFFER_CAPACITY)
      }
      return { bySource }
    })
  },
}))
