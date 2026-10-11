import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { RefreshCw, Users } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { accountKeys, fetchAccountSummary, fetchOperators, type FieldSource, type OperatorRow } from './api'
import { OperatorDiffBanner } from './OperatorDiffBanner'
import { OperatorEditorSheet } from './OperatorEditorSheet'
import { OperatorFilterBar } from './OperatorFilterBar'
import { RecognitionStatus } from './RecognitionStatus'
import { TrainingScanCard } from './TrainingScanCard'
import {
  DEFAULT_OPERATOR_FILTERS,
  OPERATOR_PAGE_SIZE,
  matchesOwned,
  operatorQueryKeyPart,
  toOperatorQuery,
  type OperatorFilters,
} from './filters'
import {
  SOURCE_HINTS,
  SOURCE_LABELS,
  eliteLabel,
  errorText,
  masteryText,
  moduleText,
  professionLabel,
  rarityLabel,
  rowSources,
  trainingValueText,
} from './format'
import { useDebouncedValue } from './use-debounced-value'

const SOURCE_TONES: Record<FieldSource, 'neutral' | 'success' | 'primary'> = {
  recognition: 'neutral',
  manual: 'primary',
  agent: 'success',
}

function FieldCell({ label, value, unknown = false }: { label: string; value: string; unknown?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd
        className={unknown ? 'truncate text-muted-foreground' : 'truncate font-medium tabular-nums'}
        title={unknown ? '识别结果里没有这一项；人工或 Agent 补录后才会显示' : undefined}
      >
        {value}
      </dd>
    </div>
  )
}

function OperatorLine({ row, onEdit }: { row: OperatorRow; onEdit: () => void }) {
  const sources = rowSources(row)
  // 练度六列的 `null` ＝ 未识别（M35）：一律写「未识别」，不折算成 0 级 / 技能 1 / 无专精。
  const isUnknown = (value: unknown) => value === null || value === undefined
  return (
    <li data-testid={`operator-${row.char_id}`} className="space-y-1.5 p-3.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-[0.9375rem] font-medium">{row.name}</span>
        <Badge variant="neutral">{rarityLabel(row.rarity)}</Badge>
        <span className="text-footnote text-muted-foreground">{professionLabel(row.profession)}</span>
        {row.own ? null : <Badge variant="outline">未拥有</Badge>}
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto shrink-0"
          aria-label={`编辑 ${row.name}`}
          onClick={onEdit}
        >
          编辑
        </Button>
      </div>
      <dl className="grid grid-cols-3 gap-x-3 gap-y-1 text-footnote sm:grid-cols-5">
        <FieldCell label="精英化" value={eliteLabel(row.elite)} unknown={isUnknown(row.elite)} />
        <FieldCell label="等级" value={trainingValueText(row.level)} unknown={isUnknown(row.level)} />
        <FieldCell label="潜能" value={trainingValueText(row.potential)} unknown={isUnknown(row.potential)} />
        <FieldCell label="技能等级" value={trainingValueText(row.skill_level)} unknown={isUnknown(row.skill_level)} />
        <FieldCell label="专精" value={masteryText(row.masteries)} unknown={isUnknown(row.masteries)} />
        <FieldCell label="模组" value={moduleText(row.modules)} unknown={isUnknown(row.modules)} />
      </dl>
      <div className="flex flex-wrap items-center gap-1">
        <span className="text-footnote text-muted-foreground">字段来源</span>
        {sources.length ? sources.map((source) => (
          <Badge key={source} variant={SOURCE_TONES[source]} data-testid={`operator-source-${row.char_id}-${source}`}>
            <span title={SOURCE_HINTS[source]}>{SOURCE_LABELS[source]}</span>
          </Badge>
        )) : <span className="text-footnote text-muted-foreground">—</span>}
      </div>
    </li>
  )
}

/** 干员页（规格 §7.5、M36 §4.8）：识别状态 + 练度扫描 + 差异提示条 + 筛选 + 列表（默认隐藏未拥有）+ 编辑抽屉。 */
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

      {/* 练度扫描是**另一种数据**（技能等级 / 专精 / 模组，M36 §4.8），不是「刷新干员识别」：
          识别仍由任务触发，这里只给自己的扫描一个入口。 */}
      <TrainingScanCard />

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
          <ul className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
            {rows.map((row) => (
              <OperatorLine key={row.char_id} row={row} onEdit={() => setEditing(row)} />
            ))}
          </ul>
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
