import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, ClipboardList, RefreshCw, SearchX, SlidersHorizontal } from 'lucide-react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RemovableChip } from '@/components/ui/chip'
import { InlineAlert } from '@/components/ui/inline-alert'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { AuditDetailSheet } from '@/features/audit/AuditDetailSheet'
import { AuditFilterSheet } from '@/features/audit/AuditFilterSheet'
import type { AuditCondition, AuditFilterKey, AuditFilters } from '@/features/audit/filters'
import {
  AUDIT_PAGE_SIZE, auditQuery, filterConditions, filtersFromSearch, pageFromSearch, searchFromFilters, withoutFilter, withPage,
} from '@/features/audit/filters'
import type { AuditPageResult, AuditRecord } from '@/features/audit/types'
import { callerText, formatDateTime, riskLabel, riskTone, statusLabel, statusTone } from '@/features/audit/types'
import { cn } from '@/lib/utils'

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

interface AuditToolbarProps {
  conditions: readonly AuditCondition[]
  total?: number
  refreshing: boolean
  onOpenFilters: () => void
  onRefresh: () => void
  onRemove: (key: AuditFilterKey) => void
  onClear: () => void
}

/** 吸顶工具条：筛选入口、结果数与刷新，生效条件以芯片显示。 */
function AuditToolbar({ conditions, total, refreshing, onOpenFilters, onRefresh, onRemove, onClear }: AuditToolbarProps) {
  const count = conditions.length
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button
          variant={count ? 'soft' : 'outline'}
          className="shrink-0 px-3.5"
          aria-haspopup="dialog"
          aria-label={count ? `筛选，已生效 ${count} 项` : '筛选'}
          onClick={onOpenFilters}
        >
          <SlidersHorizontal aria-hidden="true" />
          筛选
          {count ? (
            <span aria-hidden="true" className="grid h-5 min-w-5 place-items-center rounded-full bg-primary px-1.5 text-xs font-semibold tabular-nums text-primary-foreground">
              {count}
            </span>
          ) : null}
        </Button>
        <p className="min-w-0 flex-1 truncate text-footnote text-muted-foreground tabular-nums">{total === undefined ? null : `共 ${total} 条`}</p>
        <Button variant="ghost" size="icon" aria-label="刷新" disabled={refreshing} onClick={onRefresh}>
          <RefreshCw aria-hidden="true" className={cn(refreshing && 'animate-spin')} />
        </Button>
      </div>
      {count ? (
        <div className="flex items-center gap-2">
          <ul
            aria-label="生效的筛选条件"
            className="-my-1 flex min-w-0 flex-1 gap-2 overflow-x-auto py-1 pr-4 scrollbar-none [mask-image:linear-gradient(to_right,black_calc(100%-1rem),transparent)] sm:flex-wrap sm:pr-0 sm:[mask-image:none]"
          >
            {conditions.map((condition) => (
              <li key={condition.key} className="shrink-0">
                <RemovableChip removeLabel={`移除筛选条件：${condition.label}`} onRemove={() => onRemove(condition.key)}>
                  {condition.label}
                </RemovableChip>
              </li>
            ))}
          </ul>
          <Button variant="ghost" size="sm" className="shrink-0 px-2.5 text-primary-text" onClick={onClear}>清除全部</Button>
        </div>
      ) : null}
    </div>
  )
}

function AuditRow({ record, onOpen }: { record: AuditRecord; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <span className="min-w-0 flex-1 space-y-1">
        <span className="flex items-start justify-between gap-2">
          <span className="min-w-0 font-mono text-sm font-semibold leading-6 [overflow-wrap:anywhere]">{record.tool_name}</span>
          <Badge variant={statusTone(record.status)} className="mt-0.5">{statusLabel(record.status)}</Badge>
        </span>
        <span className="block text-footnote text-muted-foreground tabular-nums">
          {callerText(record)} · {formatDateTime(record.created_at)} · {record.duration_ms} ms
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          <Badge variant={riskTone(record.risk_level)}>{riskLabel(record.risk_level)}</Badge>
          {record.forced ? <Badge variant="warning">强制执行</Badge> : null}
          {record.error_code ? <span className="font-mono text-xs text-danger [overflow-wrap:anywhere]">{record.error_code}</span> : null}
        </span>
      </span>
      <ChevronRight aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground/70" />
    </button>
  )
}

interface SheetState {
  open: boolean
  /** 每次打开筛选面板换一个 key，让草稿从地址里的筛选重新开始。 */
  key: number
}

