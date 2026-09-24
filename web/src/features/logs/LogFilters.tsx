import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import type { LogLevelFilter } from '@/features/logs/use-log-history'

export type LogSourceFilter = 'all' | 'task' | 'service' | 'core'

interface LogFiltersProps {
  source: LogSourceFilter
  onSourceChange: (source: LogSourceFilter) => void
  levels: LogLevelFilter[]
  onLevelsChange: (levels: LogLevelFilter[]) => void
  keyword: string
  onKeywordChange: (keyword: string) => void
  since: string
  onSinceChange: (value: string) => void
  until: string
  onUntilChange: (value: string) => void
}

const sources: Array<{ value: LogSourceFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'task', label: '任务' },
  { value: 'service', label: '服务' },
  { value: 'core', label: '核心' },
]

const levels: LogLevelFilter[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

export function LogFilters({
  source,
  onSourceChange,
  levels: selectedLevels,
  onLevelsChange,
  keyword,
  onKeywordChange,
  since,
  onSinceChange,
  until,
  onUntilChange,
}: LogFiltersProps) {
  return (
    <section aria-label="日志过滤" className="space-y-3 border-b px-4 py-3 sm:px-6">
      <Tabs
        aria-label="日志来源"
        value={source}
        onValueChange={(value) => onSourceChange(value as LogSourceFilter)}
      >
        <TabsList className="grid min-h-12 w-full grid-cols-4">
          {sources.map((item) => (
            <TabsTrigger key={item.value} className="min-h-11 px-2" value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div>
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-muted-foreground">日志级别（可多选，未选择表示全部）</legend>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6" role="group" aria-label="日志级别">
            <button
              className={`min-h-11 rounded-lg border px-2 text-xs font-medium ${selectedLevels.length === 0 ? 'border-primary bg-primary text-primary-foreground' : 'bg-background text-muted-foreground'}`}
              type="button"
              aria-pressed={selectedLevels.length === 0}
              onClick={() => onLevelsChange([])}
            >
              全部级别
            </button>
            {levels.map((item) => {
              const selected = selectedLevels.includes(item)
              return (
                <button
                  key={item}
                  className={`min-h-11 rounded-lg border px-2 text-xs font-medium ${selected ? 'border-primary bg-primary text-primary-foreground' : 'bg-background text-muted-foreground'}`}
                  type="button"
                  aria-label={item}
                  aria-pressed={selected}
                  onClick={() => onLevelsChange(
                    selected
                      ? selectedLevels.filter((current) => current !== item)
                      : [...selectedLevels, item],
                  )}
                >
                  {item}
                </button>
              )
            })}
          </div>
        </fieldset>
      </div>

      <div className="grid grid-cols-1 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground" htmlFor="log-keyword">关键词</label>
          <Input
            id="log-keyword"
            type="search"
            placeholder="搜索日志内容"
            value={keyword}
            onChange={(event) => onKeywordChange(event.currentTarget.value)}
          />
        </div>
      </div>

      <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <legend className="mb-1 text-xs font-medium text-muted-foreground">时间范围</legend>
        <div>
          <label className="sr-only" htmlFor="log-since">开始时间</label>
          <Input id="log-since" aria-label="开始时间" type="datetime-local" value={since} onChange={(event) => onSinceChange(event.currentTarget.value)} />
        </div>
        <div>
          <label className="sr-only" htmlFor="log-until">结束时间</label>
          <Input id="log-until" aria-label="结束时间" type="datetime-local" value={until} onChange={(event) => onUntilChange(event.currentTarget.value)} />
        </div>
      </fieldset>
    </section>
  )
}
