import type { components } from '@/types/api'
import {
  filterSensitive,
  operationParameters,
  resolveSchema,
  schemaExample,
  type ConsoleOperation,
  type ConsoleOperationGroup,
  type ConsoleRecord,
  type describeResponse,
  type QueryDraft,
} from './utils'

export type OpenApiDocument = Record<string, unknown>
export type ApiSnippet = components['schemas']['ApiSnippetView']
export type ApiSnippetWrite = components['schemas']['ApiSnippetWrite']
export type ResponseView = ReturnType<typeof describeResponse> & { imageUrl?: string; bodyText: string; requestId: string; url: string }
export type ConsoleHistoryEntry = ConsoleRecord & {
  id: string
  method: string
  path: string
  path_params: Record<string, unknown>
  query: Record<string, unknown>
  headers: Record<string, string>
  body?: string
  status?: number
  elapsed_ms?: number
  timestamp: number
  request_id: string
}
export type SavedRequest = Pick<ApiSnippet, 'path_params' | 'query' | 'headers' | 'body'> | ConsoleHistoryEntry
export type TreeTab = 'interfaces' | 'favorites' | 'history'
export type DrawerSize = 'peek' | 'half' | 'full'
export type DrawerView = 'response' | 'logs'
export interface ConsoleNotice {
  tone: 'info' | 'success' | 'warning' | 'danger'
  text: string
}

export const STATUS_LABELS: Record<number, string> = {
  200: 'OK', 201: 'Created', 202: 'Accepted', 204: 'No Content', 301: 'Moved Permanently',
  302: 'Found', 304: 'Not Modified', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
  404: 'Not Found', 409: 'Conflict', 422: 'Unprocessable Entity', 429: 'Too Many Requests',
  500: 'Internal Server Error', 502: 'Bad Gateway', 503: 'Service Unavailable', 504: 'Gateway Timeout',
}

export function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export function storage(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try { return typeof window === 'undefined' ? null : window.localStorage }
  catch { return null }
}

export function apiBase(baseUrl: string): string {
  const origin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
  return baseUrl.trim() || origin
}

export function snippetsBase(baseUrl: string): string {
  const pageOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
  try { return new URL(apiBase(baseUrl), pageOrigin).origin }
  catch { return 'invalid-base-url://invalid' }
}

export const fetchThroughGlobal: typeof fetch = (input, init) => globalThis.fetch(input, init)

export function responseLabel(response: Pick<ResponseView, 'status' | 'statusText'>): string {
  if (response.status <= 0) return '网络错误'
  return `${response.status} ${response.statusText || STATUS_LABELS[response.status] || 'HTTP Response'}`
}

export function bodySchema(operation: ConsoleOperation | null, document: OpenApiDocument | undefined): Record<string, unknown> | null {
  if (!operation) return null
  const requestBody = resolveSchema(recordOf(operation.operation.requestBody), document)
  const content = recordOf(requestBody.content)
  const jsonContent = recordOf(content['application/json'] ?? content['application/*+json'])
  const schema = recordOf(jsonContent.schema)
  return Object.keys(schema).length ? resolveSchema(schema, document) : null
}

export function parameterSchema(parameter: Record<string, unknown>, document: OpenApiDocument | undefined): Record<string, unknown> {
  return resolveSchema(recordOf(parameter.schema), document)
}

export function operationDraft(operation: ConsoleOperation, document?: OpenApiDocument, saved?: SavedRequest) {
  const parameters = operationParameters(operation.operation, document)
  const pathParams: Record<string, string> = {}
  const query: Record<string, QueryDraft> = {}
  for (const parameter of parameters) {
    const name = typeof parameter.name === 'string' ? parameter.name : ''
    if (!name) continue
    const schema = parameterSchema(parameter, document)
    const location = parameter.in
    const savedValue = location === 'path'
      ? saved?.path_params[name]
      : location === 'query' ? saved?.query[name] : undefined
    if (location === 'path') {
      const defaultValue = savedValue ?? schema.default ?? schema.example
      pathParams[name] = defaultValue == null ? '' : String(defaultValue)
    } else if (location === 'query') {
      const enabled = Object.prototype.hasOwnProperty.call(saved?.query ?? {}, name) || parameter.required === true
      const defaultValue = savedValue ?? schema.default ?? ''
      query[name] = { enabled, value: defaultValue == null ? '' : String(defaultValue) }
    }
  }
  const schema = bodySchema(operation, document)
  const body = saved && saved.body !== null && saved.body !== undefined
    ? typeof saved.body === 'string' ? saved.body : JSON.stringify(saved.body, null, 2)
    : schema ? JSON.stringify(schemaExample(schema, 0, document), null, 2) : ''
  const savedHeaders = filterSensitive({ headers: { ...(saved?.headers ?? {}) } }).headers ?? {}
  return { pathParams, query, headers: savedHeaders, body }
}

export function findOperation(groups: ConsoleOperationGroup[], method: string, path: string): ConsoleOperation | null {
  return groups.flatMap((group) => group.operations).find((operation) => operation.method === method.toUpperCase() && operation.path === path) ?? null
}

/** 历史记录存的是填好路径参数后的实际路径（如 `/api/items/item-1`），按路径模板找回对应接口。 */
export function findOperationForPath(groups: ConsoleOperationGroup[], method: string, path: string): ConsoleOperation | null {
  const exact = findOperation(groups, method, path)
  if (exact) return exact
  return groups.flatMap((group) => group.operations).find((operation) => {
    if (operation.method !== method.toUpperCase() || !operation.path.includes('{')) return false
    const pattern = operation.path.split(/\{[^}]+\}/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+')
    return new RegExp(`^${pattern}$`).test(path)
  }) ?? null
}

export function enabledQuery(query: Record<string, QueryDraft>): Record<string, string> {
  return Object.fromEntries(Object.entries(query).filter(([, item]) => item.enabled).map(([key, item]) => [key, item.value]))
}

export function toSnippet(operation: ConsoleOperation, name: string, pathParams: Record<string, string>, query: Record<string, QueryDraft>, headers: Record<string, string>, body: string): ApiSnippetWrite {
  let parsedBody: unknown = null
  try { if (body) parsedBody = JSON.parse(body) as unknown } catch { parsedBody = body }
  return {
    name: name.trim(),
    method: operation.method as ApiSnippetWrite['method'],
    path: operation.path,
    path_params: { ...pathParams },
    query: filterSensitive({ query: enabledQuery(query) }).query,
    headers: filterSensitive({ headers }).headers,
    body: parsedBody,
  }
}

export function normalizeStoredHeaders(headers: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(headers).filter(([key, value]) => key.trim() && value.trim()).map(([key, value]) => [key.trim(), value]))
}

export function responseBodyText(body: unknown): string {
  if (typeof body === 'string') return body
  if (body === undefined || body === null) return ''
  return JSON.stringify(body, null, 2)
}

/** 按两个空格缩进重排 JSON；不是合法 JSON 时返回 null。编辑器的格式化快捷键与「格式化」按钮共用。 */
export function formatJson(source: string): string | null {
  try { return JSON.stringify(JSON.parse(source), null, 2) }
  catch { return null }
}
