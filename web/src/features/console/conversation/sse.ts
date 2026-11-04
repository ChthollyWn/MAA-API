import { isRecord, parseQuestionOptions, stringValue } from '@/features/agent/format'
import type { AgentQuestionPrompt } from '@/features/agent/types'
import type { HumanAction, Origin } from './types'

/** SSE v3 的归一事件（M72 §4.5.1）；服务端名字与字段照 04 §3。 */
export type SseV3Event =
  | { type: 'run_start'; runId: string; parentRunId: string | null; agent: string; kind: string; ticketId: string | null }
  | { type: 'text_delta'; runId: string; text: string }
  | { type: 'tool_start'; runId: string; toolCallId: string; name: string; arguments: unknown }
  | { type: 'tool_result'; runId: string; toolCallId: string; name: string; auditId: number | null; raw: Record<string, unknown> }
  | { type: 'confirmation_required'; runId: string; toolCallId: string; confirmationId: string; origin: Origin | null }
  | { type: 'question_required'; runId: string; toolCallId: string; question: AgentQuestionPrompt; origin: Origin | null }
  | { type: 'subagent_start'; runId: string; parentRunId: string; agent: string; task: string }
  | { type: 'subagent_end'; runId: string; status: string; summary: string }
  | { type: 'report'; runId: string; ticketId: string; reportId: number; status: string; summary: string; humanAction: HumanAction | null }
  | { type: 'ticket_created'; runId: string | null; ticketId: string; title: string }
  | { type: 'interrupted'; runId: string }
  | { type: 'run_end'; runId: string; finishReason: string }
  | { type: 'error'; code: string; message: string }
  | { type: 'unknown'; name: string }

/** 错误事件的兜底文案（服务端漏了 message 时）。 */
export const SSE_ERROR_FALLBACK = '本轮回复失败，请重试。'

function nullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function nullableAuditId(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 确认/提问的 origin（M71 §4.2）：是对象才解析，缺的字段写 null。 */
export function toOrigin(value: unknown): Origin | null {
  if (!isRecord(value)) return null
  return {
    ticket_id: nullableString(value.ticket_id),
    ticket_title: nullableString(value.ticket_title),
    agent: nullableString(value.agent),
    run_id: nullableString(value.run_id),
  }
}

const HUMAN_ACTION_TYPES: ReadonlySet<string> = new Set(['confirmation', 'question', 'decision'])

/** 汇报/工单 wire 里的 `human_action`；认不出的类型当作没有。 */
export function toHumanAction(value: unknown): HumanAction | null {
  if (!isRecord(value)) return null
  const type = stringValue(value.type)
  if (!HUMAN_ACTION_TYPES.has(type)) return null
  return { type: type as HumanAction['type'], prompt: stringValue(value.prompt), ref_id: nullableString(value.ref_id) }
}

function toQuestion(data: Record<string, unknown>): AgentQuestionPrompt {
  return {
    question_id: stringValue(data.question_id),
    question: stringValue(data.question),
    options: parseQuestionOptions(data.options),
    multiple: data.multiple === true,
    expires_at: stringValue(data.expires_at),
  }
}

/**
 * 把一条 SSE v3 帧归一成前端事件（M72 §4.5.1）。
 *
 * - `data` 不是对象按 `{}` 处理；字符串缺省为空串，数字不是 number 时按类型给 `null` 或 `0`。
 * - 除 `ticket_created`、`error` 外，缺 `run_id` 的事件一律归成 `unknown`：前端靠运行 id
 *   把事件放进正确的实时副本，猜归属比丢弃更糟。
 * - 名字不认识（含 M74/M75 的新事件）也归成 `unknown`，由 reducer 原样忽略。
 */
export function toSseV3Event(name: string, data: unknown): SseV3Event {
  const record = isRecord(data) ? data : {}
  const runId = stringValue(record.run_id)
  if (name !== 'ticket_created' && name !== 'error' && !runId) return { type: 'unknown', name }

  switch (name) {
    case 'run_start':
      return {
        type: 'run_start',
        runId,
        parentRunId: nullableString(record.parent_run_id),
        agent: stringValue(record.agent),
        kind: stringValue(record.kind),
        ticketId: nullableString(record.ticket_id),
      }
    case 'text_delta':
      return { type: 'text_delta', runId, text: stringValue(record.text) }
    case 'tool_start':
      return {
        type: 'tool_start',
        runId,
        toolCallId: stringValue(record.tool_call_id),
        name: stringValue(record.name),
        arguments: record.arguments,
      }
    case 'tool_result':
      return {
        type: 'tool_result',
        runId,
        toolCallId: stringValue(record.tool_call_id),
        name: stringValue(record.name),
        auditId: nullableAuditId(record.audit_id),
        raw: record,
      }
    case 'confirmation_required':
      return {
        type: 'confirmation_required',
        runId,
        toolCallId: stringValue(record.tool_call_id),
        confirmationId: stringValue(record.confirmation_id),
        origin: toOrigin(record.origin),
      }
    case 'question_required':
      return {
        type: 'question_required',
        runId,
        toolCallId: stringValue(record.tool_call_id),
        question: toQuestion(record),
        origin: toOrigin(record.origin),
      }
    case 'subagent_start':
      return {
        type: 'subagent_start',
        runId,
        parentRunId: stringValue(record.parent_run_id),
        agent: stringValue(record.agent),
        task: stringValue(record.task),
      }
    case 'subagent_end':
      return { type: 'subagent_end', runId, status: stringValue(record.status), summary: stringValue(record.summary) }
    case 'report':
      return {
        type: 'report',
        runId,
        ticketId: stringValue(record.ticket_id),
        reportId: typeof record.report_id === 'number' && Number.isFinite(record.report_id) ? record.report_id : 0,
        status: stringValue(record.status),
        summary: stringValue(record.summary),
        humanAction: toHumanAction(record.human_action),
      }
    case 'ticket_created':
      return {
        type: 'ticket_created',
        runId: nullableString(record.run_id),
        ticketId: stringValue(record.ticket_id),
        title: stringValue(record.title),
      }
    case 'interrupted':
      return { type: 'interrupted', runId }
    case 'run_end':
      return { type: 'run_end', runId, finishReason: stringValue(record.finish_reason) }
    case 'error':
      return {
        type: 'error',
        code: stringValue(record.code),
        message: stringValue(record.message) || SSE_ERROR_FALLBACK,
      }
    default:
      return { type: 'unknown', name }
  }
}
