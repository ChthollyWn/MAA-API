import { useMemo, useState } from 'react'
import { Coins, RefreshCw } from 'lucide-react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'
import { Page } from '@/components/layout/Page'
import { Section } from '@/components/ui/section'
import { Button } from '@/components/ui/button'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { SegmentedControl } from '@/components/ui/segmented-control'
import type { ChartCardData } from '@/features/agent/media'
import { EChartSurface } from '@/features/agent/chart/EChartSurface'
import { specsQueryOptions } from '@/features/runs/api'
import { agentTitle, formatMicros, formatTokens } from '@/features/runs/format'
import { settingsQueryOptions } from '@/features/settings/use-settings'
import {
  cacheHitRatio, costRange, formatRatio, roleLabel, todaySince, totalCalls,
  type CostGroupBy, type CostRange,
} from '@/features/costs/format'
import { cn } from '@/lib/utils'

type CostSummaryView = components['schemas']['CostSummaryView']
type CostGroupView = components['schemas']['CostGroupView']

const RANGE_OPTIONS = [
  { value: 'today', label: '今天' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
] as const satisfies readonly { value: CostRange; label: string }[]

const GROUP_OPTIONS = [
  { value: 'day', label: '按天' },
  { value: 'agent', label: '按代理' },
  { value: 'model', label: '按模型' },
  { value: 'role', label: '按角色' },
] as const satisfies readonly { value: CostGroupBy; label: string }[]

interface CostQuery {
  since: string
  until: string
  group_by: CostGroupBy
  currency?: string
}

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

async function fetchCosts(query: CostQuery): Promise<CostSummaryView> {
  const { data, error, response } = await api.GET('/api/agent/costs', { params: { query } })
  if (error) throw toApiError(error, response)
  return data as CostSummaryView
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 space-y-0.5 text-center">
      <p className="text-footnote text-muted-foreground">{label}</p>
      <p className="truncate text-[1.0625rem] font-semibold tabular-nums">{value}</p>
    </div>
  )
}

