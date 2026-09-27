import type { StatusTone } from '@/components/ui/badge'

export interface AuditRecord {
  audit_id: number
  caller: string
  caller_detail: string | null
  session_id: string | null
  tool_name: string
  arguments: Record<string, unknown>
  result_summary: string | null
  result_ref: Record<string, unknown> | null
  status: string
  error_code: string | null
  risk_level: string
  forced: boolean
  confirmation_id: string | null
  authorized_by_id: string | null
  duration_ms: number
  created_at: string
  request_id: string | null
}

export interface AuditPageResult {
  items: AuditRecord[]
  total: number
  page: number
  size: number
}

export const CALLER_OPTIONS = [
  { value: 'rest', label: 'REST' },
  { value: 'internal', label: '内置 Agent' },
] as const

export const STATUS_OPTIONS = [
  { value: 'pending', label: '等待确认' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'expired', label: '已过期' },
] as const

export const RISK_OPTIONS = [
  { value: 'none', label: '无额外风险' },
  { value: 'consume', label: '资源消耗' },
  { value: 'destructive', label: '破坏性操作' },
] as const

function optionLabel(options: readonly { value: string; label: string }[], value: string): string {
  return options.find((option) => option.value === value)?.label ?? value
}

export function callerKindLabel(value: string): string {
  return optionLabel(CALLER_OPTIONS, value)
}

export function callerText(record: Pick<AuditRecord, 'caller' | 'caller_detail'>): string {
  const kind = callerKindLabel(record.caller)
  return record.caller_detail ? `${kind}（${record.caller_detail}）` : kind
}

export function statusLabel(value: string): string {
  return optionLabel(STATUS_OPTIONS, value)
}

export function statusTone(value: string): StatusTone {
  if (value === 'pending') return 'warning'
  if (value === 'success') return 'success'
  if (value === 'failed' || value === 'rejected' || value === 'expired') return 'danger'
  return 'neutral'
}

export function riskLabel(value: string): string {
  if (value === 'consume') return '资源消耗'
  if (value === 'destructive') return '破坏性操作'
  return '无额外风险'
}

export function riskTone(value: string): StatusTone {
  if (value === 'consume') return 'warning'
  if (value === 'destructive') return 'danger'
  return 'neutral'
}

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
})

export function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date)
}

export function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

export function pipelineIdOf(record: Pick<AuditRecord, 'result_ref'>): string | null {
  const value = record.result_ref?.pipeline_id
  return typeof value === 'string' && value ? value : null
}