export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [filterSheet, setFilterSheet] = useState<SheetState>({ open: false, key: 0 })
  const [detail, setDetail] = useState<{ id: number; open: boolean } | null>(null)
  const page = pageFromSearch(searchParams)
  const filters = useMemo(() => filtersFromSearch(searchParams), [searchParams])
  const conditions = useMemo(() => filterConditions(filters), [filters])
  const query = useMemo(() => auditQuery(searchParams, page), [page, searchParams])

  const audits = useQuery({
    queryKey: keys.agent.auditList(query),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/audits', { params: { query } })
      if (error) throw toApiError(error, response)
      return data as unknown as AuditPageResult
    },
    placeholderData: keepPreviousData,
    retry: false,
  })
  const detailId = detail?.id ?? null
  const detailQuery = useQuery({
    queryKey: keys.agent.auditDetail(detailId ?? 0),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/audits/{audit_id}', {
        params: { path: { audit_id: detailId ?? 0 } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as AuditRecord
    },
    enabled: detailId !== null,
    retry: false,
  })

  const items = audits.data?.items ?? []
  const total = audits.data?.total
  const pageCount = Math.max(1, Math.ceil((total ?? 0) / AUDIT_PAGE_SIZE))
  const shownDetail = detailId === null
    ? null
    : detailQuery.data?.audit_id === detailId ? detailQuery.data : items.find((record) => record.audit_id === detailId) ?? null

  const applyFilters = (next: AuditFilters) => {
    setSearchParams(searchFromFilters(next))
    setFilterSheet((current) => ({ ...current, open: false }))
  }
  const clearFilters = () => {
    setSearchParams(new URLSearchParams())
    setFilterSheet((current) => ({ ...current, open: false }))
  }
  const goToPage = (next: number) => {
    setSearchParams(withPage(searchParams, next))
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0
  }

  return (
    <Page
      title="Agent 审计"
      back="/more"
      toolbar={(
        <AuditToolbar
          conditions={conditions}
          total={total}
          refreshing={audits.isFetching}
          onOpenFilters={() => setFilterSheet((current) => ({ open: true, key: current.key + 1 }))}
          onRefresh={() => void audits.refetch()}
          onRemove={(key) => setSearchParams(withoutFilter(searchParams, key))}
          onClear={clearFilters}
        />
      )}
    >
      <div className="space-y-3 pb-4">
        {audits.error && audits.data ? (
          <InlineAlert tone="danger" action={<Button variant="outline" size="sm" onClick={() => void audits.refetch()}>重试</Button>}>
            刷新失败：{queryError(audits.error)}
          </InlineAlert>
        ) : null}

        {audits.isPending ? (
          <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取审计记录…" /></div>
        ) : audits.error && !audits.data ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState message={`读取审计记录失败：${queryError(audits.error)}`} onRetry={() => void audits.refetch()} retrying={audits.isFetching} />
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            {conditions.length || page > 1 ? (
              <EmptyState
                icon={SearchX}
                title="还没有符合条件的审计记录"
                description={conditions.length ? '放宽筛选条件试试' : '这一页已经没有记录了'}
                action={(
                  <>
                    {page > 1 ? <Button variant="outline" size="sm" onClick={() => goToPage(1)}>回到第一页</Button> : null}
                    {conditions.length ? <Button variant="outline" size="sm" onClick={clearFilters}>清除筛选</Button> : null}
                  </>
                )}
              />
            ) : (
              <EmptyState icon={ClipboardList} title="还没有审计记录" description="Agent 调用工具后会自动记录在这里" />
            )}
          </div>
        ) : (
          <ul
            aria-label="审计记录"
            aria-busy={audits.isPlaceholderData || undefined}
            className={cn('divide-y overflow-hidden rounded-xl border bg-card shadow-card transition-opacity', audits.isPlaceholderData && 'opacity-60')}
          >
            {items.map((record) => (
              <li key={record.audit_id}>
                <AuditRow record={record} onOpen={() => setDetail({ id: record.audit_id, open: true })} />
              </li>
            ))}
          </ul>
        )}

        {total !== undefined && total > AUDIT_PAGE_SIZE ? (
          <nav aria-label="审计分页" className="flex items-center justify-between gap-3 pt-1">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
              <ChevronLeft aria-hidden="true" />上一页
            </Button>
            <span className="text-sm text-muted-foreground tabular-nums">第 {page} / {pageCount} 页</span>
            <Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => goToPage(page + 1)}>
              下一页<ChevronRight aria-hidden="true" />
            </Button>
          </nav>
        ) : null}
      </div>

      <AuditFilterSheet
        key={filterSheet.key}
        open={filterSheet.open}
        onOpenChange={(open) => setFilterSheet((current) => ({ ...current, open }))}
        filters={filters}
        onApply={applyFilters}
        onClear={clearFilters}
      />
      <AuditDetailSheet
        open={detail?.open ?? false}
        onOpenChange={(open) => setDetail((current) => current && { ...current, open })}
        record={shownDetail}
        loading={detailQuery.isFetching && detailQuery.data?.audit_id !== detailId}
        error={detailQuery.error ? queryError(detailQuery.error) : null}
      />
    </Page>
  )
}

export default AuditPage
