import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Package } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { ListGroup } from '@/components/ui/list'
import { NativeSelect } from '@/components/ui/native-select'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { AssetImage } from './AssetImage'
import { accountKeys, fetchAccountSummary, fetchDepot, type DepotCoverage, type DepotItem } from './api'
import { itemIconPath } from './assets'
import { RecognitionStatus } from './RecognitionStatus'
import { approximateCountHint, errorText, isApproximateCountItem } from './format'
import { useDebouncedValue } from './use-debounced-value'

const DEPOT_PAGE_SIZE = 100
const UNCATEGORIZED = '未分类'
/** 数量的未知占位（M45 W3a）：识别到身份但没读到数量时显示它，**不显示 0**。 */
const UNKNOWN_COUNT = '—'

/** 来源标签（M45 W3a）：这一行的身份是谁认出来的。未见过来源的旧数据不贴标签。 */
const SOURCE_LABELS: Record<string, string> = {
  kernel: '内核',
  self_scan: '自识别',
}

function groupByCategory(items: DepotItem[]): [string, DepotItem[]][] {
  const groups = new Map<string, DepotItem[]>()
  for (const item of items) {
    const category = item.category?.trim() || UNCATEGORIZED
    const bucket = groups.get(category)
    if (bucket) bucket.push(item)
    else groups.set(category, [item])
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right, 'zh-Hans-CN'))
}

/**
 * 仓库的一行：40px 图标（走本机代理，见 :mod:`./assets` 的 `itemIconPath`）+ 名字 +
 * 分类 + 来源 + `× 数量`。
 *
 * 图标拿不到（上游没有这张图、索引里没登记这个 id）时留中性占位，绝不留破图；
 * 数量前带「约」的是顶部资源栏那三件（识别值只到千位，见 `format.ts`）。
 *
 * M45 W3a 起两件事与数量同样重要：
 * - `count === null`（自识别只认出身份、还没读数量）显示 `—`，**不许显示 0**；
 * - 每行贴来源标签（`内核` / `自识别`），旧数据没有来源就留空 —— 不猜成内核。
 *
 * `data-testid` 分四处：整行 `depot-<item_id>`、图标 `depot-icon-<item_id>`（取不到图时是
 * `depot-icon-<item_id>-placeholder`）、数量 `depot-count-<item_id>`、来源
 * `depot-source-<item_id>`——「有没有图」「数量标没标约」「数量是不是未识别」
 * 「来源是哪一路」各自断言得清楚。
 */
function DepotRow({ item }: { item: DepotItem }) {
  const approximate = isApproximateCountItem(item.item_id)
  const hint = approximateCountHint(item.item_id)
  const unknown = item.count == null
  const sourceLabel = item.source ? SOURCE_LABELS[item.source] ?? item.source : null
  return (
    <div
      data-testid={`depot-${item.item_id}`}
      className="flex min-h-14 w-full items-center gap-3 px-4 py-2.5 text-left"
    >
      <span className="relative size-10 shrink-0 overflow-hidden rounded-lg border bg-muted">
        <AssetImage
          path={itemIconPath(item.item_id)}
          testId={`depot-icon-${item.item_id}`}
          className="size-full object-contain"
          placeholderIcon={Package}
          placeholderClassName="size-5"
        />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[0.9375rem] font-medium leading-5" title={item.name}>
          {item.name}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-footnote text-muted-foreground">
          <span>{item.category?.trim() || UNCATEGORIZED}</span>
          <span className="font-mono text-xs" data-testid={`depot-id-${item.item_id}`}>
            {item.item_id}
          </span>
          {sourceLabel ? (
            <span
              className="rounded border px-1 text-[0.6875rem] leading-4"
              data-testid={`depot-source-${item.item_id}`}
              title={
                item.source === 'self_scan'
                  ? '这一件由本机自识别补上（不在内核的识别名单里）'
                  : '这一件由内核的仓库识别给出'
              }
            >
              {sourceLabel}
            </span>
          ) : null}
        </span>
      </span>
      <span
        className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground"
        data-testid={`depot-count-${item.item_id}`}
      >
        <span
          className="tabular-nums"
          title={unknown ? '识别到这件道具，但这一轮没读到数量（不是 0）' : hint ?? undefined}
        >
          {unknown ? UNKNOWN_COUNT : `${approximate ? '约 ' : ''}× ${item.count}`}
        </span>
      </span>
    </div>
  )
}

