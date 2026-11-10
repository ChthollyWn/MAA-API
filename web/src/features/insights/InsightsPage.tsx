import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ChartLine, FlaskConical, MessageSquare } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { ChartCard } from '@/features/agent/MediaCards'
import { specsQueryOptions } from '@/features/runs/api'
import { agentTitle, formatDateTime, formatDuration, formatMicros } from '@/features/runs/format'
import type { components } from '@/types/api'
import { MetricCard } from './MetricCard'
import { buildBreakdownChartData, buildTrendChartData } from './chart/insight-chart'
import {
  EVAL_RUNS_PAGE_SIZE,
  evalRunsQueryOptions,
  insightBreakdownQueryOptions,
  insightsCatalogQueryOptions,
  insightsFeedbackQueryOptions,
  insightsOverviewQueryOptions,
  insightTrendQueryOptions,
  type MetricId,
} from './api'
import {
  EVIDENCE_LABELS,
  evalModeLabel,
  evalRunStatus,
  evalSuiteLabel,
  feedbackReasonLabel,
  formatPercent,
  ratingStars,
} from './format'

type MetricDefinitionView = components['schemas']['MetricDefinitionOut']
type EvalRunSummaryView = components['schemas']['EvalRunSummaryOut']

type WindowDays = '7' | '30' | '90'

const WINDOW_OPTIONS = [
  { value: '7', label: '7 天' },
  { value: '30', label: '30 天' },
  { value: '90', label: '90 天' },
] as const satisfies readonly { value: WindowDays; label: string }[]

const CARD = 'rounded-xl border bg-card shadow-card'

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

function MethodRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

