import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Badge, type StatusTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { formatDateTime, formatDuration, formatMicros } from '@/features/runs/format'
import type { components } from '@/types/api'
import { evalRunQueryOptions } from './api'
import {
  evalModeLabel,
  evalRunStatus,
  evalSuiteLabel,
  formatCi,
  formatPercent,
  type MetricId,
} from './format'

/**
 * 一次离线评测的详情（M99 §4.6，`/more/evals/:runId`）。
 *
 * 概要与指标率只读接口返回的形状；用例表保持后端 `attempt, scenario` 的顺序，
 * 每行用原生 `<details>` 展开 grader 的 details 与 error。用例超过 500 条时
 * 接口会截断并给出 `cases_truncated`，页面在表格上方提示。
 */

type EvalCaseView = components['schemas']['EvalCaseOut']
type EvalCaseStatusView = components['schemas']['EvalCaseStatus']
type EvalMetricRateView = components['schemas']['EvalMetricRateOut']
type EvalRunDetailView = components['schemas']['EvalRunDetailOut']

const CARD = 'rounded-xl border bg-card shadow-card'

const METRIC_LABELS: Record<MetricId, string> = {
  accuracy: '准确率',
  hallucination_rate: '幻觉率',
  safety_rate: '安全率',
  task_completion: '任务完成率',
  conversation_efficiency: '对话效率',
  user_satisfaction: '用户满意度',
  cost: '成本',
}

const CASE_STATUS: Record<EvalCaseStatusView, { label: string; tone: StatusTone }> = {
  passed: { label: '通过', tone: 'success' },
  failed: { label: '失败', tone: 'warning' },
  error: { label: '错误', tone: 'danger' },
  skipped: { label: '跳过', tone: 'neutral' },
}

function metricLabel(metric: MetricId): string {
  return METRIC_LABELS[metric] ?? metric
}

function caseStatus(status: EvalCaseStatusView) {
  return CASE_STATUS[status] ?? { label: '未知', tone: 'neutral' as const }
}

/** 得分保留两位；没有得分写 `—`，不拿 0 冒充。 */
function formatScore(score: number | null): string {
  return typeof score === 'number' && Number.isFinite(score) ? score.toFixed(2) : '—'
}

/** grader 的 details 是纯文本列表；防御快照里的旧形状。 */
function graderDetailLines(graders: EvalCaseView['graders']): string[] {
  return graders.flatMap((grader) => {
    const details = grader.details
    if (!Array.isArray(details)) return []
    return details.filter((detail): detail is string => typeof detail === 'string')
  })
}

function SummaryItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-footnote text-muted-foreground">{label}</dt>
      <dd className="truncate text-[0.9375rem] font-semibold tabular-nums">{children}</dd>
    </div>
  )
}

