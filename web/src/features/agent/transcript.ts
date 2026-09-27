import { isRecord, parseJson, stringValue } from './format'
import type { AgentMessage, JsonRecord, ToolTrace } from './types'

export type ToolStatus = 'running' | 'awaiting' | 'done' | 'failed' | 'missing'

/** 一次工具调用的展示数据：历史里由调用与结果两条消息配成，本轮由流事件累积。 */
export interface ToolView {
  name: string
  arguments?: unknown
  /** 工具返回体（`{ result }` 或 `{ error }`）；还没有结果时为 undefined。 */
  result?: unknown
  status: ToolStatus
  errorMessage?: string
  auditId?: number
  /** 只有结果、对应的调用不在已加载消息里（在更早的页）时单独显示。 */
  standalone: boolean
}

export type TranscriptEntry =
  | { kind: 'user'; key: string; message: AgentMessage }
  | { kind: 'assistant'; key: string; message: AgentMessage }
  | { kind: 'tool'; key: string; tool: ToolView }

function errorMessageOf(result: unknown): string | undefined {
  if (!isRecord(result) || !result.error) return undefined
  if (typeof result.error === 'string') return result.error
  return isRecord(result.error) ? stringValue(result.error.message, '工具调用失败') : '工具调用失败'
}

function resultStatus(result: unknown): Pick<ToolView, 'status' | 'errorMessage'> {
  const errorMessage = errorMessageOf(result)
  return errorMessage === undefined ? { status: 'done' } : { status: 'failed', errorMessage }
}

/** 把历史消息排成展示条目：工具调用与它的结果合成一条，结果消息不再单独出现。 */
export function buildTranscript(messages: readonly AgentMessage[]): TranscriptEntry[] {
  const results = new Map<string, AgentMessage>()
  for (const message of messages) {
    if (message.role === 'tool' && message.tool_call_id) results.set(message.tool_call_id, message)
  }
  const paired = new Set<number>()
  const entries: TranscriptEntry[] = []
  for (const message of messages) {
    if (message.role === 'tool') {
      if (paired.has(message.id)) continue
      const result = parseJson(message.content)
      entries.push({
        kind: 'tool',
        key: `result-${message.id}`,
        tool: { name: message.tool_name ?? '调用', result, ...resultStatus(result), auditId: message.audit_id ?? undefined, standalone: true },
      })
      continue
    }
    if (message.role === 'user') {
      entries.push({ kind: 'user', key: `message-${message.id}`, message })
      continue
    }
    if (message.content?.trim()) entries.push({ kind: 'assistant', key: `message-${message.id}`, message })
    message.tool_calls?.forEach((call, index) => {
      const resultMessage = call.id ? results.get(call.id) : undefined
      if (resultMessage) paired.add(resultMessage.id)
      const result = resultMessage ? parseJson(resultMessage.content) : undefined
      entries.push({
        kind: 'tool',
        key: `call-${message.id}-${call.id ?? index}`,
        tool: {
          name: call.function?.name || '未知工具',
          arguments: parseJson(call.function?.arguments),
          result,
          ...(resultMessage ? resultStatus(result) : { status: 'missing' as const }),
          auditId: resultMessage?.audit_id ?? undefined,
          standalone: false,
        },
      })
    })
  }
  return entries
}

/** 流事件里的结果带着调用编号、消息编号等字段；只取工具返回体，与历史里的工具消息一致。 */
function toolBody(data: unknown): unknown {
  if (!isRecord(data) || !('result' in data || 'error' in data)) return data
  const body: JsonRecord = {}
  if ('result' in data) body.result = data.result
  if ('error' in data) body.error = data.error
  return body
}

export function liveToolView(trace: ToolTrace, running: boolean): ToolView {
  const base = { name: trace.name, arguments: trace.arguments, auditId: trace.auditId, standalone: false }
  if (trace.result !== undefined) {
    const result = toolBody(trace.result)
    return { ...base, result, ...resultStatus(result) }
  }
  if (trace.confirmationId) return { ...base, status: 'awaiting' }
  return { ...base, status: running ? 'running' : 'missing' }
}
