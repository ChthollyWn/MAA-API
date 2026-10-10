import { Search, SlidersHorizontal } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { PROFESSIONS, eliteLabel } from './format'
import { activeFilterCount, type OperatorFilters, type OwnedFilter } from './filters'

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
  const count = activeFilterCount(filters)
  const set = <K extends keyof OperatorFilters>(key: K, value: OperatorFilters[K]) =>
    onFiltersChange({ ...filters, [key]: value })

  return (
    <div className="space-y-3 rounded-xl border bg-card p-3 shadow-card">
      <div className="flex items-center gap-2">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">搜索干员</span>
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            className="pl-9"
            placeholder="搜索干员名"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="search"
            value={filters.query}
            onChange={(event) => set('query', event.currentTarget.value)}
          />
        </label>
        {count > 0 ? (
          <Button variant="ghost" size="sm" className="shrink-0" onClick={onClear}>
            清除
          </Button>
        ) : null}
      </div>
      <p className="flex flex-wrap items-center gap-2 text-footnote text-muted-foreground">
        <SlidersHorizontal aria-hidden="true" className="size-3.5" />
        {count > 0 ? `已应用 ${count} 项筛选` : '默认只显示已拥有的干员'}
        {busy ? <Badge variant="outline" role="status">正在筛选…</Badge> : null}
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label className="space-y-1 text-footnote text-muted-foreground">
          <span className="block">持有</span>
          <NativeSelect
            aria-label="持有"
            value={filters.owned}
            onChange={(event) => set('owned', event.currentTarget.value as OwnedFilter)}
          >
            {OWNED_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </NativeSelect>
        </label>
        <label className="space-y-1 text-footnote text-muted-foreground">
          <span className="block">最低稀有度</span>
          <NativeSelect
            aria-label="最低稀有度"
            value={filters.rarityMin === null ? '' : String(filters.rarityMin)}
            onChange={(event) => set('rarityMin', event.currentTarget.value ? Number(event.currentTarget.value) : null)}
          >
            <option value="">不限</option>
            {RARITY_OPTIONS.map((rarity) => <option key={rarity} value={rarity}>{rarity}★</option>)}
          </NativeSelect>
        </label>
        <label className="space-y-1 text-footnote text-muted-foreground">
          <span className="block">职业</span>
          <NativeSelect
            aria-label="职业"
            value={filters.profession}
            onChange={(event) => set('profession', event.currentTarget.value)}
          >
            <option value="">不限</option>
            {PROFESSIONS.map((profession) => <option key={profession} value={profession}>{profession}</option>)}
          </NativeSelect>
        </label>
        <label className="space-y-1 text-footnote text-muted-foreground">
          <span className="block">最低精英化</span>
          <NativeSelect
            aria-label="最低精英化"
            value={filters.eliteMin === null ? '' : String(filters.eliteMin)}
            onChange={(event) => set('eliteMin', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
          >
            <option value="">不限</option>
            {ELITE_OPTIONS.map((elite) => <option key={elite} value={elite}>{eliteLabel(elite)}</option>)}
          </NativeSelect>
        </label>
      </div>
      <p className="text-footnote text-muted-foreground">
        稀有度与精英化按「最低」筛选；精英化未识别的干员不参与「最低精英化」筛选；未拥有只能在已取回的列表里过滤。
      </p>
    </div>
  )
}