export function CostsPage() {
  const [range, setRange] = useState<CostRange>('today')
  const [groupBy, setGroupBy] = useState<CostGroupBy>('day')
  const bounds = useMemo(() => costRange(range), [range])
  const todayStart = useMemo(() => todaySince(), [])
  const filters = useMemo<CostQuery>(() => ({
    since: bounds.since,
    until: bounds.until,
    group_by: groupBy,
  }), [bounds, groupBy])

  const costs = useQuery({
    queryKey: keys.agent.costs(filters as unknown as Readonly<Record<string, unknown>>),
    queryFn: () => fetchCosts(filters),
    placeholderData: keepPreviousData,
    retry: false,
  })
  const today = useQuery({
    queryKey: keys.agent.costs({ since: todayStart, group_by: 'day', currency: 'CNY' }),
    queryFn: () => fetchCosts({ since: todayStart, until: new Date().toISOString(), group_by: 'day', currency: 'CNY' }),
    retry: false,
  })
  const settings = useQuery(settingsQueryOptions)
  const specs = useQuery(specsQueryOptions)
  const titles = useMemo(() => new Map((specs.data ?? []).map((spec) => [spec.name, spec.title])), [specs.data])

  const groups = costs.data?.groups ?? []
  const currency = costs.data?.currency ?? ''
  const ratio = cacheHitRatio(groups)
  const hitRate = formatRatio(ratio)

  const limitValue = settings.data?.values['budget.daily_cost_limit']?.value
  const limit = typeof limitValue === 'number' && Number.isFinite(limitValue) ? Math.max(limitValue, 0) : null
  const warnValue = settings.data?.values['budget.warn_ratio']?.value
  const warnRatio = typeof warnValue === 'number' && warnValue > 0 && warnValue <= 1 ? warnValue : 1
  const todayMicros = today.data?.total_micros ?? 0
  const todayCost = todayMicros / 1_000_000
  const warning = limit !== null && limit > 0 && todayCost >= limit * warnRatio

  const groupLabel = (key: string): string => {
    if (groupBy === 'agent') return agentTitle(key, titles)
    if (groupBy === 'role') return roleLabel(key)
    return key
  }

  const chartSpec = useMemo<ChartCardData | null>(() => {
    if (groupBy !== 'day' || groups.length === 0) return null
    const ordered = [...groups].sort((left, right) => left.key.localeCompare(right.key))
    return {
      type: 'chart',
      caption: '每天模型费用',
      chartType: 'bar',
      title: '每天模型费用',
      categories: ordered.map((group) => group.key),
      series: [{ name: '费用', values: ordered.map((group) => Number((group.cost_micros / 1_000_000).toFixed(4))) }],
      stacked: false,
      xLabel: '日期',
      yLabel: '费用',
      unit: currency === 'CNY' ? '元' : currency,
      source: 'summarize_costs',
    }
  }, [currency, groupBy, groups])

  const refresh = () => {
    void costs.refetch()
    void today.refetch()
    void settings.refetch()
  }
  const refreshing = costs.isFetching || today.isFetching

  return (
    <Page
      title="成本"
      back="/more"
      actions={(
        <Button variant="ghost" size="icon" aria-label="刷新" disabled={refreshing} onClick={refresh}>
          <RefreshCw aria-hidden="true" className={cn(refreshing && 'animate-spin')} />
        </Button>
      )}
    >
      <div className="space-y-5 pb-4">
        <div className="space-y-2">
          <SegmentedControl ariaLabel="时间范围" value={range} onValueChange={setRange} options={RANGE_OPTIONS} fullWidth size="sm" />
          <SegmentedControl ariaLabel="分组" value={groupBy} onValueChange={setGroupBy} options={GROUP_OPTIONS} fullWidth size="sm" />
        </div>

        <section
          aria-label="今日费用"
          className={cn(
            'rounded-xl border bg-card p-4 shadow-card',
            warning && 'border-warning/50 bg-warning-soft/40',
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-footnote text-muted-foreground">今日费用</p>
              <p className={cn('mt-0.5 text-title font-semibold tabular-nums', warning && 'text-warning')}>
                {formatMicros(todayMicros, 'CNY')}
              </p>
            </div>
            <div className="shrink-0 text-right">
              <p className="text-footnote text-muted-foreground">每日上限</p>
              <p className={cn('mt-0.5 text-sm font-medium tabular-nums', warning && 'text-warning')}>
                {limit === null ? '—' : limit === 0 ? '不限' : formatMicros(Math.round(limit * 1_000_000), 'CNY')}
              </p>
              {warning ? <p className="mt-0.5 text-footnote font-medium text-warning">已接近每日上限</p> : null}
            </div>
          </div>
        </section>

        {costs.isPending ? (
          <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在汇总成本…" /></div>
        ) : costs.error && !costs.data ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              message={`读取成本失败：${queryError(costs.error)}`}
              onRetry={() => void costs.refetch()}
              retrying={costs.isFetching}
            />
          </div>
        ) : (
          <>
            <section aria-label="成本汇总" className="rounded-xl border bg-card p-4 shadow-card">
              <div className="grid grid-cols-3 gap-2 divide-x divide-border">
                <SummaryStat label="总费用" value={formatMicros(costs.data?.total_micros ?? 0, currency)} />
                <SummaryStat label="调用次数" value={formatTokens(totalCalls(groups))} />
                <SummaryStat label="缓存命中率" value={hitRate} />
              </div>
            </section>

            {chartSpec ? (
              <Section title="按天费用" description="柱高是当天模型费用">
                <div className="rounded-xl border bg-card p-3 shadow-card">
                  <EChartSurface spec={chartSpec} />
                </div>
              </Section>
            ) : null}

            <Section title="分组明细">
              {groups.length === 0 ? (
                <div className="rounded-xl border bg-card shadow-card">
                  <EmptyState icon={Coins} title="这段时间没有模型调用" description="模型调用后费用会记在这里" />
                </div>
              ) : (
                <div className="overflow-x-auto rounded-xl border bg-card shadow-card">
                  <table aria-label="成本分组" className="w-full min-w-[36rem] text-sm">
                    <thead className="bg-surface-2/60 text-footnote text-muted-foreground">
                      <tr>
                        <th scope="col" className="px-3 py-2 text-left font-medium">名称</th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">调用</th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">输入 token</th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">缓存命中</th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">输出 token</th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">费用</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border tabular-nums">
                      {groups.map((group: CostGroupView) => (
                        <tr key={group.key}>
                          <th scope="row" className="max-w-48 truncate px-3 py-2.5 text-left font-medium">{groupLabel(group.key)}</th>
                          <td className="px-3 py-2.5 text-right">{formatTokens(group.calls)}</td>
                          <td className="px-3 py-2.5 text-right">{formatTokens(group.prompt_tokens)}</td>
                          <td className="px-3 py-2.5 text-right">{formatTokens(group.cached_tokens)}</td>
                          <td className="px-3 py-2.5 text-right">{formatTokens(group.completion_tokens)}</td>
                          <td className="px-3 py-2.5 text-right">{formatMicros(group.cost_micros, currency)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          </>
        )}
      </div>
    </Page>
  )
}

export default CostsPage
