import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, LoaderCircle } from 'lucide-react'
import { useVirtualizer, type VirtualizerOptions } from '@tanstack/react-virtual'
import { Button } from '@/components/ui/button'
import { LogEntry } from '@/features/logs/LogEntry'
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

interface LogListProps {
  records: LogRecord[]
  loading: boolean
  hasMore: boolean
  loadingMore: boolean
  onLoadOlder: () => Promise<void>
}

export function LogList({ records, loading, hasMore, loadingMore, onLoadOlder }: LogListProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const initializedRef = useRef(false)
  const loadingLockRef = useRef(false)
  const previousNewestIdRef = useRef<number | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [newCount, setNewCount] = useState(0)
  const getScrollElement = useCallback(() => scrollRef.current, [])
  const getItemKey = useCallback((index: number) => records[index]?.id ?? index, [records])
  const estimateSize = useCallback(() => 84, [])
  const virtualizer = useVirtualizer({
    count: records.length,
    getScrollElement,
    estimateSize,
    getItemKey,
    measureElement: measureLogRow,
    initialRect: { width: 640, height: 480 },
    observeElementRect: observeLogScrollRect,
    overscan: 8,
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

  const virtualRows = virtualizer.getVirtualItems()
  return (
    <section aria-label="日志列表" className="relative">
      {hasMore ? (
        <div className="flex justify-center border-b px-3 py-2">
          <Button variant="ghost" size="sm" disabled={loadingMore} onClick={loadOlder}>
            {loadingMore ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
            {loadingMore ? '正在加载更早日志' : '加载更早日志'}
          </Button>
        </div>
      ) : null}
      <div
        ref={scrollRef}
        className="h-[min(68dvh,48rem)] min-h-80 overflow-y-auto overscroll-contain rounded-b-xl"
        onScroll={handleScroll}
        aria-label="日志滚动区域"
        role="region"
        tabIndex={0}
      >
        {loading && records.length === 0 ? (
          <div className="flex min-h-80 items-center justify-center gap-2 text-sm text-muted-foreground" role="status">
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />正在加载历史日志
          </div>
        ) : records.length === 0 ? (
          <div className="grid min-h-80 place-items-center px-6 text-center text-sm text-muted-foreground" role="status">
            暂无匹配的历史或实时日志
          </div>
        ) : (
          <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
            {virtualRows.map((virtualRow) => {
              const record = records[virtualRow.index]
              if (!record) return null
              return (
                <div
                  key={virtualRow.key}
                  ref={virtualizer.measureElement}
                  data-index={virtualRow.index}
                  className="absolute left-0 top-0 w-full"
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  <LogEntry record={record} />
                </div>
              )
            })}
          </div>
        )}
      </div>
      {!atBottom && records.length > 0 ? (
        <div className="pointer-events-none absolute bottom-3 left-0 right-0 flex justify-center">
          <Button className="pointer-events-auto shadow-lg" size="sm" onClick={scrollToBottom}>
            <ArrowDown aria-hidden="true" />
            {newCount > 0 ? `${newCount} 条新日志 · 回到底部` : '回到底部'}
          </Button>
        </div>
      ) : null}
    </section>
  )
}
