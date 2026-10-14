import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { RefreshCw, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { accountKeys, fetchAccountSummary, fetchOperators } from './api'
import { OperatorFilterBar } from './OperatorFilterBar'
import { OperatorGrid } from './OperatorGrid'
import { RecognitionStatus } from './RecognitionStatus'
import { SklandBindingCard } from './SklandBindingCard'
import {
  DEFAULT_OPERATOR_FILTERS,
  OPERATOR_PAGE_SIZE,
  matchesOwned,
  operatorQueryKeyPart,
  toOperatorQuery,
  type OperatorFilters,
} from './filters'
import { errorText } from './format'
import { useDebouncedValue } from './use-debounced-value'

/** 干员页（M47 只读版）：森空岛同步状态 + 绑定/同步卡片 + 筛选 + **卡片网格**。
 *
 * M47 起干员数据的唯一来源是森空岛练度同步：页面不再有编辑抽屉、差异条与来源标记，
 * 内核 `OperBox` 识别任务也随之下线（docs/13 §8.70）。 */
export function OperatorsTab() {
  const [filters, setFilters] = useState<OperatorFilters>(DEFAULT_OPERATOR_FILTERS)
  // 搜索防抖：输入停下 300 ms 才换查询键，别的筛选立即生效。
  const debouncedQuery = useDebouncedValue(filters.query, 300)
  const effectiveFilters = useMemo<OperatorFilters>(() => ({ ...filters, query: debouncedQuery }), [filters, debouncedQuery])

  const summary = useQuery({
    queryKey: accountKeys.summary(),
    queryFn: ({ signal }) => fetchAccountSummary(signal),
    retry: false,
  })

  const list = useInfiniteQuery({
    queryKey: accountKeys.operators(operatorQueryKeyPart(effectiveFilters)),
    queryFn: ({ pageParam, signal }) => fetchOperators(toOperatorQuery(effectiveFilters, pageParam), signal),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.limit < last.total ? last.offset + last.limit : undefined),
    retry: false,
  })

  const rows = (list.data?.pages ?? [])
    .flatMap((page) => page.items ?? [])
    .filter((row) => matchesOwned(row, filters.owned))
  const total = list.data?.pages.at(-1)?.total ?? rows.length
  const searching = effectiveFilters.query !== filters.query || list.isFetching

  return (
    <div className="space-y-3.5">
      {/* 顶部数据源状态区：桌面端双列并排，移动端紧凑堆叠 */}
      <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2 lg:items-start">
        {summary.data ? (
          <RecognitionStatus
            label="森空岛练度同步"
            entry={summary.data.operators}
            unit="名"
            description="干员数据只来自森空岛同步；未绑定时先在右边绑定账号，再点「同步练度」。"
            staleHint="数据已超过 {days} 天没同步，需要时请在右边的卡片里点「同步练度」。"
            recognizedPrefix="同步于"
          />
        ) : summary.error ? (
          <InlineAlert tone="warning">读取账号概要失败：{errorText(summary.error)}</InlineAlert>
        ) : (
          <LoadingState label="正在读取账号概要…" />
        )}

        <SklandBindingCard />
      </div>

      <Section
        title="干员"
        description={list.isPending ? '正在加载列表…' : `共 ${total} 名 · 每页 ${OPERATOR_PAGE_SIZE} 名`}
        actions={(
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="刷新干员列表"
            onClick={() => {
              void list.refetch()
              void summary.refetch()
            }}
          >
            {list.isRefetching && !list.isFetchingNextPage ? <Spinner /> : <RefreshCw aria-hidden="true" />}
          </Button>
        )}
      >
        <OperatorFilterBar
          filters={filters}
          onFiltersChange={setFilters}
          onClear={() => setFilters(DEFAULT_OPERATOR_FILTERS)}
          busy={searching}
        />

        {list.error && rows.length > 0 ? (
          <InlineAlert tone="warning">列表刷新失败：{errorText(list.error)}</InlineAlert>
        ) : null}

        {list.isPending ? (
          <LoadingState label="正在读取干员…" />
        ) : list.error && rows.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              title="干员列表读取失败"
              message={errorText(list.error)}
              onRetry={() => void list.refetch()}
              retrying={list.isFetching}
            />
          </div>
        ) : rows.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <EmptyState
              icon={Users}
              title="没有符合条件的干员"
              description={filters.owned === 'unowned'
                ? '当前页没有未拥有的干员；可以继续加载或换一页筛选条件'
                : '试试放宽筛选；还没有数据的话，先绑定森空岛账号并同步练度'}
            />
          </div>
        ) : (
          <div className="space-y-3">
            <OperatorGrid rows={rows} />
            {list.hasNextPage ? (
              <Button variant="outline" className="w-full" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                {list.isFetchingNextPage ? '正在加载…' : '加载更多'}
              </Button>
            ) : (
              <p className="py-1 text-center text-footnote text-muted-foreground">已显示全部 {rows.length} 名</p>
            )}
          </div>
        )}
      </Section>
    </div>
  )
}