/**
 * 覆盖说明（M45 W3a）：这一轮的物品里内核给了几件、自识别补了几件、还有几件没识别到。
 *
 * 「没识别到」是一个必须说出口的事实：内核的 `DepotInfo` 只回 `{itemId: count}`，
 * 缺失与 0 库存无法区分，页面绝不能把它渲染成「有 0 件」。
 */
function DepotCoverageNote({ coverage }: { coverage: DepotCoverage }) {
  const scanned = coverage.scanned ?? {}
  const pages = typeof scanned.pages === 'number' ? scanned.pages : null
  return (
    <p className="px-1 text-footnote tabular-nums text-muted-foreground" data-testid="depot-coverage">
      共 {coverage.total} 种：内核 {coverage.kernel}、自识别 {coverage.self_scan}
      {pages ? `（扫了 ${pages} 屏）` : ''}
      {coverage.unrecognized > 0 ? `；${coverage.unrecognized} 种这一轮没识别到（不等于 0）` : ''}
    </p>
  )
}

/**
 * 仓库页（规格 §7.5、M44 §4.4）：按分类分组的只读列表，每行显示图标 + 名字 + 分类 + 数量，
 * 另显示识别时间与是否过期。不做「刷新识别」按钮、不做导入导出（§7.10、Q8、Q37）。
 */
export function DepotTab() {
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
    // 视图载荷只带 items / total / categories；翻页偏移由已取回的条数推出来。
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.items.length, 0)
      return lastPage.items.length > 0 && loaded < lastPage.total ? loaded : undefined
    },
    retry: false,
  })

  const items = (list.data?.pages ?? []).flatMap((page) => page.items ?? [])
  const total = list.data?.pages.at(-1)?.total ?? items.length
  const categories = list.data?.pages.at(-1)?.categories ?? []
  // 「整份快照」的三项事实（M45 W3a）：与筛选、分页无关，所以从最后一页取一次。
  const coverage = list.data?.pages.at(-1)?.coverage ?? null
  const unrecognized = list.data?.pages.at(-1)?.unrecognized ?? []
  const unrecognizedTotal = list.data?.pages.at(-1)?.unrecognizedTotal ?? unrecognized.length
  const groups = groupByCategory(items)

  return (
    <div className="space-y-4 pb-4">
      {summary.data ? (
        <RecognitionStatus
          label="仓库识别"
          entry={summary.data.depot}
          unit="种物品"
          description="只读列表，由任务里的「仓库识别」写入；识别走仓库的「全部」页签，整仓道具一起入库。"
        />
      ) : summary.error ? (
        <InlineAlert tone="warning">读取账号概要失败：{errorText(summary.error)}</InlineAlert>
      ) : (
        <LoadingState label="正在读取账号概要…" />
      )}

      <div className="grid gap-3 rounded-xl border bg-card p-3 shadow-card sm:grid-cols-2">
        <label className="relative min-w-0">
          <span className="sr-only">搜索物品</span>
          <Input
            type="search"
            placeholder="搜索物品名或 id"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="search"
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
          />
        </label>
        <label className="min-w-0">
          <span className="sr-only">分类</span>
          <NativeSelect aria-label="分类" value={category} onChange={(event) => setCategory(event.currentTarget.value)}>
            <option value="">全部分类</option>
            {categories.map((name) => <option key={name} value={name}>{name}</option>)}
          </NativeSelect>
        </label>
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
            description="识别还没跑过，或者筛选条件太窄。仓库识别走「全部」页签"
          />
        </div>
      ) : (
        <div className="space-y-4">
          {list.error ? <InlineAlert tone="warning">仓库刷新失败：{errorText(list.error)}</InlineAlert> : null}
          <p className="px-1 text-footnote tabular-nums text-muted-foreground">共 {total} 种物品，按分类展示</p>
          {coverage ? <DepotCoverageNote coverage={coverage} /> : null}
          {unrecognized.length > 0 ? (
            <p className="px-1 text-footnote text-muted-foreground" data-testid="depot-unrecognized">
              这一轮没识别到 {unrecognizedTotal} 种：
              {unrecognized.slice(0, 8).map((entry) => entry.name || entry.item_id).join('、')}
              {unrecognizedTotal > 8 ? ' 等' : ''}（没识别到不等于仓库里没有）
            </p>
          ) : null}
          {groups.map(([name, group]) => (
            <ListGroup key={name} title={`${name}（${group.length}）`}>
              {group.map((item) => (
                <DepotRow key={item.item_id} item={item} />
              ))}
            </ListGroup>
          ))}
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
