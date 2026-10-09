import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ScrollText } from 'lucide-react'
import { useVirtualizer, type VirtualizerOptions } from '@tanstack/react-virtual'
import { Button } from '@/components/ui/button'
import { ImageViewer } from '@/components/ui/image-viewer'
import { EmptyState, LoadingState } from '@/components/ui/states'
import { LogEntry } from '@/features/logs/LogEntry'
import { formatFullTime, type LogImage } from '@/features/logs/format'
import { cn } from '@/lib/utils'
import type { LogRecord } from '@/realtime/events'

const observeLogScrollRect: VirtualizerOptions<HTMLDivElement, HTMLDivElement>['observeElementRect'] = (
  instance,
  callback,
) => {
  const element = instance.scrollElement
  if (!element) return
  const measure = () => {
    const rect = element.getBoundingClientRect()
    callback({
      width: rect.width || element.clientWidth || 640,
      height: rect.height || element.clientHeight || 480,
    })
  }
  measure()
  if (typeof ResizeObserver === 'undefined') return
  const observer = new ResizeObserver(measure)
  observer.observe(element)
  return () => observer.disconnect()
}

const measureLogRow: VirtualizerOptions<HTMLDivElement, HTMLDivElement>['measureElement'] = (
  element,
  entry,
  instance,
) => {
  const measured = entry?.borderBoxSize?.[0]?.blockSize ?? element.getBoundingClientRect().height
  return measured > 0 ? measured : instance.options.estimateSize(instance.indexFromElement(element))
}

const ESTIMATED_ROW_HEIGHT = 84
/** 顶部「加载更早日志」一行的高度，同时是虚拟列表的 paddingStart，两者必须一致。 */
const LOAD_OLDER_HEIGHT = 48

interface ViewerState {
  open: boolean
  image: LogImage | null
  caption?: string
}

export interface LogListProps {
  records: LogRecord[]
  loading: boolean
  hasMore: boolean
  loadingMore: boolean
  onLoadOlder: () => Promise<void>
  /** 没有记录且不在加载时显示的内容，默认是空状态。 */
  placeholder?: ReactNode
  className?: string
}

/**
 * 虚拟滚动的日志列表：初次显示停在最新一条；在底部时新日志自动跟随，
 * 不在底部时不抢滚动位置，改为提示「N 条新日志」。滚到顶部读取更早的历史。
 */
