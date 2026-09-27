import { Fragment, useMemo, useRef, type ReactNode } from 'react'
import { BookOpen, CalendarClock, ChevronDown, ChevronRight, Heart, History, ListTree, RefreshCw, Search, SearchX, Trash2, X } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { EmptyState, LoadingState } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import type { ApiSnippet, ConsoleHistoryEntry, TreeTab } from './console-model'
import { HISTORY_MAX_ENTRIES, relativeTimeLabel, responseTone, searchOperations, type ConsoleOperation, type ConsoleOperationGroup } from './utils'

const methodTone: Record<string, NonNullable<BadgeProps['variant']>> = {
  GET: 'primary',
  POST: 'success',
  PUT: 'warning',
  PATCH: 'warning',
  DELETE: 'danger',
}

export function MethodBadge({ method, className }: { method: string; className?: string }) {
  return (
    <Badge variant={methodTone[method] ?? 'neutral'} className={cn('w-16 justify-center rounded-md px-0 font-mono font-semibold', className)}>
      {method}
    </Badge>
  )
}

/** 每个「/」后留一个换行点：窄栏里的长路径按段折行，而不是从单词中间断开。 */
export function PathText({ path }: { path: string }) {
  return <>{path.split('/').map((segment, index) => <Fragment key={index}>{index > 0 ? <>/<wbr /></> : null}{segment}</Fragment>)}</>
}

const statusTone = {
  success: 'text-success',
  neutral: 'text-muted-foreground',
  warning: 'text-warning',
  danger: 'text-danger',
} as const

/** 放在分组卡片里（手机）或直接铺在栏里（桌面三栏）。 */
function listClassName(inset: boolean) {
  return inset ? 'divide-y border-b' : 'divide-y overflow-hidden rounded-xl border bg-card shadow-card'
}

function ListHeader({ children, action, inset }: { children: ReactNode; action?: ReactNode; inset: boolean }) {
  return (
    <div className={cn('flex min-h-11 items-center justify-between gap-2', inset ? 'border-b pl-4 pr-2' : 'px-1')}>
      <p className="min-w-0 truncate text-footnote text-muted-foreground tabular-nums">{children}</p>
      {action}
    </div>
  )
}

function RefreshButton({ label, refreshing, onRefresh }: { label: string; refreshing: boolean; onRefresh: () => void }) {
  return (
    <Button variant="ghost" size="icon" aria-label={label} disabled={refreshing} onClick={onRefresh} className="-mr-2 shrink-0">
      <RefreshCw aria-hidden="true" className={cn(refreshing && 'animate-spin motion-reduce:animate-none')} />
    </Button>
  )
}

function SearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div className="relative">
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-[1.125rem] -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        type="search"
        aria-label="搜索接口"
        placeholder="搜索路径、摘要或 operationId"
        enterKeyHint="search"
        autoCapitalize="off"
        autoCorrect="off"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
        }}
        className={cn('pl-10 [&::-webkit-search-cancel-button]:appearance-none', value && 'pr-11')}
      />
      {value ? (
        <button
          type="button"
          aria-label="清除搜索"
          onClick={() => {
            onChange('')
            inputRef.current?.focus()
          }}
          className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      ) : null}
    </div>
  )
}

const TREE_TABS = [
  { value: 'interfaces', label: '接口' },
  { value: 'favorites', label: '收藏' },
  { value: 'history', label: '历史' },
] as const

/** 接口列表区顶部：接口 / 收藏 / 历史切换，接口页签下带搜索框。手机上随标题栏吸顶。 */
export function TreeToolbar({ tab, onTabChange, search, onSearchChange }: {
  tab: TreeTab
  onTabChange: (tab: TreeTab) => void
  search: string
  onSearchChange: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <SegmentedControl ariaLabel="请求资源" value={tab} onValueChange={onTabChange} options={TREE_TABS} fullWidth />
      {tab === 'interfaces' ? <SearchField value={search} onChange={onSearchChange} /> : null}
    </div>
  )
}