function RunSummary({ run }: { run: EvalRunDetailView }) {
  const status = evalRunStatus(run)
  return (
    <section aria-label="评测概要" className={`${CARD} p-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="primary">{evalSuiteLabel(run.suite)}</Badge>
        <Badge variant="outline">{evalModeLabel(run.mode)}</Badge>
        <Badge variant={status.tone}>{status.label}</Badge>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
        <SummaryItem label="通过率">{formatPercent(run.pass_rate)}</SummaryItem>
        <SummaryItem label="总数">{run.total}</SummaryItem>
        <SummaryItem label="通过">{run.passed}</SummaryItem>
        <SummaryItem label="失败">{run.failed}</SummaryItem>
        <SummaryItem label="错误">{run.error}</SummaryItem>
        <SummaryItem label="跳过">{run.skipped}</SummaryItem>
        <SummaryItem label="成本">{formatMicros(run.cost_micros, run.currency)}</SummaryItem>
        <SummaryItem label="耗时">{formatDuration(run.duration_ms)}</SummaryItem>
        <SummaryItem label="开始">{formatDateTime(run.started_at)}</SummaryItem>
        <SummaryItem label="结束">{formatDateTime(run.finished_at)}</SummaryItem>
      </dl>
    </section>
  )
}

function MetricRates({ rates }: { rates: readonly EvalMetricRateView[] }) {
  if (rates.length === 0) {
    return (
      <div className={CARD}>
        <EmptyState
          title="这次评测没有指标记录"
          description="用例带上指标标签并发布后显示"
        />
      </div>
    )
  }
  return (
    <div className="overflow-x-auto rounded-xl border bg-card shadow-card">
      <table aria-label="指标率" className="w-full min-w-[36rem] text-sm">
        <thead className="bg-surface-2/60 text-footnote text-muted-foreground">
          <tr>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">指标</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">通过 / 可判定</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">比例</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">Wilson 区间</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border tabular-nums">
          {rates.map((item) => (
            <tr key={item.metric}>
              <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left font-medium">
                {metricLabel(item.metric)}
              </th>
              <td className="whitespace-nowrap px-3 py-2.5 text-right">{item.passed} / {item.total}</td>
              <td className="whitespace-nowrap px-3 py-2.5 text-right">
                {item.rate === null ? '—' : formatPercent(item.rate)}
              </td>
              <td className="whitespace-nowrap px-3 py-2.5 text-left">
                {item.ci_low === null || item.ci_high === null
                  ? '—'
                  : formatCi(item.ci_low, item.ci_high, 'ratio')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CaseDetails({ item }: { item: EvalCaseView }) {
  const lines = graderDetailLines(item.graders)
  return (
    <div className="mt-2 space-y-2 rounded-lg bg-surface-2/60 p-2.5 text-footnote font-normal">
      {lines.length > 0 ? (
        <ul className="list-disc space-y-1 pl-4">
          {lines.map((line, index) => (
            <li key={`${item.scenario}-${item.attempt}-detail-${index}`} className="whitespace-pre-wrap break-words">
              {line}
            </li>
          ))}
        </ul>
      ) : null}
      {item.error ? (
        <div className="space-y-0.5">
          <p className="font-medium text-muted-foreground">错误</p>
          <p className="whitespace-pre-wrap break-words">{item.error}</p>
        </div>
      ) : null}
      {lines.length === 0 && !item.error ? (
        <p className="text-muted-foreground">这次用例没有判分详情</p>
      ) : null}
    </div>
  )
}

function CasesTable({ cases }: { cases: readonly EvalCaseView[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border bg-card shadow-card">
      <table aria-label="评测用例" className="w-full min-w-[64rem] text-sm">
        <thead className="bg-surface-2/60 text-footnote text-muted-foreground">
          <tr>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">场景</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">标题</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">指标</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">状态</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">得分</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">耗时</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">模型调用</th>
            <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">成本</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border tabular-nums">
          {cases.map((item) => {
            const status = caseStatus(item.status)
            return (
              <tr key={`${item.attempt}-${item.scenario}`} className="align-top">
                <th scope="row" className="min-w-[12rem] px-3 py-2.5 text-left align-top font-medium">
                  <details className="group">
                    <summary className="cursor-pointer list-none rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                      <span className="break-words">{item.scenario}</span>
                      <span className="ml-1.5 text-footnote text-muted-foreground">判分详情</span>
                    </summary>
                    <CaseDetails item={item} />
                  </details>
                </th>
                <td className="px-3 py-2.5 align-top">{item.title || '—'}</td>
                <td className="px-3 py-2.5 align-top">
                  {item.metrics.length > 0 ? (
                    <span className="flex flex-wrap gap-1">
                      {item.metrics.map((metric) => (
                        <Badge key={metric} variant="outline">{metricLabel(metric)}</Badge>
                      ))}
                    </span>
                  ) : '—'}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 align-top">
                  <Badge variant={status.tone}>{status.label}</Badge>
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right align-top">{formatScore(item.score)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right align-top">{formatDuration(item.duration_ms)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right align-top">{item.model_calls}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right align-top">
                  {formatMicros(item.cost_micros, item.currency)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function MissingRun() {
  return (
    <Page title="评测详情" back="/more/insights">
      <div className={CARD}>
        <ErrorState title="找不到这次评测" message="评测运行不存在" />
      </div>
    </Page>
  )
}

function EvalRunDetail({ runId }: { runId: string }) {
  const detail = useQuery(evalRunQueryOptions(runId))

  if (detail.isPending) {
    return (
      <Page title="评测详情" back="/more/insights">
        <div className={CARD}><LoadingState label="正在读取评测详情…" /></div>
      </Page>
    )
  }

  if (detail.error && !detail.data) {
    const apiError = toApiError(detail.error)
    const notFound = apiError.status === 404 || apiError.code === 'EVAL_RUN_NOT_FOUND'
    return (
      <Page title="评测详情" back="/more/insights">
        <div className={CARD}>
          <ErrorState
            title={notFound ? '找不到这次评测' : '读取评测详情失败'}
            message={apiError.message}
            onRetry={notFound ? undefined : () => void detail.refetch()}
            retrying={detail.isFetching}
          />
        </div>
      </Page>
    )
  }

  if (!detail.data) return <MissingRun />
  const run = detail.data

  return (
    <Page title="评测详情" back="/more/insights">
      <div className="space-y-5 pb-4">
        {detail.error ? (
          <InlineAlert
            tone="warning"
            title="刷新评测详情失败"
            action={<Button variant="outline" size="sm" onClick={() => void detail.refetch()}>重试</Button>}
          >
            {toApiError(detail.error).message}
          </InlineAlert>
        ) : null}

        <RunSummary run={run} />

        <Section id="eval-metrics" title="指标">
          <MetricRates rates={run.metric_rates} />
        </Section>

        <Section id="eval-cases" title="用例">
          <div className="space-y-2">
            {run.cases_truncated ? (
              <InlineAlert tone="warning">只显示前 500 条</InlineAlert>
            ) : null}
            {run.cases.length === 0 ? (
              <div className={CARD}>
                <EmptyState
                  title="这次评测没有用例记录"
                  description="发布带用例的评测后显示"
                />
              </div>
            ) : (
              <CasesTable cases={run.cases} />
            )}
          </div>
        </Section>
      </div>
    </Page>
  )
}

/** 路由入口：`useParams` 只在有 id 时渲染带 hooks 的详情组件。 */
export default function EvalRunDetailPage() {
  const { runId } = useParams()
  if (!runId) return <MissingRun />
  return <EvalRunDetail runId={runId} />
}
