import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { RefreshCw, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { accountKeys, fetchAccountSummary, fetchOperators, type OperatorRow } from './api'
import { OperatorDiffBanner } from './OperatorDiffBanner'
import { OperatorEditorSheet } from './OperatorEditorSheet'
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

/** 干员页（规格 §7.5、M42 §4.8、M43 §4.4）：识别状态 + 森空岛绑定/同步 + 差异提示条 + 筛选 +
 * **卡片网格**（默认隐藏未拥有）+ 编辑抽屉。点卡片打开抽屉，其余段落与 M42 一致。 */
export function OperatorsTab() {
  const [filters, setFilters] = useState<OperatorFilters>(DEFAULT_OPERATOR_FILTERS)
  const [editing, setEditing] = useState<OperatorRow | null>(null)
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
    <div className="space-y-4 pb-4">
      {summary.data ? (
        <RecognitionStatus
          label="干员识别"
          entry={summary.data.operators}
          unit="名"
          description="数据由任务里的「干员识别」写入；这一页不提供刷新识别按钮。"
        />
      ) : summary.error ? (
        <InlineAlert tone="warning">读取账号概要失败：{errorText(summary.error)}</InlineAlert>
      ) : (
        <LoadingState label="正在读取账号概要…" />
      )}

      {/* 森空岛练度是**另一个写入者**（技能等级 / 专精 / 模组 / 信赖，M42 §4.5），不是
          「刷新干员识别」：识别仍由任务触发，这里只给绑定与同步一个入口。 */}
      <SklandBindingCard />

      <OperatorDiffBanner />

      <OperatorFilterBar
        filters={filters}
        onFiltersChange={setFilters}
        onClear={() => setFilters(DEFAULT_OPERATOR_FILTERS)}
        busy={searching}
      />

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
              : '试试放宽筛选，或者先运行「干员识别」任务'}
          />
        </div>
      ) : (
        <Section
          title="干员"
          description={`共 ${total} 名 · 每页 ${OPERATOR_PAGE_SIZE} 名`}
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
          {list.error ? <InlineAlert tone="warning">列表刷新失败：{errorText(list.error)}</InlineAlert> : null}
          <OperatorGrid rows={rows} onSelect={setEditing} />
          {list.hasNextPage ? (
            <Button variant="outline" className="w-full" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
              {list.isFetchingNextPage ? '正在加载…' : '加载更多'}
            </Button>
          ) : (
            <p className="py-1 text-center text-footnote text-muted-foreground">已显示全部 {rows.length} 名</p>
          )}
        </Section>
      )}

      {editing ? <OperatorEditorSheet key={editing.char_id} row={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  )
}
