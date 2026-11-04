import { ApiError, toApiError } from '@/api/errors'
import { useAuth } from '@/stores/auth'

/** 连接层失败（fetch 拒绝、读流中断）的统一文案：浏览器的原文（如 `network error`）不能给用户看。 */
export const NETWORK_ERROR_MESSAGE = '网络连接中断，请检查网络后重试。'

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

/** 连接层异常统一包成 `ApiError`；主动 abort 保持原样，由调用方按「用户停止」处理。 */
function toNetworkError(error: unknown, signal: AbortSignal): unknown {
  if (signal.aborted) return error
  return new ApiError({ message: NETWORK_ERROR_MESSAGE, cause: error })
}

/** `detach`：服务端已受理但本端不读流（排队消息），拿到 2xx 后取消响应体。 */
export interface StreamSseOptions {
  detach?: boolean
}

/**
 * 向 `path` 发一条 JSON 请求体并逐个回调 SSE 事件。非 2xx 抛出带错误码的 `ApiError`。
 * 断开连接不会停止服务端这一轮（后台 producer 会继续），所以这里只负责读流。
 * 连接层失败（fetch 被拒或读流中断）也抛 `ApiError`，把浏览器自己的错误原文挡在界面之外。
 */
export async function streamSse(
  path: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (name: string, data: unknown) => void,
  options: StreamSseOptions = {},
): Promise<void> {
  const headers = new Headers({
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
    'X-Request-Id': requestId(),
  })
  const token = useAuth.getState().token
  if (token) headers.set('X-Token', token)
  let response: Response
  try {
    response = await globalThis.fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body: JSON.stringify(body),
      signal,
    })
  } catch (error) {
    throw toNetworkError(error, signal)
  }
  if (!response.ok) {
    if (response.status === 401) useAuth.getState().onUnauthorized()
    let errorBody: unknown
    try { errorBody = await response.json() as unknown } catch { errorBody = null }
    throw toApiError(errorBody, response)
  }
  if (options.detach) {
    await response.body?.cancel()
    return
  }
  if (!response.body) throw new Error('服务没有返回可读取的 SSE 消息流。')
  try {
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
  } catch (error) {
    throw toNetworkError(error, signal)
  }
}
