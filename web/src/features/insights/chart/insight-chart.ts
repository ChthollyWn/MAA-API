import type { ChartCardData } from '@/features/agent/media'
import type { components } from '@/types/api'
import { formatCi, formatMetricValue, unitLabel } from '../format'

/**
 * 接口数据 → `ChartCardData` 的映射（M99 §4.6 第 5、6 项）。
 *
 * 只做形状转换，不改 `ChartCardData` / `EChartSurface` / `buildChartOption`；趋势用
 * 已注册的 `line`，分组用 `bar`，数据表兜底由 `ChartCard` 自己提供。
 */

export type TrendPointView = components['schemas']['TrendPointOut']
export type BreakdownItemView = components['schemas']['BreakdownItemOut']

/** 趋势卡副标题：最近一个有点的 `值 · n=… · 95% CI …`。 */
export function trendCaption(points: readonly TrendPointView[], unit: string): string {
  const latest = [...points].reverse().find((point) => point.value !== null) ?? points[points.length - 1]
  if (!latest) return ''
  const ci = latest.ci_low !== null && latest.ci_high !== null
    ? ` · ${formatCi(latest.ci_low, latest.ci_high, unit)}`
    : ''
  return `${formatMetricValue(latest.value, unit)} · n=${latest.sample_size}${ci}`
}

export function buildTrendChartData(points: readonly TrendPointView[], unit: string): ChartCardData {
  const label = unitLabel(unit)
  return {
    type: 'chart',
    title: '指标趋势',
    caption: trendCaption(points, unit),
    chartType: 'line',
    categories: points.map((point) => point.date),
    series: [{ name: label, values: points.map((point) => point.value) }],
    stacked: false,
    xLabel: '日期',
    yLabel: label,
    unit: label,
    source: '数据来自评测库与运行账本',
  }
}

/**
 * ``BreakdownItemOut.key`` 是后端 ``ticket.kind``（Worker 代理定义的 name，如
 * ``daily_ops``），不能直接摆到界面上（docs/11 不暴露代理代码名）。页面传入
 * ``/api/agent/specs`` 的 name → title 映射；找不到时回退接口给的 label。
 */
export function buildBreakdownChartData(
  items: readonly BreakdownItemView[],
  unit: string,
  titles?: ReadonlyMap<string, string>,
): ChartCardData {
  const label = unitLabel(unit)
  return {
    type: 'chart',
    title: '按工单类型',
    caption: `共 ${items.length} 类`,
    chartType: 'bar',
    categories: items.map((item) => titles?.get(item.key) ?? item.label),
    series: [{ name: label, values: items.map((item) => item.value) }],
    stacked: false,
    xLabel: '工单类型',
    yLabel: label,
    unit: label,
    source: '数据来自运行账本',
  }
}
