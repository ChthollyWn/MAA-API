import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import type { components } from '@/types/api'

/**
 * 事件规则页的接口层（M81 规格 §4.3、§4.6）。
 *
 * 六个端点与 §4.3 一一对应。DST 与分页范围由后端校验，这里只把 `TRIGGER_RULE_INVALID`
 * 的 `details.field` 原样交给编辑器，让错误落在对应的字段上。
 */
export type TriggerRule = components['schemas']['TriggerRuleOut']
export type TriggerRuleList = components['schemas']['TriggerRuleList']
export type TriggerRuleWrite = components['schemas']['TriggerRuleWrite']
export type TriggerMode = components['schemas']['TriggerMode']
export type EventSource = components['schemas']['EventSource']
export type Observation = components['schemas']['ObservationOut']
export type ObservationPage = components['schemas']['ObservationPage']
export type ObservationVerdict = components['schemas']['ObservationVerdictIn']['verdict']
export type Trigger = components['schemas']['TriggerOut']
export type TriggerPage = components['schemas']['TriggerPage']
export type SpecSummary = components['schemas']['SpecSummary']

/** 列表每页条数；与事件记录、观察记录两个分页端点同口径。 */
export const TRIGGER_PAGE_SIZE = 20

export interface TriggerFilters {
  source?: string
  kind?: string
  rule_id?: string
}

export async function fetchTriggerRules(): Promise<TriggerRuleList> {
  const { data, error, response } = await api.GET('/api/agent/trigger-rules')
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function saveTriggerRule(ruleId: string, body: TriggerRuleWrite): Promise<TriggerRule> {
  const { data, error, response } = await api.PUT('/api/agent/trigger-rules/{rule_id}', {
    params: { path: { rule_id: ruleId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function deleteTriggerRule(ruleId: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/agent/trigger-rules/{rule_id}', {
    params: { path: { rule_id: ruleId } },
  })
  if (error) throw toApiError(error, response)
}

export async function fetchObservations(
  filters: { reviewed?: boolean } = {},
  page = 1,
  signal?: AbortSignal,
): Promise<ObservationPage> {
  const { data, error, response } = await api.GET('/api/agent/observations', {
    params: {
      query: {
        ...(filters.reviewed === undefined ? {} : { reviewed: filters.reviewed }),
        page,
        size: TRIGGER_PAGE_SIZE,
      },
    },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function reviewObservation(
  observationId: number,
  verdict: ObservationVerdict,
): Promise<Observation> {
  const { data, error, response } = await api.POST('/api/agent/observations/{observation_id}/verdict', {
    params: { path: { observation_id: observationId } },
    body: { verdict },
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function fetchTriggers(
  filters: TriggerFilters = {},
  page = 1,
  signal?: AbortSignal,
): Promise<TriggerPage> {
  const { data, error, response } = await api.GET('/api/agent/triggers', {
    params: {
      query: {
        ...(filters.source ? { source: filters.source } : {}),
        ...(filters.kind ? { kind: filters.kind } : {}),
        ...(filters.rule_id ? { rule_id: filters.rule_id } : {}),
        page,
        size: TRIGGER_PAGE_SIZE,
      },
    },
    signal,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

/** 执行代理下拉的数据源；调用方只保留 `kind=worker`。 */
export async function fetchAgentSpecs(): Promise<SpecSummary[]> {
  const { data, error, response } = await api.GET('/api/agent/specs')
  if (error) throw toApiError(error, response)
  return data?.items ?? []
}

/** 编辑器里能标错的字段名；与服务端 `details.field` 的映射见下表。 */
export type RuleField =
  | 'id'
  | 'title'
  | 'source'
  | 'kindPattern'
  | 'conditions'
  | 'mode'
  | 'worker'
  | 'policy'
  | 'cooldown'
  | 'cron'

/** 服务端的字段名 → 表单字段名（`rule_id` 对新建规则的 id 输入框）。 */
const FIELD_MAP: Record<string, RuleField> = {
  rule_id: 'id',
  title: 'title',
  source: 'source',
  kind_pattern: 'kindPattern',
  conditions: 'conditions',
  mode: 'mode',
  worker: 'worker',
  policy: 'policy',
  cooldown_seconds: 'cooldown',
  cron: 'cron',
}

function detailsOf(error: ApiError): Record<string, unknown> | null {
  return error.details && typeof error.details === 'object' && !Array.isArray(error.details)
    ? (error.details as Record<string, unknown>)
    : null
}

/**
 * `TRIGGER_RULE_INVALID` 的字段错误：返回 `details.field` 对应的表单字段与提示。
 * 其它错误（422 形状错误、网络错误）返回 null，由调用方弹 Toast。
 */
export function triggerRuleFieldError(error: unknown): { field: RuleField; message: string } | null {
  const failure = toApiError(error)
  if (failure.code !== 'TRIGGER_RULE_INVALID') return null
  const details = detailsOf(failure)
  const raw = details?.field
  if (typeof raw !== 'string' || raw.length === 0) return null
  const field = FIELD_MAP[raw]
  if (!field) return null
  return { field, message: failure.message || '事件规则不合法' }
}

/** 规则的错误提示：优先服务端 message，空时给一句兜底。 */
export function describeTriggerError(error: unknown): string {
  const failure = toApiError(error)
  return failure.message.trim() || '操作失败，请稍后重试。'
}
