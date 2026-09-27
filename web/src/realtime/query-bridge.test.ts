import '@testing-library/jest-dom/vitest'
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it } from 'vitest'
import { handleServerEvent } from '@/realtime/query-bridge'
import { useLogBuffer } from '@/stores/log-buffer'
import { formatLogTime } from '@/features/logs/format'

/** 只保留列表真正渲染的字段，避免把缓冲区内部状态写进断言。 */
function bufferRows(source: string) {
  // 缓冲区攒批到下一帧才入 state，断言前先手动 flush。
  useLogBuffer.getState().flushPending()
  return useLogBuffer.getState().bySource[source] ?? []
}

describe('realtime log records carry a timestamp', () => {
  beforeEach(() => {
    useLogBuffer.getState().clear()
  })

  it("uses the envelope timestamp for live 'log' events", () => {
    const ts = Date.UTC(2026, 8, 27, 12, 30, 45) / 1000
    const queryClient = new QueryClient()

    // 服务端实时事件的形状：ts 在信封上，data 里没有 ts（见 maa_api/api/ws.py
    // 的 broadcast_log → _record_data）。日志列表只读 data，所以这一层必须把
    // 信封时间并入记录，否则列表渲染成「时间未知」。
    handleServerEvent(
      {
        type: 'log',
        ts,
        data: {
          id: 9042,
          stream_id: 'stream-1',
          source: 'service',
          level: 'INFO',
          content: 'HTTP GET /api/system/logs completed',
        },
      },
      queryClient,
    )

    const rows = bufferRows('service')
    expect(rows).toHaveLength(1)
    expect(rows[0].ts).toBe(ts)
    expect(formatLogTime(rows[0].ts)).not.toBe('时间未知')
  })

  it("keeps the per-record timestamp of 'log_batch' records", () => {
    const records = [
      { id: 1, source: 'core', level: 'INFO', content: 'a', ts: 1_790_000_001 },
      { id: 2, source: 'core', level: 'INFO', content: 'b', ts: 1_790_000_002 },
    ]
    const queryClient = new QueryClient()

    handleServerEvent(
      {
        type: 'log_batch',
        ts: 1_790_000_999,
        data: { records, truncated: false, stream_id: 'stream-1' },
      },
      queryClient,
    )

    expect(bufferRows('core').map((row) => row.ts)).toEqual([1_790_000_001, 1_790_000_002])
  })

  it('prefers the record timestamp when a live record already has one', () => {
    const queryClient = new QueryClient()

    handleServerEvent(
      {
        type: 'log',
        ts: 1_790_000_999,
        data: { id: 3, source: 'task', level: 'INFO', content: 'c', ts: 1_790_000_500 },
      },
      queryClient,
    )

    expect(bufferRows('task')[0].ts).toBe(1_790_000_500)
  })
})
