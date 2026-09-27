import { useRef } from 'react'
import { Search, SlidersHorizontal, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { RemovableChip } from '@/components/ui/chip'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { LOG_SOURCES, type FilterCondition, type LogSourceFilter } from '@/features/logs/filters'
import { cn } from '@/lib/utils'

function LogSearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div className="relative min-w-0 flex-1">
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-[1.125rem] -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        type="search"
        aria-label="搜索日志"
        placeholder="搜索日志内容"
        enterKeyHint="search"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        onKeyDown={(event) => {
          // 手机上按「搜索」键收起键盘，结果已经随输入更新。
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

export interface LogToolbarProps {
  source: LogSourceFilter
  onSourceChange: (source: LogSourceFilter) => void
  keyword: string
  onKeywordChange: (keyword: string) => void
  conditions: readonly FilterCondition[]
  onOpenFilters: () => void
  onRemoveCondition: (id: string) => void
  onClearConditions: () => void
}

/** 日志页吸顶工具条：来源分段、搜索、筛选入口，以及生效条件的芯片。 */
export function LogToolbar({
  source,
  onSourceChange,
  keyword,
  onKeywordChange,
  conditions,
  onOpenFilters,
  onRemoveCondition,
  onClearConditions,
}: LogToolbarProps) {
  const count = conditions.length
  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <SegmentedControl
          ariaLabel="日志来源"
          value={source}
          onValueChange={onSourceChange}
          options={LOG_SOURCES}
          fullWidth
          className="sm:w-auto sm:shrink-0"
        />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <LogSearchField value={keyword} onChange={onKeywordChange} />
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
        </div>
      </div>
      {count ? (
        <div className="flex items-center gap-2">
          <ul
            aria-label="生效的筛选条件"
            className="-my-1 flex min-w-0 flex-1 gap-2 overflow-x-auto py-1 pr-4 scrollbar-none [mask-image:linear-gradient(to_right,black_calc(100%-1rem),transparent)] sm:flex-wrap sm:pr-0 sm:[mask-image:none]"
          >
            {conditions.map((condition) => (
              <li key={condition.id} className="shrink-0">
                <RemovableChip removeLabel={`移除筛选条件：${condition.label}`} onRemove={() => onRemoveCondition(condition.id)}>
                  {condition.label}
                </RemovableChip>
              </li>
            ))}
          </ul>
          <Button variant="ghost" size="sm" className="shrink-0 px-2.5 text-primary-text" onClick={onClearConditions}>
            清除全部
          </Button>
        </div>
      ) : null}
    </div>
  )
}
