import { useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Package, RotateCcw, Search, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import { AssetImage } from './AssetImage'
import { accountKeys, fetchAccountSummary, fetchDepot, type DepotItem } from './api'
import { itemIconPath } from './assets'
import { RecognitionStatus } from './RecognitionStatus'
import { approximateCountHint, depotCategoryLabel, errorText, isApproximateCountItem } from './format'
import { useDebouncedValue } from './use-debounced-value'

const DEPOT_PAGE_SIZE = 100
const UNCATEGORIZED = '未分类'
/** 数量的未知占位（防御性）：后端只给有明确数量的行，真拿到空值时显示它，不显示 0。 */
const UNKNOWN_COUNT = '—'

function groupByCategory(items: DepotItem[]): [string, DepotItem[]][] {
  const groups = new Map<string, DepotItem[]>()
  for (const item of items) {
    const category = depotCategoryLabel(item.category)
    const bucket = groups.get(category)
    if (bucket) bucket.push(item)
    else groups.set(category, [item])
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right, 'zh-Hans-CN'))
}

/**
 * 仓库的一件道具卡片：紧凑的图标 + 物品名 + 数量展示。
 *
 * 在手机端 2 列、平板 3 列、桌面端 4 列网格中排布，避免长单列占用过多纵向空间；
 * 分类与编号保留在 DOM 中供检索与断言，不喧宾夺主。
 */
function DepotRow({ item }: { item: DepotItem }) {
  const approximate = isApproximateCountItem(item.item_id)
  const hint = approximateCountHint(item.item_id)
  const unknown = item.count == null
  const categoryText = depotCategoryLabel(item.category)

  return (
    <div
      data-testid={`depot-${item.item_id}`}
      className="flex min-w-0 items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-2/40"
    >
      <span className="relative size-9 shrink-0 overflow-hidden rounded-lg border bg-surface-2/70 p-0.5">
        <AssetImage
          path={itemIconPath(item.item_id)}
          testId={`depot-icon-${item.item_id}`}
          className="size-full object-contain"
          placeholderIcon={Package}
          placeholderClassName="size-4"
        />
      </span>
      <div className="min-w-0 flex-1">
        <span className="block truncate text-[0.875rem] font-medium leading-5 text-foreground" title={item.name}>
          {item.name}
        </span>
        <span
          className={cn(
            'inline-flex items-center text-xs font-semibold leading-4',
            unknown
              ? 'text-muted-foreground'
              : approximate
                ? 'text-warning'
                : 'text-primary-text',
          )}
          data-testid={`depot-count-${item.item_id}`}
        >
          <span
            className="tabular-nums"
            title={unknown ? '这行没有数量（不是 0）' : hint ?? undefined}
          >
            {unknown ? UNKNOWN_COUNT : `${approximate ? '约 ' : ''}× ${item.count}`}
          </span>
        </span>
        <span className="sr-only">
          <span>{categoryText || UNCATEGORIZED}</span>
          <span data-testid={`depot-id-${item.item_id}`}>{item.item_id}</span>
        </span>
      </div>
    </div>
  )
}

/**
 * 仓库页（规格 §7.5、M44 §4.4）：按分类分组的只读网格列表，展示图标、名字、分类与数量，
 * 另显示识别时间与是否过期。不做刷新识别按钮、不做导入导出（§7.10、Q8、Q37）。
 */
