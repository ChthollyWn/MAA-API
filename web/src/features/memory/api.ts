import { queryOptions, type QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

export type MemoryItem = components['schemas']['MemoryOut']
export type MemoryKind = MemoryItem['kind']
export type MemoryStatus = MemoryItem['status']
export type MemoryReviewRequest = components['schemas']['MemoryReviewRequest']
export type MemoryUpdateRequest = components['schemas']['MemoryUpdateRequest']

/**
 * 记忆页的接口与展示规则（M79 规格 §4.3、§4.6）。
 *
 * `GET /api/agent/memories` 不传 `status` 返回全部状态：记忆页一次读全，客户端按
 * 「待审核 / 生效中 / 已归档」分组；待你处理另用一条 `status=proposed` 的查询，
 * 两个 key 都在 `keys.agent.memories()` 前缀下，`memory_proposed` 一次失效两处。
 */
export const MEMORY_PAGE_SIZE = 200

export const MEMORY_PATH = '/more/memories'

export const KIND_LABELS: Record<MemoryKind, string> = {
  semantic: '事实',
  episodic: '经历',
  procedural: '做法',
}

export const STATUS_LABELS: Record<MemoryStatus, string> = {
  proposed: '待审核',
  active: '生效中',
  rejected: '已拒绝',
  superseded: '已取代',
  expired: '已过期',
}

export type MemoryStatusTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'outline'

export function statusBadge(status: MemoryStatus): { label: string; tone: MemoryStatusTone } {
  const label = STATUS_LABELS[status] ?? status
  if (status === 'proposed') return { label, tone: 'warning' }
  if (status === 'active') return { label, tone: 'success' }
  return { label, tone: 'neutral' }
}

export interface MemoryGroups {
  pending: MemoryItem[]
  active: MemoryItem[]
  archived: MemoryItem[]
}

/** 三组：待审核 = proposed；生效中 = active；已归档 = rejected / superseded / expired。 */
export function groupMemories(items: readonly MemoryItem[]): MemoryGroups {
  return {
    pending: items.filter((item) => item.status === 'proposed'),
    active: items.filter((item) => item.status === 'active'),
    archived: items.filter(
      (item) => item.status === 'rejected' || item.status === 'superseded' || item.status === 'expired',
    ),
  }
}

export const memoryListQuery = queryOptions({
  queryKey: keys.agent.memoryList(),
  queryFn: async (): Promise<MemoryItem[]> => {
    const { data, error, response } = await api.GET('/api/agent/memories', {
      params: { query: { size: MEMORY_PAGE_SIZE } },
    })
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  staleTime: 15_000,
})

export const attentionMemoriesQuery = queryOptions({
  queryKey: keys.agent.attentionMemories(),
  queryFn: async (): Promise<MemoryItem[]> => {
    const { data, error, response } = await api.GET('/api/agent/memories', {
      params: { query: { status: 'proposed', size: MEMORY_PAGE_SIZE } },
    })
    if (error || !data) throw toApiError(error, response)
    return data.items
  },
  staleTime: 15_000,
})

export async function reviewMemory(memoryId: string, body: MemoryReviewRequest): Promise<MemoryItem> {
  const { data, error, response } = await api.POST('/api/agent/memories/{memory_id}/review', {
    params: { path: { memory_id: memoryId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function updateMemory(memoryId: string, body: MemoryUpdateRequest): Promise<MemoryItem> {
  const { data, error, response } = await api.PUT('/api/agent/memories/{memory_id}', {
    params: { path: { memory_id: memoryId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/agent/memories/{memory_id}', {
    params: { path: { memory_id: memoryId } },
  })
  if (error) throw toApiError(error, response)
}

/** 审核、修改、删除后让记忆页与待你处理的待审核清单一起失效。 */
export function invalidateMemories(queryClient: QueryClient): Promise<unknown> {
  return queryClient.invalidateQueries({ queryKey: keys.agent.memories() })
}

const GENERIC_MESSAGES = new Set(['与当前状态冲突', '参数校验失败', '资源不存在'])
const CODE_TEXT: Record<string, string> = {
  MEMORY_NOT_FOUND: '这条记忆不存在，可能已经在别处被删掉了。',
  MEMORY_NOT_PROPOSED: '这条记忆已经不在待审核状态，刷新后再试。',
}

export function describeMemoryError(error: unknown): string {
  const failure = toApiError(error)
  if (failure.status === undefined && !failure.code) return failure.message || '操作失败，请稍后重试。'
  const message = failure.message.trim()
  if (message && !GENERIC_MESSAGES.has(message) && !/^请求(参数)?校验失败/.test(message)) return message
  return (failure.code ? CODE_TEXT[failure.code] : undefined) ?? message ?? '操作失败，请稍后重试。'
}

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' })

/** 库里是 UTC 的 `...Z`；展示与 `<input type="datetime-local">` 都用浏览器本地时区。 */
export function formatMemoryTime(value: string | null | undefined): string {
  if (!value) return '长期有效'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date)
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** `...Z` → `YYYY-MM-DDTHH:mm`（datetime-local 的本地时区写法）；空值给空串。 */
export function toDateTimeLocal(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** datetime-local 的本地时区写法 → UTC ISO；空串表示清空（`null`）。 */
export function fromDateTimeLocal(value: string): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}
