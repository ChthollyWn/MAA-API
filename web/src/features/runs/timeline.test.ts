import { describe, expect, it } from 'vitest'
import type { SpanOut } from '@/features/runs/api'
import { buildSpanRows, spanBar, traceBounds } from '@/features/runs/timeline'

const BASE = Date.parse('2026-11-02T08:00:00.000Z')

function makeSpan(options: {
  id: string
  parent?: string | null
  startMs: number
  durationMs?: number
  runId?: string
}): SpanOut {
  const started = new Date(BASE + options.startMs).toISOString()
  const duration = options.durationMs ?? 100
  return {
    span_id: options.id,
    parent_span_id: options.parent ?? null,
    run_id: options.runId ?? 'run-1',
    name: 'agent.run',
    kind: 'other',
    status: 'ok',
    attributes: {},
    started_at: started,
    ended_at: new Date(BASE + options.startMs + duration).toISOString(),
    duration_ms: duration,
  }
}

describe('buildSpanRows', () => {
  it('orders rows depth first', () => {
    const spans = [
      makeSpan({ id: 'root-b', startMs: 3000 }),
      makeSpan({ id: 'child-c', parent: 'root-a', startMs: 200 }),
      makeSpan({ id: 'root-a', startMs: 0 }),
      makeSpan({ id: 'grandchild', parent: 'child-c', startMs: 150 }),
      makeSpan({ id: 'child-d', parent: 'root-a', startMs: 100 }),
    ]

    const rows = buildSpanRows(spans)
    expect(rows.map((row) => row.span.span_id)).toEqual(['root-a', 'child-d', 'child-c', 'grandchild', 'root-b'])
    expect(rows.map((row) => row.depth)).toEqual([0, 1, 1, 2, 0])
    expect(rows.every((row) => !row.orphan)).toBe(true)
  })

  it('marks orphans as top level', () => {
    const spans = [
      makeSpan({ id: 'orphan', parent: 'missing-parent', startMs: 500 }),
      makeSpan({ id: 'root', startMs: 0 }),
    ]

    const rows = buildSpanRows(spans)
    expect(rows.map((row) => row.span.span_id)).toEqual(['root', 'orphan'])
    const orphan = rows.find((row) => row.span.span_id === 'orphan')
    expect(orphan?.depth).toBe(0)
    expect(orphan?.orphan).toBe(true)
    expect(rows.find((row) => row.span.span_id === 'root')?.orphan).toBe(false)
  })
})

describe('spanBar', () => {
  it('bars stay within range', () => {
    const spans = [
      makeSpan({ id: 'first', startMs: 0, durationMs: 100 }),
      makeSpan({ id: 'middle', startMs: 500, durationMs: 100 }),
      makeSpan({ id: 'last', startMs: 900, durationMs: 100 }),
      makeSpan({ id: 'outside', startMs: 5000, durationMs: 100 }),
    ]
    const rows = buildSpanRows(spans)
    const bounds = traceBounds(spans)
    expect(bounds.start).toBe(BASE)
    expect(bounds.end).toBe(BASE + 5100)

    for (const row of rows) {
      const bar = spanBar(row, BASE, BASE + 1000)
      expect(bar.left).toBeGreaterThanOrEqual(0)
      expect(bar.width).toBeGreaterThanOrEqual(0.5)
      expect(bar.left + bar.width).toBeLessThanOrEqual(100)
    }

    const middle = rows.find((row) => row.span.span_id === 'middle')!
    const bar = spanBar(middle, BASE, BASE + 1000)
    expect(bar.left).toBeCloseTo(50, 5)
    expect(bar.width).toBeCloseTo(10, 5)
  })
})