export function LogList({ records, loading, hasMore, loadingMore, onLoadOlder, placeholder, className }: LogListProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const initializedRef = useRef(false)
  const loadingLockRef = useRef(false)
  const previousNewestIdRef = useRef<number | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [newCount, setNewCount] = useState(0)
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<number>>(() => new Set())
  const [viewer, setViewer] = useState<ViewerState>({ open: false, image: null })
  const getScrollElement = useCallback(() => scrollRef.current, [])
  const getItemKey = useCallback((index: number) => records[index]?.id ?? index, [records])
  const estimateSize = useCallback(() => ESTIMATED_ROW_HEIGHT, [])
  const virtualizer = useVirtualizer({
    count: records.length,
    getScrollElement,
    estimateSize,
    getItemKey,
    measureElement: measureLogRow,
    initialRect: { width: 640, height: 480 },
    observeElementRect: observeLogScrollRect,
    overscan: 8,
    paddingStart: hasMore ? LOAD_OLDER_HEIGHT : 0,
  })

  const scrollToBottom = useCallback(() => {
    if (!records.length) return
    virtualizer.scrollToIndex(records.length - 1, { align: 'end' })
    const element = scrollRef.current
    if (element) element.scrollTop = element.scrollHeight
    setAtBottom(true)
    setNewCount(0)
  }, [records.length, virtualizer])

  useEffect(() => {
    if (loading || !records.length) return
    const newestId = records[records.length - 1]?.id ?? null
    if (!initializedRef.current) {
      initializedRef.current = true
      previousNewestIdRef.current = newestId
      requestAnimationFrame(scrollToBottom)
      return
    }

    const previousNewestId = previousNewestIdRef.current
    const arrived = previousNewestId === null
      ? []
      : records.filter((record) => record.id > previousNewestId)
    if (newestId !== null && (previousNewestId === null || newestId > previousNewestId)) {
      previousNewestIdRef.current = newestId
    }
    if (arrived.length > 0) {
      if (atBottom) requestAnimationFrame(scrollToBottom)
      else setNewCount((count) => count + arrived.length)
    }
  }, [atBottom, loading, records, scrollToBottom])

  const loadOlder = useCallback(() => {
    const element = scrollRef.current
    if (!element || loadingLockRef.current || !hasMore || loadingMore) return
    loadingLockRef.current = true
    const oldHeight = element.scrollHeight
    const oldTop = element.scrollTop
    void onLoadOlder().finally(() => {
      requestAnimationFrame(() => {
        const current = scrollRef.current
        if (current) current.scrollTop = oldTop + Math.max(current.scrollHeight - oldHeight, 0)
        loadingLockRef.current = false
      })
    })
  }, [hasMore, loadingMore, onLoadOlder])

  const handleScroll = useCallback(() => {
    const element = scrollRef.current
    if (!element) return
    const nextAtBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 48
    setAtBottom(nextAtBottom)
    if (nextAtBottom) setNewCount(0)
    if (initializedRef.current && element.scrollTop <= 12) loadOlder()
  }, [loadOlder])

  const toggleExpanded = useCallback((id: number) => {
    setExpandedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const viewImage = useCallback((image: LogImage, record: LogRecord) => {
    setViewer({ open: true, image, caption: `日志截图 · ${formatFullTime(record.ts)}` })
  }, [])

  const empty = records.length === 0
  const virtualRows = virtualizer.getVirtualItems()
  return (
    <section aria-label="日志列表" className={cn('relative flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card', className)}>
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        onScroll={handleScroll}
        aria-label="日志滚动区域"
        role="region"
        tabIndex={0}
      >
        {loading && empty ? (
          <LoadingState label="正在加载历史日志" className="h-full min-h-48" />
        ) : empty ? (
          <div className="grid h-full min-h-48 place-items-center">
            {placeholder ?? <EmptyState icon={ScrollText} title="还没有匹配的日志" />}
          </div>
        ) : (
          <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
            {hasMore ? (
              <div className="absolute inset-x-0 top-0 flex items-center justify-center" style={{ height: LOAD_OLDER_HEIGHT }}>
                <Button variant="ghost" size="sm" className="text-primary-text" loading={loadingMore} onClick={loadOlder}>
                  {loadingMore ? null : <ArrowUp aria-hidden="true" />}
                  {loadingMore ? '正在加载更早日志' : '加载更早日志'}
                </Button>
              </div>
            ) : null}
            {virtualRows.map((virtualRow) => {
              const record = records[virtualRow.index]
              if (!record) return null
              return (
                <div
                  key={virtualRow.key}
                  ref={virtualizer.measureElement}
                  data-index={virtualRow.index}
                  className={cn('absolute left-0 top-0 w-full', (hasMore || virtualRow.index > 0) && 'border-t')}
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  <LogEntry
                    record={record}
                    expanded={expandedIds.has(record.id)}
                    onToggle={toggleExpanded}
                    onViewImage={viewImage}
                  />
                </div>
              )
            })}
          </div>
        )}
      </div>
      {!atBottom && !empty ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-3">
          <Button
            variant={newCount > 0 ? 'default' : 'outline'}
            className="pointer-events-auto rounded-full px-4 text-sm shadow-float"
            onClick={scrollToBottom}
          >
            <ArrowDown aria-hidden="true" />
            {newCount > 0 ? `${newCount.toLocaleString()} 条新日志` : '回到底部'}
          </Button>
        </div>
      ) : null}
      <ImageViewer
        open={viewer.open}
        onOpenChange={(open) => setViewer((current) => ({ ...current, open }))}
        src={viewer.image?.full ?? null}
        alt="日志截图原图"
        caption={viewer.caption}
      />
    </section>
  )
}
