import { useEffect, useMemo, useRef, useState } from 'react'
import type { EChartsType } from 'echarts/core'
import { useUiStore } from '@/stores/ui'
import type { ChartCardData } from '../media'
import {
  buildChartOption,
  FALLBACK_CHART_COLORS,
  FALLBACK_CHART_PALETTE,
  type ChartThemeColors,
} from './buildChartOption'

interface ChartTokens {
  palette: string[]
  colors: ChartThemeColors
}

function cssValue(root: HTMLElement, name: string, fallback: string): string {
  if (typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') return fallback
  const raw = window.getComputedStyle(root).getPropertyValue(name).trim()
  return raw || fallback
}

/**
 * 从 `:root` 上读调色板与主题色。图表不许自己写死颜色：`--chart-1…--chart-6`
 * 与 `--foreground` / `--muted-foreground` / `--border` / `--surface` 是唯一来源
 * （规格 §3.8），暗色主题靠 `:root.dark` 覆盖同名变量自动跟随。
 */
export function readChartTokens(): ChartTokens {
  const root = typeof document === 'undefined' ? null : document.documentElement
  if (!root) return { palette: [...FALLBACK_CHART_PALETTE], colors: { ...FALLBACK_CHART_COLORS } }
  return {
    palette: FALLBACK_CHART_PALETTE.map((fallback, index) => cssValue(root, `--chart-${index + 1}`, fallback)),
    colors: {
      foreground: cssValue(root, '--foreground', FALLBACK_CHART_COLORS.foreground),
      muted: cssValue(root, '--muted-foreground', FALLBACK_CHART_COLORS.muted),
      border: cssValue(root, '--border', FALLBACK_CHART_COLORS.border),
      surface: cssValue(root, '--surface', FALLBACK_CHART_COLORS.surface),
    },
  }
}

/**
 * ECharts 面：动态 import + 按需注册 + `ResizeObserver` 跟随容器。
 *
 * 只有会话里真的出现图表卡才会加载 echarts（独立 chunk）。jsdom 里没有 canvas，
 * 组件测试对 `echarts/core` 等模块做 mock；真机上加载失败或 `init` 抛错时回调
 * `onError`，由父组件打开数据表兜底——图表永远不是唯一的信息来源。
 */
export function EChartSurface({ spec, onError }: { spec: ChartCardData; onError?: (message: string) => void }) {
  const container = useRef<HTMLDivElement | null>(null)
  const chart = useRef<EChartsType | null>(null)
  const [ready, setReady] = useState(false)
  const [tokens, setTokens] = useState<ChartTokens>(readChartTokens)
  const theme = useUiStore((state) => state.theme)
  // 回调放进 ref：父组件传内联函数时不能让它变成「重建整个图表」的理由。
  const onErrorRef = useRef(onError)
  useEffect(() => {
    onErrorRef.current = onError
  }, [onError])

  // 主题跟随：`useUiStore` 的值变了重新读一遍 token；`system` 时还要听系统颜色偏好的变化
  // （store 自己会改 `:root.dark`，我们只负责把新值取回来重建 option）。
  useEffect(() => {
    const sync = () => setTokens(readChartTokens())
    sync()
    if (theme !== 'system' || typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener?.('change', sync)
    return () => media.removeEventListener?.('change', sync)
  }, [theme])

  useEffect(() => {
    const node = container.current
    if (!node) return
    let disposed = false
    let observer: ResizeObserver | null = null
    void (async () => {
      try {
        const [core, charts, components, renderers] = await Promise.all([
          import('echarts/core'),
          import('echarts/charts'),
          import('echarts/components'),
          import('echarts/renderers'),
        ])
        if (disposed) return
        core.use([
          charts.LineChart,
          charts.BarChart,
          charts.PieChart,
          components.GridComponent,
          components.TooltipComponent,
          components.LegendComponent,
          components.TitleComponent,
          renderers.CanvasRenderer,
        ])
        const instance = core.init(node)
        if (disposed) {
          instance.dispose()
          return
        }
        chart.current = instance
        setReady(true)
        if (typeof ResizeObserver !== 'undefined') {
          observer = new ResizeObserver(() => instance.resize())
          observer.observe(node)
        }
      } catch (error) {
        if (disposed) return
        onErrorRef.current?.(error instanceof Error ? error.message : '图表加载失败')
      }
    })()
    return () => {
      disposed = true
      observer?.disconnect()
      chart.current?.dispose()
      chart.current = null
      setReady(false)
    }
  }, [])

  const option = useMemo(() => buildChartOption(spec, tokens.palette, tokens.colors), [spec, tokens])

  useEffect(() => {
    if (!ready || !chart.current) return
    chart.current.setOption(option, { notMerge: true })
  }, [ready, option])

  return <div ref={container} data-testid="echart-surface" className="h-56 w-full" />
}
