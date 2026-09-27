import { callerKindLabel, riskLabel, statusLabel } from '@/features/audit/types'
import type { operations } from '@/types/api'

export const AUDIT_PAGE_SIZE = 20

export type AuditQuery = NonNullable<operations['agent_list_audits']['parameters']['query']>

export interface AuditFilters {
  caller: string
  tool_name: string
  status: string
  risk_level: string
  /** 日期控件的值 YYYY-MM-DD；地址里保存为该日 UTC 零点的 ISO 时间。 */
  since: string
}

export type AuditFilterKey = keyof AuditFilters

export const EMPTY_FILTERS: AuditFilters = {
  caller: '',
  tool_name: '',
  status: '',
  risk_level: '',
  since: '',
}

export function pageFromSearch(search: URLSearchParams): number {
  return Math.max(1, Number(search.get('page') ?? 1) || 1)
}

export function filtersFromSearch(search: URLSearchParams): AuditFilters {
  const since = search.get('since') ?? ''
  return {
    caller: search.get('caller') ?? '',
    tool_name: search.get('tool_name') ?? '',
    status: search.get('status') ?? '',
    risk_level: search.get('risk_level') ?? '',
    since: since ? since.slice(0, 10) : '',
  }
}

function sinceToIso(date: string): string | null {
  const parsed = new Date(`${date}T00:00:00Z`)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

/** 筛选写入地址参数；筛选一变就回到第一页，所以不带 page。 */
export function searchFromFilters(filters: AuditFilters): URLSearchParams {
  const next = new URLSearchParams()
  if (filters.caller) next.set('caller', filters.caller)
  if (filters.tool_name.trim()) next.set('tool_name', filters.tool_name.trim())
  if (filters.status) next.set('status', filters.status)
  if (filters.risk_level) next.set('risk_level', filters.risk_level)
  const since = filters.since ? sinceToIso(filters.since) : null
  if (since) next.set('since', since)
  return next
}

export function withoutFilter(search: URLSearchParams, key: AuditFilterKey): URLSearchParams {
  const next = new URLSearchParams(search)
  next.delete(key)
  next.delete('page')
  return next
}

export function withPage(search: URLSearchParams, page: number): URLSearchParams {
  const next = new URLSearchParams(search)
  if (page <= 1) next.delete('page')
  else next.set('page', String(page))
  return next
}

/** 列表接口的查询参数：直接取地址里的值（since 已是 ISO 时间）。 */
export function auditQuery(search: URLSearchParams, page: number): AuditQuery {
  const query: AuditQuery = { page, size: AUDIT_PAGE_SIZE }
  const caller = search.get('caller')
  const toolName = search.get('tool_name')
  const status = search.get('status')
  const risk = search.get('risk_level')
  const since = search.get('since')
  if (caller) query.caller = caller as AuditQuery['caller']
  if (toolName) query.tool_name = toolName
  if (status) query.status = status as AuditQuery['status']
  if (risk) query.risk_level = risk as AuditQuery['risk_level']
  if (since) query.since = since
  return query
}

export interface AuditCondition {
  key: AuditFilterKey
  label: string
}

export function filterConditions(filters: AuditFilters): AuditCondition[] {
  const conditions: AuditCondition[] = []
  if (filters.caller) conditions.push({ key: 'caller', label: `调用方：${callerKindLabel(filters.caller)}` })
  if (filters.tool_name) conditions.push({ key: 'tool_name', label: `工具名：${filters.tool_name}` })
  if (filters.status) conditions.push({ key: 'status', label: `状态：${statusLabel(filters.status)}` })
  if (filters.risk_level) conditions.push({ key: 'risk_level', label: `风险：${riskLabel(filters.risk_level)}` })
  if (filters.since) conditions.push({ key: 'since', label: `开始日期：${filters.since}` })
  return conditions
}
