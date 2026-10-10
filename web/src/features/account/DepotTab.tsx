import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Package } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { ListGroup, ListItem } from '@/components/ui/list'
import { NativeSelect } from '@/components/ui/native-select'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { accountKeys, fetchAccountSummary, fetchDepot, type DepotItem } from './api'
import { RecognitionStatus } from './RecognitionStatus'
import { errorText } from './format'
import { useDebouncedValue } from './use-debounced-value'

const DEPOT_PAGE_SIZE = 100
const UNCATEGORIZED = '未分类'

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
 * 仓库页（规格 §7.5）：按分类分组的只读列表，显示识别时间与是否过期。
 * 不做「刷新识别」按钮、不做导入导出（§7.10、Q8、Q37）。
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
    // 仓库响应不带 limit/offset，下一页的偏移由已取回的条数推出来。
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

  return (
    <div className="space-y-4 pb-4">
      {summary.data ? (
        <RecognitionStatus
          label="仓库识别"
          entry={summary.data.depot}
          unit="种物品"
          description="只读列表，由任务里的「仓库识别」写入；识别只覆盖养成材料页签，龙门币与作战记录不在其中。"
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
            description="识别还没跑过，或者筛选条件太窄。仓库识别只覆盖养成材料页签"
          />
        </div>
      ) : (
        <div className="space-y-4">
          {list.error ? <InlineAlert tone="warning">仓库刷新失败：{errorText(list.error)}</InlineAlert> : null}
          <p className="px-1 text-footnote tabular-nums text-muted-foreground">共 {total} 种物品，按分类展示</p>
          {groups.map(([name, group]) => (
            <ListGroup key={name} title={`${name}（${group.length}）`}>
              {group.map((item) => (
                <ListItem
                  key={item.item_id}
                  title={item.name}
                  description={<span className="font-mono text-xs">{item.item_id}</span>}
                  trailing={<span className="tabular-nums">{item.count}</span>}
                  chevron={false}
                />
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
