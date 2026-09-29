import type { EChartsOption, SeriesOption } from 'echarts'
import type { ChartCardData } from '../media'

/** 图表用的主题色；由 `index.css` 的语义 token 在运行时读出（见 `EChartSurface`）。 */
export interface ChartThemeColors {
  foreground: string
  muted: string
  border: string
  surface: string
}

/** CSS 变量不可用时的兜底（暗色一套），保证 option 永远是合法颜色而不是空串。 */
export const FALLBACK_CHART_PALETTE = ['#7b86f0', '#e8b256', '#a8b0bd', '#5cc0f0', '#c98cf0', '#54d3a0'] as const

export const FALLBACK_CHART_COLORS: ChartThemeColors = {
  foreground: '#e7e9ee',
  muted: '#959ba7',
  border: '#272c35',
  surface: '#15181d',
}

function axisName(label: string, unit: string): string {
  if (label && unit) return `${label}（${unit}）`
  return label || unit
}

/**
 * 卡片规格 + 调色板 + 主题色 → ECharts option（纯函数，可单测）。
 *
 * 约定（规格 §3.8）：
 * - `line` / `bar` 走类目轴；`stacked` 时给每个系列同一个 `stack`；
 * - `pie` 只用第一条系列，环形（`radius: ['40%', '70%']`）；
 * - `tooltip.trigger`：折线/柱状 `axis`、饼图 `item`；
 * - `legend` 只在多系列时显示（饼图的「系列」是类目）；
 * - `grid` 用 `containLabel` 留出轴标签空间；
 * - 数值里的 `null` 原样保留（折线断点），不写成 0。
 */
export function buildChartOption(
  spec: ChartCardData,
  palette: readonly string[],
  colors: ChartThemeColors,
): EChartsOption {
  const color = palette.length > 0 ? [...palette] : [...FALLBACK_CHART_PALETTE]
  const textStyle = { color: colors.foreground }

  if (spec.chartType === 'pie') {
    const first = spec.series[0]
    const values = first?.values ?? []
    return {
      color,
      backgroundColor: 'transparent',
      textStyle,
      tooltip: { trigger: 'item', confine: true },
      legend: {
        show: spec.categories.length > 1,
        bottom: 0,
        type: 'scroll',
        textStyle: { color: colors.muted },
      },
      series: [
        {
          name: first?.name ?? '',
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['50%', '48%'],
          avoidLabelOverlap: true,
          // 断点（null）在饼图里没有扇区，原样交给 ECharts 忽略。
          data: spec.categories.map((name, index) => ({ name, value: values[index] ?? null })),
          itemStyle: { borderColor: colors.surface, borderWidth: 1 },
          label: { color: colors.foreground },
          labelLine: { lineStyle: { color: colors.border } },
        } as unknown as SeriesOption,
      ],
    }
  }

  // 饼图已经在上面返回；这里的类型收窄要显式存一份，闭包里才不会被还原成 ChartType。
  const seriesType: 'line' | 'bar' = spec.chartType
  const legendShown = spec.series.length > 1
  return {
    color,
    backgroundColor: 'transparent',
    textStyle,
    tooltip: { trigger: 'axis', confine: true },
    legend: {
      show: legendShown,
      bottom: 0,
      type: 'scroll',
      textStyle: { color: colors.muted },
    },
    grid: {
      left: 8,
      right: 16,
      top: 12,
      bottom: legendShown ? 28 : 8,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: [...spec.categories],
      name: spec.xLabel || undefined,
      nameTextStyle: { color: colors.muted },
      axisLabel: { color: colors.muted },
      axisLine: { lineStyle: { color: colors.border } },
      axisTick: { lineStyle: { color: colors.border } },
    },
    yAxis: {
      type: 'value',
      name: axisName(spec.yLabel, spec.unit) || undefined,
      nameTextStyle: { color: colors.muted },
      axisLabel: { color: colors.muted },
      splitLine: { lineStyle: { color: colors.border, type: 'dashed' } },
    },
    series: spec.series.map((series) => ({
      name: series.name,
      type: seriesType,
      data: [...series.values],
      // null 保留成断点：折线断开，柱状留空，而不是掉到 0（ECharts 运行时支持，类型定义里收紧）。
      connectNulls: false,
      ...(spec.stacked ? { stack: 'total' } : {}),
      ...(seriesType === 'bar' ? { barMaxWidth: 32 } : { smooth: false }),
    })) as unknown as SeriesOption[],
  }
}