export function DepotTab() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [category, setCategory] = useState('')
  const [text, setText] = useState('')
  const query = useDebouncedValue(text, 300)

  const summary = useQuery({
    queryKey: accountKeys.summary(),
    queryFn: ({ signal }) => fetchAccountSummary(signal),
    retry: false,
  })

  const filters = useMemo(() => ({ category, query }), [category, query])
  const list = useInfiniteQuery({
    queryKey: accountKeys.depot(filters),
    queryFn: ({ pageParam, signal }) => fetchDepot(
      {
        limit: DEPOT_PAGE_SIZE,
        offset: pageParam,
        ...(filters.category ? { category: filters.category } : {}),
        ...(filters.query.trim() ? { query: filters.query.trim() } : {}),
      },
      signal,
    ),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.items.length, 0)
      return lastPage.items.length > 0 && loaded < lastPage.total ? loaded : undefined
    },
    retry: false,
  })

  const items = (list.data?.pages ?? []).flatMap((page) => page.items ?? [])
  const total = list.data?.pages.at(-1)?.total ?? items.length
  const categories = list.data?.pages.at(-1)?.categories ?? []
  const groups = groupByCategory(items)
  const hasActiveFilter = Boolean(category || text.trim())
  const searching = query !== text || list.isFetching

  const clearFilters = () => {
    setCategory('')
    setText('')
  }

  return (
    <div className="space-y-4">
      {summary.data ? (
        <RecognitionStatus
          label="仓库识别"
          entry={summary.data.depot}
          unit="种物品"
          description="只读列表，由任务里的「仓库识别」写入。"
          staleHint="数据已超过 {days} 天，需要时请在任务里运行「仓库识别」重新识别。"
        />
      ) : summary.error ? (
        <InlineAlert tone="warning">读取账号概要失败：{errorText(summary.error)}</InlineAlert>
      ) : (
        <LoadingState label="正在读取账号概要…" />
      )}

      {/* 紧凑单行搜索与分类筛选工具条 */}
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <label className="sr-only" htmlFor="depot-search-input">搜索物品</label>
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="depot-search-input"
            ref={inputRef}
            type="search"
            className={cn('h-10 pl-9 [&::-webkit-search-cancel-button]:appearance-none', text && 'pr-10')}
            placeholder="搜索物品名或 id"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="search"
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') event.currentTarget.blur()
            }}
          />
          {text ? (
            <button
              type="button"
              aria-label="清除搜索词"
              onClick={() => {
                setText('')
                inputRef.current?.focus()
              }}
              className="absolute inset-y-0 right-0 grid w-10 place-items-center rounded-r-lg text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <X aria-hidden="true" className="size-4" />
            </button>
          ) : null}
        </div>
        <label className="w-32 shrink-0 sm:w-40">
          <span className="sr-only">分类</span>
          <NativeSelect
            aria-label="分类"
            className="h-10 text-sm"
            value={category}
            onChange={(event) => setCategory(event.currentTarget.value)}
          >
            <option value="">全部分类</option>
            {categories.map((name) => (
              <option key={name} value={name}>{depotCategoryLabel(name)}</option>
            ))}
          </NativeSelect>
        </label>
        {hasActiveFilter ? (
          <Button variant="ghost" size="sm" className="h-10 min-h-10 shrink-0 px-2.5 text-primary-text" onClick={clearFilters}>
            <RotateCcw aria-hidden="true" className="size-3.5!" />
            清除
          </Button>
        ) : null}
      </div>

      {list.isPending ? (
        <LoadingState label="正在读取仓库…" />
      ) : list.error && items.length === 0 ? (
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState
            title="仓库读取失败"
            message={errorText(list.error)}
            onRetry={() => void list.refetch()}
            retrying={list.isFetching}
          />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-xl border bg-card shadow-card">
          <EmptyState
            icon={Package}
            title="仓库里没有符合条件的物品"
            description="识别还没跑过，或者筛选条件太窄"
          />
        </div>
      ) : (
        <div className="space-y-4">
          {list.error ? <InlineAlert tone="warning">仓库刷新失败：{errorText(list.error)}</InlineAlert> : null}
          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <p className="text-footnote tabular-nums text-muted-foreground">
              共 {total} 种物品，按分类展示
            </p>
            {searching ? <Badge variant="outline" role="status">正在更新…</Badge> : null}
          </div>

          <div className="space-y-4">
            {groups.map(([name, group]) => (
              <section key={name} className="space-y-1.5">
                <h2 className="px-1 text-footnote font-medium text-muted-foreground">{`${name}（${group.length}）`}</h2>
                <div className="grid grid-cols-2 divide-x divide-y divide-border overflow-hidden rounded-xl border bg-card shadow-card sm:grid-cols-3 lg:grid-cols-4">
                  {group.map((item) => (
                    <DepotRow key={item.item_id} item={item} />
                  ))}
                </div>
              </section>
            ))}
          </div>

          {list.hasNextPage ? (
            <Button variant="outline" className="w-full" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
              {list.isFetchingNextPage ? '正在加载…' : '加载更多'}
            </Button>
          ) : (
            <p className="py-1 text-center text-footnote text-muted-foreground">已显示全部 {items.length} 种</p>
          )}
        </div>
      )}
    </div>
  )
}
