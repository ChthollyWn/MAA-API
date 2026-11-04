import type { SpanOut } from './api'

export interface SpanRow {
  span: SpanOut
  /** 根为 0；渲染时缩进最多 6 级。 */
  depth: number
  /** 上级不在这条 trace 里（被清理或跨 trace），当顶层显示。 */
  orphan: boolean
}

function timeOf(value: string | null | undefined): number {
  if (!value) return Number.NaN
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : Number.NaN
}

function sortKey(span: SpanOut): number {
  const started = timeOf(span.started_at)
  return Number.isNaN(started) ? Number.POSITIVE_INFINITY : started
}

/**
 * 按开始时间做深度优先排列：先按开始时间排同级，再递归展开 children。
 *
 * 上级缺失的 span 当顶层，`orphan: true`（服务重启时根 span 可能没写下来，
 * 它已经结束的子 span 仍然要能看）。
 */
export function buildSpanRows(spans: readonly SpanOut[]): SpanRow[] {
  const ordered = spans
    .map((span, index) => ({ span, index }))
    .sort((left, right) => sortKey(left.span) - sortKey(right.span) || left.index - right.index)

  const byId = new Map(ordered.map(({ span }) => [span.span_id, span]))
  const children = new Map<string, SpanOut[]>()
  const roots: SpanOut[] = []

  for (const { span } of ordered) {
    const parentId = span.parent_span_id
    if (parentId && parentId !== span.span_id && byId.has(parentId)) {
      const list = children.get(parentId)
      if (list) list.push(span)
      else children.set(parentId, [span])
    } else {
      roots.push(span)
    }
  }

  const rows: SpanRow[] = []
  const visited = new Set<string>()
  const visit = (span: SpanOut, depth: number) => {
    if (visited.has(span.span_id)) return
    visited.add(span.span_id)
    const parentId = span.parent_span_id
    rows.push({ span, depth, orphan: Boolean(parentId && !byId.has(parentId)) })
    for (const child of children.get(span.span_id) ?? []) visit(child, depth + 1)
  }

  for (const span of roots) visit(span, 0)
  // 父链成环（异常数据）时上面的根遍历到不了，补成顶层，避免整行丢失。
  for (const { span } of ordered) {
    if (!visited.has(span.span_id)) visit(span, 0)
  }
  return rows
}

function spanRange(row: SpanRow): { start: number; end: number } {
  const started = timeOf(row.span.started_at)
  const fallbackDuration = typeof row.span.duration_ms === 'number' ? Math.max(row.span.duration_ms, 0) : 0
  if (Number.isNaN(started)) return { start: 0, end: 0 }
  const ended = timeOf(row.span.ended_at)
  return { start: started, end: Number.isNaN(ended) ? started + fallbackDuration : ended }
}

export interface SpanBar {
  left: number
  width: number
}

/**
 * 横条在整条 trace 里的位置与宽度（百分比）。宽度至少 0.5，且整条横条不出右边界。
 */
export function spanBar(row: SpanRow, start: number, end: number): SpanBar {
  const total = end - start
  if (!Number.isFinite(total) || total <= 0) return { left: 0, width: 100 }
  const range = spanRange(row)
  const rawLeft = ((range.start - start) / total) * 100
  const rawWidth = ((range.end - range.start) / total) * 100
  const left = Math.min(Math.max(rawLeft, 0), 99.5)
  const width = Math.min(Math.max(rawWidth, 0.5), 100 - left)
  return { left, width }
}

/** 整个瀑布图的时间范围；没有有效时间时给 1ms 的窗口，避免除零。 */
export function traceBounds(spans: readonly SpanOut[]): { start: number; end: number } {
  let start = Number.POSITIVE_INFINITY
  let end = Number.NEGATIVE_INFINITY
  for (const span of spans) {
    const range = spanRange({ span, depth: 0, orphan: false })
    if (!Number.isFinite(range.start)) continue
    start = Math.min(start, range.start)
    end = Math.max(end, range.end)
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    const now = Date.now()
    return { start: now, end: now + 1 }
  }
  return { start, end: Math.max(end, start + 1) }
}
