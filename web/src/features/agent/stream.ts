import { toApiError } from '@/api/errors'
import { useAuth } from '@/stores/auth'

export function requestId(): string {
  return typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export function parseSseFrame(frame: string, onEvent: (name: string, data: unknown) => void): void {
  let name = 'message'
  const data: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') name = value
    if (field === 'data') data.push(value)
  }
  if (data.length === 0) return
  const raw = data.join('\n')
  try { onEvent(name, JSON.parse(raw) as unknown) }
  catch { onEvent(name, { message: raw }) }
}

/**
 * 发送一条消息并逐个回调 SSE 事件。非 2xx 抛出带错误码的 `ApiError`。
 * 断开连接不会停止服务端这一轮（后台 producer 会继续），所以这里只负责读流。
 */
export async function streamMessage(
  sessionId: string,
  content: string,
  signal: AbortSignal,
  onEvent: (name: string, data: unknown) => void,
): Promise<void> {
  const headers = new Headers({
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
    'X-Request-Id': requestId(),
  })
  const token = useAuth.getState().token
  if (token) headers.set('X-Token', token)
  const response = await globalThis.fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    credentials: 'same-origin',
    headers,
    body: JSON.stringify({ content }),
    signal,
  })
  if (!response.ok) {
    if (response.status === 401) useAuth.getState().onUnauthorized()
    let body: unknown
    try { body = await response.json() as unknown } catch { body = null }
    throw toApiError(body, response)
  }
  if (!response.body) throw new Error('服务没有返回可读取的 SSE 消息流。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    while (true) {
      const separator = /\r?\n\r?\n/.exec(buffer)
      if (!separator || separator.index === undefined) break
      parseSseFrame(buffer.slice(0, separator.index), onEvent)
      buffer = buffer.slice(separator.index + separator[0].length)
    }
  }
  buffer += decoder.decode()
  if (buffer.trim()) parseSseFrame(buffer, onEvent)
}