function EvalRunsTable({ runs }: { runs: readonly EvalRunSummaryView[] }) {
  const navigate = useNavigate()
  return (
    <div className="overflow-x-auto rounded-xl border bg-card shadow-card">
      <table aria-label="最近评测" className="w-full min-w-[46rem] text-sm">
        <thead className="bg-surface-2/60 text-footnote text-muted-foreground">
          <tr>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">时间</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">套件</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">模式</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">通过率</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">用例数</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">成本</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">耗时</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">状态</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border tabular-nums">
          {runs.map((run) => {
            const path = `/more/evals/${encodeURIComponent(run.id)}`
            const status = evalRunStatus(run)
            return (
              <tr
                key={run.id}
                onClick={() => navigate(path)}
                className="cursor-pointer transition-colors hover:bg-surface-2/60"
              >
                <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left font-medium">
                  <Link
                    to={path}
                    onClick={(event) => event.stopPropagation()}
                    className="rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {formatDateTime(run.finished_at)}
                  </Link>
                </th>
                <td className="whitespace-nowrap px-3 py-2.5"><Badge variant="primary">{evalSuiteLabel(run.suite)}</Badge></td>
                <td className="whitespace-nowrap px-3 py-2.5"><Badge variant="outline">{evalModeLabel(run.mode)}</Badge></td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right">{formatPercent(run.pass_rate)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right">{run.total}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right">{formatMicros(run.cost_micros, run.currency)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right">{formatDuration(run.duration_ms)}</td>
                <td className="whitespace-nowrap px-3 py-2.5"><Badge variant={status.tone}>{status.label}</Badge></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** M99 能力效果看板（`/more/insights`）。 */
export function InsightsPage() {
  const [windowDays, setWindowDays] = useState<WindowDays>('30')
  const [metric, setMetric] = useState<MetricId>('task_completion')
  const days = Number(windowDays)

  const catalog = useQuery(insightsCatalogQueryOptions)
  const overview = useQuery(insightsOverviewQueryOptions(days))
  const trend = useQuery(insightTrendQueryOptions(metric, days))
  const definition = useMemo(
    () => catalog.data?.find((item) => item.id === metric),
    [catalog.data, metric],
  )
  const breakdown = useQuery(insightBreakdownQueryOptions(metric, days, definition?.supports_breakdown === true))
  const feedback = useQuery(insightsFeedbackQueryOptions(days))
  const evalRuns = useQuery(evalRunsQueryOptions({ page: 1, size: EVAL_RUNS_PAGE_SIZE }))
  const specs = useQuery(specsQueryOptions)

  const definitions = useMemo(
    () => new Map((catalog.data ?? []).map((item) => [item.id, item])),
    [catalog.data],
  )
  const titles = useMemo(
    () => new Map((specs.data ?? []).map((spec) => [spec.name, spec.title])),
    [specs.data],
  )

  const cards = overview.data?.items ?? []
  const offline = cards.filter((card) => card.scope === 'offline')
  const online = cards.filter((card) => card.scope === 'online')
  const warnings = overview.data?.warnings ?? []
  const runs = evalRuns.data?.items ?? []
  const feedbackItems = feedback.data?.items ?? []
  const methods = catalog.data ?? []
  const metricOptions = methods.map((item) => ({ value: item.id, label: item.label }))

  const trendPoints = trend.data?.points ?? []
  const trendChart = trend.data && trendPoints.length > 0
    ? buildTrendChartData(trendPoints, trend.data.unit)
    : null
  const breakdownItems = breakdown.data?.items ?? []
  const breakdownChart = breakdown.data && definition && breakdownItems.length > 0
    ? buildBreakdownChartData(breakdownItems, definition.unit, titles)
    : null

  const refresh = () => {
    void overview.refetch()
    void catalog.refetch()
    void trend.refetch()
    if (definition?.supports_breakdown) void breakdown.refetch()
    void feedback.refetch()
    void evalRuns.refetch()
  }

  return (
    <Page title="能力效果" back="/more">
      <div className="space-y-5 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <SegmentedControl
            ariaLabel="统计窗口"
            value={windowDays}
            onValueChange={(value) => setWindowDays(value as WindowDays)}
            options={WINDOW_OPTIONS}
            size="sm"
          />
          <InfoHint label="指标说明" align="end">
            指标来自运行账本与离线评测。没有数据源的指标显示为空，不估算。
          </InfoHint>
        </div>

        {overview.error && overview.data ? (
          <InlineAlert
            tone="danger"
            title="刷新能力效果失败"
            action={<Button variant="outline" size="sm" onClick={refresh}>重试</Button>}
          >
            {queryError(overview.error)}
          </InlineAlert>
        ) : null}

        {warnings.length > 0 ? (
          <InlineAlert tone="warning" title="数据说明">{warnings.join('；')}</InlineAlert>
        ) : null}

        {overview.isPending ? (
          <div className={CARD}><LoadingState label="正在读取能力效果…" /></div>
        ) : overview.error && !overview.data ? (
          <div className={CARD}>
            <ErrorState
              message={`读取能力效果失败：${queryError(overview.error)}`}
              onRetry={() => void overview.refetch()}
              retrying={overview.isFetching}
            />
          </div>
        ) : (
          <>
            <Section id="insights-capability" title="能力指标">
              <div className="grid gap-2 sm:grid-cols-3">
                {offline.map((card) => (
                  <MetricCard key={card.id} card={card} definition={definitions.get(card.id)} />
                ))}
              </div>
            </Section>

            <Section id="insights-effect" title="运行效果">
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {online.map((card) => (
                  <MetricCard key={card.id} card={card} definition={definitions.get(card.id)} />
                ))}
              </div>
            </Section>

            <Section id="insights-trend" title="趋势">
              <div className="space-y-2">
                {metricOptions.length > 0 ? (
                  <div className="-mx-1 overflow-x-auto px-1 pb-1">
                    <SegmentedControl
                      ariaLabel="趋势指标"
                      value={metric}
                      onValueChange={(value) => setMetric(value as MetricId)}
                      options={metricOptions}
                      size="sm"
                    />
                  </div>
                ) : null}
                {trend.isPending ? (
                  <div className={CARD}><LoadingState label="正在读取趋势…" /></div>
                ) : trend.error ? (
                  <InlineAlert tone="danger" title="读取趋势失败">
                    {queryError(trend.error)}
                    <Button variant="outline" size="sm" onClick={() => void trend.refetch()}>重试</Button>
                  </InlineAlert>
                ) : trendChart ? (
                  <ChartCard card={trendChart} />
                ) : (
                  <div className={CARD}>
                    <EmptyState icon={ChartLine} title="这个窗口还没有数据" description="有评测或工单数据后显示" />
                  </div>
                )}
              </div>
            </Section>

            {definition?.supports_breakdown ? (
              <Section id="insights-breakdown" title="按工单类型">
                {breakdown.isPending ? (
                  <div className={CARD}><LoadingState label="正在读取分组…" /></div>
                ) : breakdown.error ? (
                  <InlineAlert tone="danger" title="读取分组失败">
                    {queryError(breakdown.error)}
                    <Button variant="outline" size="sm" onClick={() => void breakdown.refetch()}>重试</Button>
                  </InlineAlert>
                ) : breakdownChart ? (
                  <ChartCard card={breakdownChart} />
                ) : (
                  <div className={CARD}>
                    <EmptyState icon={ChartLine} title="这个窗口还没有数据" description="完成工单后显示" />
                  </div>
                )}
              </Section>
            ) : null}

            <Section id="insights-evals" title="离线评测">
              <div className="space-y-2">
                {evalRuns.isPending ? (
                  <div className={CARD}><LoadingState label="正在读取评测记录…" /></div>
                ) : evalRuns.error ? (
                  <InlineAlert tone="danger" title="读取评测记录失败">
                    {queryError(evalRuns.error)}
                    <Button variant="outline" size="sm" onClick={() => void evalRuns.refetch()}>重试</Button>
                  </InlineAlert>
                ) : runs.length === 0 ? (
                  <div className={CARD}>
                    <EmptyState
                      icon={FlaskConical}
                      title="还没有评测记录"
                      description="在命令行跑评测并发布后显示"
                    />
                  </div>
                ) : (
                  <EvalRunsTable runs={runs} />
                )}
                <p className="px-1 text-footnote text-muted-foreground">
                  评测在命令行跑：<code className="font-mono">scripts/agent_eval.py --publish</code> 后在这里出现
                </p>
              </div>
            </Section>

            <Section id="insights-feedback" title="最近评价">
              {feedback.isPending ? (
                <div className={CARD}><LoadingState label="正在读取评价…" /></div>
              ) : feedback.error ? (
                <InlineAlert tone="danger" title="读取评价失败">
                  {queryError(feedback.error)}
                  <Button variant="outline" size="sm" onClick={() => void feedback.refetch()}>重试</Button>
                </InlineAlert>
              ) : feedbackItems.length === 0 ? (
                <div className={CARD}>
                  <EmptyState icon={MessageSquare} title="还没有收到评价" description="在运行详情页评价一次后显示" />
                </div>
              ) : (
                <ul className="divide-y divide-border overflow-hidden rounded-xl border bg-card shadow-card">
                  {feedbackItems.map((item) => (
                    <li key={item.id} className="space-y-1 p-3">
                      <div className="flex items-start justify-between gap-2">
                        <span role="img" aria-label={`评分 ${item.rating} 分`} className="text-warning">
                          {ratingStars(item.rating)}
                        </span>
                        <span className="text-footnote text-muted-foreground">{formatDateTime(item.created_at)}</span>
                      </div>
                      {item.reasons.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {item.reasons.map((reason) => (
                            <Badge key={reason} variant="neutral">{feedbackReasonLabel(reason)}</Badge>
                          ))}
                        </div>
                      ) : null}
                      {item.comment ? <p className="break-words text-sm">{item.comment}</p> : null}
                      <p className="text-footnote text-muted-foreground">
                        {item.ticket_id ? `工单 ${item.ticket_id} · ` : ''}
                        {agentTitle(item.agent_name, titles)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section id="insights-methods" title="评估方法">
              {catalog.isPending ? (
                <div className={CARD}><LoadingState label="正在读取指标目录…" /></div>
              ) : catalog.error ? (
                <InlineAlert tone="danger" title="读取指标目录失败">
                  {queryError(catalog.error)}
                  <Button variant="outline" size="sm" onClick={() => void catalog.refetch()}>重试</Button>
                </InlineAlert>
              ) : methods.length === 0 ? (
                <div className={CARD}>
                  <EmptyState title="还没有指标目录" description="服务端目录就绪后显示" />
                </div>
              ) : (
                <ol className="space-y-2">
                  {methods.map((item: MetricDefinitionView) => (
                    <li key={item.id}>
                      <details className="group overflow-hidden rounded-xl border bg-card shadow-card">
                        <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-medium">{item.label}</span>
                            <Badge variant="outline">{item.scope === 'offline' ? '离线' : '在线'}</Badge>
                          </span>
                          <span className="shrink-0 text-footnote text-muted-foreground">定义、公式与限制</span>
                        </summary>
                        <dl className="space-y-1.5 border-t border-border/70 px-3 py-3 text-footnote">
                          <MethodRow label="定义">{item.definition}</MethodRow>
                          <MethodRow label="公式">{item.formula}</MethodRow>
                          <MethodRow label="数据源">{item.source}</MethodRow>
                          <MethodRow label="评估方法">{item.evaluation}</MethodRow>
                          <MethodRow label="证据等级">{EVIDENCE_LABELS[item.evidence]}</MethodRow>
                          <MethodRow label="样本阈值">最少 {item.min_sample} 个样本</MethodRow>
                          <MethodRow label="限制">
                            <ul className="list-disc space-y-0.5 pl-4">
                              {item.limitations.map((limitation, index) => (
                                <li key={`${item.id}-limitation-${index}`}>{limitation}</li>
                              ))}
                            </ul>
                          </MethodRow>
                        </dl>
                      </details>
                    </li>
                  ))}
                </ol>
              )}
            </Section>
          </>
        )}
      </div>
    </Page>
  )
}

export default InsightsPage
