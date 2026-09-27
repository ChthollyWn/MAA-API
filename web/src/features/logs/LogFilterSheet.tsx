import { Button } from '@/components/ui/button'
import { Chip } from '@/components/ui/chip'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ResponsiveSheet } from '@/components/ui/sheet'
import {
  ANY_TIME,
  LOG_LEVELS,
  NO_FILTERS,
  TIME_PRESETS,
  filterConditions,
  isAnyTime,
  isInvertedRange,
  presetRange,
  type LogFilters,
} from '@/features/logs/filters'
import type { LogLevelFilter } from '@/features/logs/use-log-history'

const dateTimeInputClassName = 'text-left [&::-webkit-date-and-time-value]:text-left'

export interface LogFilterSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  filters: LogFilters
  onFiltersChange: (filters: LogFilters) => void
}

/** 筛选面板：级别多选与时间范围，改动立即生效，「完成」只负责收起。 */
export function LogFilterSheet({ open, onOpenChange, filters, onFiltersChange }: LogFilterSheetProps) {
  const { levels, range } = filters
  const inverted = isInvertedRange(range)
  const toggleLevel = (level: LogLevelFilter) => onFiltersChange({
    ...filters,
    levels: levels.includes(level) ? levels.filter((item) => item !== level) : [...levels, level],
  })

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="筛选日志"
      description="选择后立即生效"
      footer={(
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" disabled={filterConditions(filters).length === 0} onClick={() => onFiltersChange(NO_FILTERS)}>
            清除
          </Button>
          <Button onClick={() => onOpenChange(false)}>完成</Button>
        </div>
      )}
    >
      <div className="space-y-6 pt-2">
        <section aria-labelledby="log-filter-levels" className="space-y-2.5">
          <div>
            <h3 id="log-filter-levels" className="text-[0.9375rem] font-semibold">级别</h3>
            <p className="mt-0.5 text-footnote text-muted-foreground">可多选，不选表示全部级别。</p>
          </div>
          <div role="group" aria-labelledby="log-filter-levels" className="flex flex-wrap gap-2">
            <Chip selected={levels.length === 0} onClick={() => onFiltersChange({ ...filters, levels: [] })}>全部级别</Chip>
            {LOG_LEVELS.map((level) => (
              <Chip key={level} selected={levels.includes(level)} className="font-mono text-[0.8125rem]" onClick={() => toggleLevel(level)}>
                {level}
              </Chip>
            ))}
          </div>
        </section>

        <section aria-labelledby="log-filter-time" className="space-y-3">
          <h3 id="log-filter-time" className="text-[0.9375rem] font-semibold">时间范围</h3>
          <div role="group" aria-label="快捷时间范围" className="flex flex-wrap gap-2">
            <Chip selected={isAnyTime(range)} onClick={() => onFiltersChange({ ...filters, range: ANY_TIME })}>不限时间</Chip>
            {TIME_PRESETS.map((preset) => (
              <Chip
                key={preset.value}
                selected={range.preset === preset.value}
                onClick={() => onFiltersChange({
                  ...filters,
                  range: range.preset === preset.value ? ANY_TIME : presetRange(preset.value),
                })}
              >
                {preset.label}
              </Chip>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="开始时间" htmlFor="log-since">
              <Input
                id="log-since"
                type="datetime-local"
                className={dateTimeInputClassName}
                value={range.since}
                onChange={(event) => onFiltersChange({ ...filters, range: { ...range, preset: null, since: event.currentTarget.value } })}
              />
            </Field>
            <Field
              label="结束时间"
              htmlFor="log-until"
              errorId="log-until-error"
              error={inverted ? '结束时间早于开始时间，不会有匹配的日志。' : undefined}
            >
              <Input
                id="log-until"
                type="datetime-local"
                className={dateTimeInputClassName}
                aria-invalid={inverted || undefined}
                aria-describedby={inverted ? 'log-until-error' : undefined}
                value={range.until}
                onChange={(event) => onFiltersChange({ ...filters, range: { ...range, preset: null, until: event.currentTarget.value } })}
              />
            </Field>
          </div>
        </section>
      </div>
    </ResponsiveSheet>
  )
}
