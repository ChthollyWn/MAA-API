import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/**
 * 消耗护栏的接口与展示规则（M84 规格 §4.3、§4.6）。
 *
 * 两个端点：`GET /api/agent/spend` 读今日三项、按日用量与今天拦下的提交；
 * `POST /api/agent/brief/run` 立即生成一份每日简报。消耗日从北京时间 04:00
 * 起算（§4.5.2），所以「今天」与拦截记录的钟点都按 `Asia/Shanghai` 算，
 * 与浏览器时区无关。
 */
export type SpendSummary = components['schemas']['SpendSummary']
export type SpendKindUsage = components['schemas']['SpendKindUsage']
export type SpendDayRow = components['schemas']['SpendDayRow']
export type SpendBlock = components['schemas']['SpendBlock']
export type BriefRunResult = components['schemas']['BriefRunResult']

/** 拦截记录只有种类代码，标签在这里补齐；与 `SPEND_LABELS` 一致。 */
export const SPEND_KIND_LABELS: Record<SpendKindUsage['kind'], string> = {
  medicine: '理智药',
  stone: '源石',
  expedite: '加急许可',
}

export const SPEND_SOURCE_LABELS: Record<SpendBlock['source'], string> = {
  agent: '代理',
  scheduled: '定时任务',
}

/** 页面上可切换的两种范围；缺省是近 7 天（接口不传 `since`）。 */
export type SpendRange = '7d' | '30d'

export const SPEND_RANGE_DAYS: Record<SpendRange, number> = { '7d': 7, '30d': 30 }

/** 消耗日的起点小时（北京时间），与后端 `SPEND_DAY_RESET_HOUR` 一致。 */
const SPEND_DAY_RESET_HOUR = 4

const dayFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function dayPart(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((part) => part.type === type)?.value ?? ''
}

/** 当前的消耗日（`YYYY-MM-DD`）：把时间回拨 4 小时后取北京时间的日历日。 */
export function spendDayToday(now: Date = new Date()): string {
  const shifted = new Date(now.getTime() - SPEND_DAY_RESET_HOUR * 3_600_000)
  const parts = dayFormat.formatToParts(shifted)
  return `${dayPart(parts, 'year')}-${dayPart(parts, 'month')}-${dayPart(parts, 'day')}`
}

/** 近 `days` 天（含今天）的起始消耗日，作为 `since` 参数。 */
export function spendSinceDate(days: number, now: Date = new Date()): string {
  const start = new Date(`${spendDayToday(now)}T00:00:00Z`)
  start.setUTCDate(start.getUTCDate() - (days - 1))
  return start.toISOString().slice(0, 10)
}

const clockFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** 拦截记录的钟点（`HH:MM`，北京时间）；读不出时原样显示。 */
export function formatSpendClock(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : clockFormat.format(parsed)
}

/** 今日一种资源的用量文案：`4 / 5`，不限时 `3（不限）`。 */
export function spendUsageText(item: SpendKindUsage): string {
  return item.limit < 0 ? `${item.used}（不限）` : `${item.used} / ${item.limit}`
}

/** 用量接近上限的阈值；与「待你处理」的进入条件同口径（§4.6）。 */
export const SPEND_NEAR_RATIO = 0.8

/** 进度条颜色：到顶 danger、八成为 warning，其余 primary。 */
export function spendUsageTone(item: SpendKindUsage): 'primary' | 'warning' | 'danger' {
  if (item.limit <= 0) return 'primary'
  if (item.used >= item.limit) return 'danger'
  return item.used >= Math.ceil(item.limit * SPEND_NEAR_RATIO) ? 'warning' : 'primary'
}

/** 一条拦截记录：`20:00 定时任务 · 理智药 需要 3，已计 4 / 5`。 */
export function spendBlockText(block: SpendBlock): string {
  const source = SPEND_SOURCE_LABELS[block.source] ?? block.source
  const kind = SPEND_KIND_LABELS[block.kind] ?? block.kind
  return `${formatSpendClock(block.at)} ${source} · ${kind} 需要 ${block.requested}，已计 ${block.used} / ${block.limit}`
}

export async function fetchSpendSummary(since?: string, signal?: AbortSignal): Promise<SpendSummary> {
  const { data, error, response } = await api.GET('/api/agent/spend', {
    params: { query: since === undefined ? {} : { since } },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/**
 * 消耗汇总：60 秒重取一次（§4.6）。`since` 缺省走接口默认的近 7 天；
 * 近 30 天显式带上起始消耗日，两份缓存各占一个 key。
 */
export function spendQueryOptions(since?: string) {
  return queryOptions({
    queryKey: keys.agent.spend(since),
    queryFn: ({ signal }) => fetchSpendSummary(since, signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  })
}

/** 立即生成简报：不受一天一张限制，前端在请求进行中禁用按钮（§4.5.13）。 */
export async function runBrief(): Promise<BriefRunResult> {
  const { data, error, response } = await api.POST('/api/agent/brief/run')
  if (error || !data) throw toApiError(error, response)
  return data
}

/**
 * `POST /api/agent/brief/run`。成功后新工单要出现在工单列表里，简报产物也会
 * 多一份，所以一起失效这两片缓存；失败原因交给调用方弹 Toast。
 */
export function useRunBrief() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: runBrief,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agent.tickets.lists() })
      void queryClient.invalidateQueries({ queryKey: keys.agent.artifactsAll() })
    },
  })
}