function OperationRow({ operation, active, showChevron, onSelect }: {
  operation: ConsoleOperation
  active: boolean
  showChevron: boolean
  onSelect: (operation: ConsoleOperation) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(operation)}
      aria-pressed={active}
      aria-label={`${operation.method} ${operation.path} ${operation.summary}`.trim()}
      className={cn(
        'flex min-h-14 w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
        active && 'bg-primary-soft hover:bg-primary-soft',
      )}
    >
      <MethodBadge method={operation.method} className="mt-0.5" />
      <span className="min-w-0 flex-1">
        <code className={cn('block font-mono text-sm font-medium leading-6 [overflow-wrap:anywhere]', active && 'text-primary-text')}><PathText path={operation.path} /></code>
        {operation.summary ? <span className="mt-0.5 line-clamp-2 block text-footnote text-muted-foreground">{operation.summary}</span> : null}
      </span>
      {showChevron ? <ChevronRight aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground/70" /> : null}
    </button>
  )
}

export interface InterfaceListProps {
  groups: ConsoleOperationGroup[]
  search: string
  onClearSearch: () => void
  selected: ConsoleOperation | null
  onSelect: (operation: ConsoleOperation) => void
  loading: boolean
  error: unknown
  refreshing: boolean
  onRefresh: () => void
  /** 桌面三栏：直接铺在栏里、分组标题吸顶；手机：每组一张卡片，行尾带箭头。 */
  inset: boolean
}

export function InterfaceList({ groups, search, onClearSearch, selected, onSelect, loading, error, refreshing, onRefresh, inset }: InterfaceListProps) {
  const matches = useMemo(() => new Set(searchOperations(groups, search)), [groups, search])
  const filtering = search.trim() !== ''
  let content: ReactNode
  if (error) {
    content = (
      <InlineAlert tone="danger" className={cn(inset && 'mx-3')} action={<Button variant="outline" size="sm" onClick={onRefresh}>重试</Button>}>
        {error instanceof Error ? error.message : '读取 OpenAPI 失败'}
      </InlineAlert>
    )
  } else if (loading) {
    content = <LoadingState label="正在读取接口目录…" />
  } else if (matches.size === 0) {
    content = filtering
      ? <EmptyState icon={SearchX} title="没有匹配的接口" description="按路径、摘要或 operationId 搜索。" action={<Button variant="outline" size="sm" onClick={onClearSearch}>清除搜索</Button>} />
      : <EmptyState icon={ListTree} title="接口目录为空" description="/openapi.json 里没有可调用的接口。" />
  } else {
    content = groups.map((group) => {
      const operations = group.operations.filter((operation) => matches.has(operation))
      if (!operations.length) return null
      return (
        // 搜索时整组重新挂载为展开状态，避免匹配项藏在被手动收起的分组里。
        <details key={`${group.name}:${filtering ? 'filtered' : 'all'}`} open className={cn('group/tag', !inset && 'space-y-1.5')}>
          <summary
            title={group.description || group.name}
            className={cn(
              'flex min-h-10 list-none items-center gap-2 text-footnote font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden',
              inset ? 'sticky top-0 z-10 border-b bg-card/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-card/80' : 'rounded-lg px-1',
            )}
          >
            <span className="min-w-0 flex-1 truncate">{group.name}</span>
            <span className="tabular-nums">{operations.length}</span>
            <ChevronDown aria-hidden="true" className="size-4 shrink-0 transition-transform duration-200 group-open/tag:rotate-180" />
          </summary>
          <ul className={listClassName(inset)}>
            {operations.map((operation) => (
              <li key={`${operation.method}:${operation.path}`}>
                <OperationRow
                  operation={operation}
                  active={selected?.path === operation.path && selected.method === operation.method}
                  showChevron={!inset}
                  onSelect={onSelect}
                />
              </li>
            ))}
          </ul>
        </details>
      )
    })
  }
  return (
    <div className={cn(inset ? '' : 'space-y-3')}>
      <ListHeader inset={inset} action={<RefreshButton label="刷新接口目录" refreshing={refreshing} onRefresh={onRefresh} />}>
        {loading ? '正在读取 /openapi.json' : `${matches.size} 个接口 · 来自 /openapi.json`}
      </ListHeader>
      <div className={cn(inset && (error || loading || matches.size === 0) && 'py-3')}>{content}</div>
    </div>
  )
}

export interface FavoriteListProps {
  favorites: ApiSnippet[]
  loading: boolean
  error: unknown
  refreshing: boolean
  onRefresh: () => void
  onOpen: (favorite: ApiSnippet) => void
  onSchedule: (favorite: ApiSnippet) => void
  onDelete: (favorite: ApiSnippet) => void
  inset: boolean
}

