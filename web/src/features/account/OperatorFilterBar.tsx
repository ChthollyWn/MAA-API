import { useRef } from 'react'
import { RotateCcw, Search, SlidersHorizontal, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RemovableChip } from '@/components/ui/chip'
import { InfoHint } from '@/components/ui/info-hint'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { cn } from '@/lib/utils'
import { PROFESSIONS, eliteLabel } from './format'
import { activeFilterCount, DEFAULT_OPERATOR_FILTERS, type OperatorFilters, type OwnedFilter } from './filters'

const OWNED_OPTIONS: readonly { value: OwnedFilter; label: string }[] = [
  { value: 'owned', label: '已拥有' },
  { value: 'all', label: '全部' },
  { value: 'unowned', label: '未拥有' },
]

const RARITY_OPTIONS: readonly number[] = [1, 2, 3, 4, 5, 6]
const ELITE_OPTIONS: readonly number[] = [0, 1, 2]

export interface OperatorFilterBarProps {
  filters: OperatorFilters
  onFiltersChange: (filters: OperatorFilters) => void
  /** 「清除」按钮；回到默认（只看已拥有）。搜索框的防抖在列表页里做。 */
  onClear: () => void
  /** 请求在途：搜索/筛选刚改动时给个提示。 */
  busy?: boolean
}

/** 干员筛选：搜索 + 持有 / 最低稀有度 / 职业 / 最低精英化（规格 §7.5）。 */
export function OperatorFilterBar({ filters, onFiltersChange, onClear, busy = false }: OperatorFilterBarProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const count = activeFilterCount(filters)
  const set = <K extends keyof OperatorFilters>(key: K, value: OperatorFilters[K]) =>
    onFiltersChange({ ...filters, [key]: value })

  const activeChips: { id: string; label: string; onRemove: () => void }[] = []
  if (filters.owned !== DEFAULT_OPERATOR_FILTERS.owned) {
    const ownedLabel = OWNED_OPTIONS.find((item) => item.value === filters.owned)?.label ?? filters.owned
    activeChips.push({
      id: 'owned',
      label: `持有：${ownedLabel}`,
      onRemove: () => set('owned', DEFAULT_OPERATOR_FILTERS.owned),
    })
  }
  if (filters.rarityMin !== null) {
    activeChips.push({
      id: 'rarityMin',
      label: `最低 ${filters.rarityMin}★`,
      onRemove: () => set('rarityMin', null),
    })
  }
  if (filters.profession) {
    activeChips.push({
      id: 'profession',
      label: `职业：${filters.profession}`,
      onRemove: () => set('profession', ''),
    })
  }
  if (filters.eliteMin !== null) {
    activeChips.push({
      id: 'eliteMin',
      label: `最低 ${eliteLabel(filters.eliteMin)}`,
      onRemove: () => set('eliteMin', null),
    })
  }

  return (
    <div className="space-y-2.5 rounded-xl border bg-card p-3 shadow-card">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <label className="sr-only" htmlFor="operator-search-input">搜索干员</label>
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="operator-search-input"
            ref={inputRef}
            type="search"
            className={cn('h-10 pl-9 [&::-webkit-search-cancel-button]:appearance-none', filters.query && 'pr-10')}
            placeholder="搜索干员名"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="search"
            value={filters.query}
            onChange={(event) => set('query', event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') event.currentTarget.blur()
            }}
          />
          {filters.query ? (
            <button
              type="button"
              aria-label="清除搜索词"
              onClick={() => {
                set('query', '')
                inputRef.current?.focus()
              }}
              className="absolute inset-y-0 right-0 grid w-10 place-items-center rounded-r-lg text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <X aria-hidden="true" className="size-4" />
            </button>
          ) : null}
        </div>
        {count > 0 ? (
          <Button variant="ghost" size="sm" className="h-9 min-h-9 shrink-0 px-2.5 text-primary-text" onClick={onClear}>
            <RotateCcw aria-hidden="true" className="size-3.5!" />
            清除
          </Button>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <label className="flex items-center gap-1.5 rounded-lg border bg-surface px-2.5 py-1 text-xs text-muted-foreground focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
          <span className="shrink-0 font-medium">持有</span>
          <NativeSelect
            aria-label="持有"
            wrapperClassName="flex-1"
            className="h-8 border-0 bg-transparent pl-1 pr-6 text-right text-xs font-medium text-foreground focus-visible:ring-0"
            value={filters.owned}
            onChange={(event) => set('owned', event.currentTarget.value as OwnedFilter)}
          >
            {OWNED_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </NativeSelect>
        </label>
        <label className="flex items-center gap-1.5 rounded-lg border bg-surface px-2.5 py-1 text-xs text-muted-foreground focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
          <span className="shrink-0 font-medium">最低稀有度</span>
          <NativeSelect
            aria-label="最低稀有度"
            wrapperClassName="flex-1"
            className="h-8 border-0 bg-transparent pl-1 pr-6 text-right text-xs font-medium text-foreground focus-visible:ring-0"
            value={filters.rarityMin === null ? '' : String(filters.rarityMin)}
            onChange={(event) => set('rarityMin', event.currentTarget.value ? Number(event.currentTarget.value) : null)}
          >
            <option value="">不限</option>
            {RARITY_OPTIONS.map((rarity) => <option key={rarity} value={rarity}>{rarity}★</option>)}
          </NativeSelect>
        </label>
        <label className="flex items-center gap-1.5 rounded-lg border bg-surface px-2.5 py-1 text-xs text-muted-foreground focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
          <span className="shrink-0 font-medium">职业</span>
          <NativeSelect
            aria-label="职业"
            wrapperClassName="flex-1"
            className="h-8 border-0 bg-transparent pl-1 pr-6 text-right text-xs font-medium text-foreground focus-visible:ring-0"
            value={filters.profession}
            onChange={(event) => set('profession', event.currentTarget.value)}
          >
            <option value="">不限</option>
            {PROFESSIONS.map((profession) => <option key={profession} value={profession}>{profession}</option>)}
          </NativeSelect>
        </label>
        <label className="flex items-center gap-1.5 rounded-lg border bg-surface px-2.5 py-1 text-xs text-muted-foreground focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
          <span className="shrink-0 font-medium">最低精英化</span>
          <NativeSelect
            aria-label="最低精英化"
            wrapperClassName="flex-1"
            className="h-8 border-0 bg-transparent pl-1 pr-6 text-right text-xs font-medium text-foreground focus-visible:ring-0"
            value={filters.eliteMin === null ? '' : String(filters.eliteMin)}
            onChange={(event) => set('eliteMin', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
          >
            <option value="">不限</option>
            {ELITE_OPTIONS.map((elite) => <option key={elite} value={elite}>{eliteLabel(elite)}</option>)}
          </NativeSelect>
        </label>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-footnote text-muted-foreground">
          <SlidersHorizontal aria-hidden="true" className="size-3.5 shrink-0" />
          <span>{count > 0 ? `已应用 ${count} 项筛选` : '默认只显示已拥有的干员'}</span>
          <InfoHint label="筛选规则说明">
            稀有度与精英化按「最低」筛选；精英化未识别的干员不参与「最低精英化」筛选；未拥有只能在已取回的列表里过滤。
          </InfoHint>
          {busy ? <Badge variant="outline" role="status">正在筛选…</Badge> : null}
        </div>
        {activeChips.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {activeChips.map((chip) => (
              <RemovableChip key={chip.id} removeLabel={`移除筛选：${chip.label}`} onRemove={chip.onRemove}>
                {chip.label}
              </RemovableChip>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