export function FavoriteList({ favorites, loading, error, refreshing, onRefresh, onOpen, onSchedule, onDelete, inset }: FavoriteListProps) {
  return (
    <section aria-label="已命名收藏" className={cn(!inset && 'space-y-3')}>
      <ListHeader inset={inset} action={<RefreshButton label="刷新收藏" refreshing={refreshing} onRefresh={onRefresh} />}>
        {favorites.length} 条收藏 · 保存在当前服务
      </ListHeader>
      {error ? (
        <InlineAlert tone="danger" className={cn(inset && 'm-3')} action={<Button variant="outline" size="sm" onClick={onRefresh}>重试</Button>}>
          收藏读取失败：{toApiError(error).message}
        </InlineAlert>
      ) : null}
      {loading ? <LoadingState label="正在读取收藏…" /> : favorites.length === 0 ? (
        error ? null : <EmptyState icon={Heart} title="还没有收藏" description="在请求编辑器里命名并保存；收藏同步到当前服务，不含鉴权凭据。" />
      ) : (
        <ul className={listClassName(inset)}>
          {favorites.map((favorite) => (
            <li key={favorite.id} className="flex items-center">
              <button
                type="button"
                onClick={() => onOpen(favorite)}
                className="flex min-h-14 min-w-0 flex-1 flex-col justify-center gap-1 py-3 pl-4 pr-2 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              >
                <span className="block truncate text-[0.9375rem] font-medium">{favorite.name}</span>
                <span className="flex min-w-0 items-center gap-2">
                  <MethodBadge method={favorite.method} />
                  <code className="min-w-0 truncate font-mono text-footnote text-muted-foreground">{favorite.path}</code>
                </span>
              </button>
              <ActionMenu
                label={`收藏 ${favorite.name} 的更多操作`}
                title={favorite.name}
                description={`${favorite.method} ${favorite.path}`}
                className="mr-1.5"
                actions={[
                  ...(favorite.path === '/api/pipelines'
                    ? [{ label: '转为定时任务', description: '把请求体里的任务带进新建定时任务', icon: CalendarClock, onSelect: () => onSchedule(favorite) }]
                    : []),
                  { label: '删除收藏', icon: Trash2, tone: 'danger' as const, onSelect: () => onDelete(favorite) },
                ]}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function HistoryList({ entries, onRestore, inset }: { entries: ConsoleHistoryEntry[]; onRestore: (entry: ConsoleHistoryEntry) => void; inset: boolean }) {
  return (
    <section aria-label="本机历史" className={cn(!inset && 'space-y-3')}>
      <ListHeader inset={inset}>本机历史 · {entries.length}/{HISTORY_MAX_ENTRIES} · 不含 token</ListHeader>
      {entries.length === 0 ? (
        <EmptyState icon={History} title="还没有请求记录" description={`发送过的请求只保存在本机，最多 ${HISTORY_MAX_ENTRIES} 条，不含 token 等凭据。`} />
      ) : (
        <ul aria-label="本机请求历史" className={listClassName(inset)}>
          {entries.map((entry) => {
            const tone = responseTone(entry.status ?? 0)
            return (
              <li key={entry.id}>
                <button
                  type="button"
                  onClick={() => onRestore(entry)}
                  className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <MethodBadge method={entry.method} />
                  <span className="min-w-0 flex-1">
                    <code className="block truncate font-mono text-sm">{entry.path}</code>
                    <span className="mt-0.5 block text-footnote text-muted-foreground tabular-nums">
                      {relativeTimeLabel(entry.timestamp)} · {Number(entry.elapsed_ms ?? 0).toFixed(0)} ms{entry.truncated ? ' · 已截断' : ''}
                    </span>
                  </span>
                  <span className={cn('shrink-0 font-mono text-sm font-semibold tabular-nums', statusTone[tone])}>{entry.status ? entry.status : '失败'}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

/** 原生 `/docs`、`/redoc` 用来交叉验证契约。 */
export function DocsLinks({ className }: { className?: string }) {
  const linkClass = 'inline-flex min-h-11 items-center gap-1.5 font-medium text-primary-text underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-md'
  return (
    <p className={cn('flex flex-wrap items-center gap-x-3 text-footnote text-muted-foreground', className)}>
      <span>原生文档</span>
      <a href="/docs" className={linkClass}><BookOpen aria-hidden="true" className="size-3.5" />OpenAPI 文档</a>
      <span aria-hidden="true">·</span>
      <a href="/redoc" className={linkClass}>ReDoc</a>
    </p>
  )
}
