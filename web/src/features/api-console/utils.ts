export type ConsoleRecord = Record<string, unknown> & { truncated?: boolean }
export const HISTORY_STORAGE_KEY = 'maa.api-console.history'
export const HISTORY_MAX_ENTRIES = 100
export const HISTORY_MAX_BYTES = 16 * 1024
const HISTORY_MAX_ID_BYTES = 256
const CREDENTIAL_HEADER = /authorization|cookie|(?:^|[-_])token(?:$|[-_])|api[-_]?key|password|(?:^|[-_])secret(?:$|[-_])/i

const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'] as const

export interface ConsoleOperation {
  method: string
  path: string
  summary: string
  operationId: string
  tag: string
  operation: Record<string, unknown>
}

export interface ConsoleOperationGroup {
  name: string
  description: string
  operations: ConsoleOperation[]
}

export type JsonSchema = Record<string, unknown>

function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export function resolveSchema(schema: JsonSchema, document: unknown, depth = 0): JsonSchema {
  if (depth > 10) return schema
  const root = recordOf(document)
  if (typeof schema.$ref === 'string' && schema.$ref.startsWith('#/')) {
    const target = schema.$ref.slice(2).split('/').map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'))
      .reduce<unknown>((value, key) => recordOf(value)[key], root)
    if (target && typeof target === 'object' && !Array.isArray(target)) {
      const merged = { ...recordOf(target), ...schema }
      delete merged.$ref
      return resolveSchema(merged, document, depth + 1)
    }
  }
  const result: JsonSchema = { ...schema }
  if (schema.properties && typeof schema.properties === 'object') {
    result.properties = Object.fromEntries(Object.entries(recordOf(schema.properties)).map(([key, child]) => [
      key,
      child && typeof child === 'object' && !Array.isArray(child) ? resolveSchema(child as JsonSchema, document, depth + 1) : child,
    ]))
  }
  if (schema.items && typeof schema.items === 'object' && !Array.isArray(schema.items)) {
    result.items = resolveSchema(schema.items as JsonSchema, document, depth + 1)
  }
  if (Array.isArray(schema.allOf)) {
    const merged: JsonSchema = { ...result, properties: { ...recordOf(result.properties) } }
    const required = new Set(Array.isArray(merged.required) ? merged.required as string[] : [])
    for (const item of schema.allOf) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) continue
      const resolved = resolveSchema(item as JsonSchema, document, depth + 1)
      Object.assign(merged, resolved)
      Object.assign(merged.properties as object, recordOf(resolved.properties))
      if (Array.isArray(resolved.required)) for (const field of resolved.required) if (typeof field === 'string') required.add(field)
    }
    if (required.size) merged.required = [...required]
    return merged
  }
  return result
}

export function operationParameters(operation: Record<string, unknown>, document?: unknown): Array<Record<string, unknown>> {
  return Array.isArray(operation.parameters) ? operation.parameters.map((parameter) => resolveSchema(recordOf(parameter), document)) : []
}

function encodedBytes(value: string): number {
  return new TextEncoder().encode(value).length
}

function truncateUtf8(value: string, maxBytes: number): string {
  if (encodedBytes(value) <= maxBytes) return value
  const suffix = '…'
  let result = ''
  for (const character of value) {
    if (encodedBytes(result + character + suffix) > maxBytes) break
    result += character
  }
  return `${result}${suffix}`
}

export function buildOperationTree(document: unknown): ConsoleOperationGroup[] {
  const root = recordOf(document)
  const tags = Array.isArray(root.tags) ? root.tags.map(recordOf) : []
  const descriptions = new Map(tags.flatMap((tag) => typeof tag.name === 'string'
    ? [[tag.name, typeof tag.description === 'string' ? tag.description : ''] as const]
    : []))
  const groups = new Map<string, ConsoleOperation[]>()
  for (const [path, rawPathItem] of Object.entries(recordOf(root.paths))) {
    const pathItem = recordOf(rawPathItem)
    for (const method of HTTP_METHODS) {
      const operation = recordOf(pathItem[method])
      if (!pathItem[method]) continue
      const operationTags = Array.isArray(operation.tags) ? operation.tags.filter((tag): tag is string => typeof tag === 'string') : []
      const groupNames = operationTags.length > 0 ? operationTags : ['其他']
      const mergedOperation = {
        ...operation,
        ...(Array.isArray(pathItem.parameters) || Array.isArray(operation.parameters)
          ? { parameters: [...(Array.isArray(pathItem.parameters) ? pathItem.parameters : []), ...(Array.isArray(operation.parameters) ? operation.parameters : [])] }
          : {}),
      }
      for (const tag of groupNames) {
        const items = groups.get(tag) ?? []
        items.push({
          method: method.toUpperCase(),
          path,
          summary: typeof operation.summary === 'string' ? operation.summary : '',
          operationId: typeof operation.operationId === 'string' ? operation.operationId : '',
          tag,
          operation: mergedOperation,
        })
        groups.set(tag, items)
      }
    }
  }
  const tagOrder = new Map(tags.flatMap((tag, index) => typeof tag.name === 'string' ? [[tag.name, index] as const] : []))
  return [...groups.entries()]
    .sort(([left], [right]) => (tagOrder.get(left) ?? Number.MAX_SAFE_INTEGER) - (tagOrder.get(right) ?? Number.MAX_SAFE_INTEGER) || left.localeCompare(right))
    .map(([name, operations]) => ({
      name,
      description: descriptions.get(name) ?? '',
      operations: operations.sort((left, right) => left.path.localeCompare(right.path) || left.method.localeCompare(right.method)),
    }))
}

export function searchOperations(groups: ConsoleOperationGroup[], query: string): ConsoleOperation[] {
  const normalized = query.trim().toLocaleLowerCase()
  return groups.flatMap((group) => group.operations).filter((operation) => !normalized ||
    [operation.path, operation.summary, operation.operationId].some((field) => field.toLocaleLowerCase().includes(normalized)))
}

export function relativeTimeLabel(timestamp: number, now = Date.now()): string {
  if (!Number.isFinite(timestamp)) return '时间未知'
  const delta = now - timestamp
  if (delta < -30_000) return '即将'
  const seconds = Math.max(0, delta / 1000)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} 小时前`
  return `${Math.floor(seconds / 86_400)} 天前`
}

export function pipelineTemplateFromBody(body: unknown): Array<Record<string, unknown>> {
  let parsed = body
  if (typeof body === 'string') {
    try { parsed = JSON.parse(body) as unknown } catch { return [] }
  }
  const root = recordOf(parsed)
  const nested = recordOf(root.pipeline)
  const tasks = Array.isArray(root.tasks) ? root.tasks : Array.isArray(nested.tasks) ? nested.tasks : []
  return tasks.filter((task): task is Record<string, unknown> =>
    Boolean(task && typeof task === 'object' && !Array.isArray(task) && typeof (task as Record<string, unknown>).name === 'string'))
}

export interface QueryDraft {
  enabled: boolean
  value: string
}

export function buildRequestUrl(request: {
  baseUrl: string
  path: string
  pathParams?: Record<string, unknown>
  query?: Record<string, QueryDraft>
}): string {
  const path = request.path.replace(/\{([^}]+)\}/g, (_match, key: string) => {
    const value = request.pathParams?.[key]
    if (value === undefined || value === null || String(value) === '') throw new Error(`请填写路径参数 ${key}`)
    return encodeURIComponent(String(value))
  })
  const base = request.baseUrl.trim() || (typeof window === 'undefined' ? 'http://localhost' : window.location.origin)
  const url = new URL(path, base)
  for (const [key, value] of Object.entries(request.query ?? {})) {
    if (value.enabled) url.searchParams.append(key, value.value)
  }
  return url.toString()
}

export function optionalFieldDescriptions(input: JsonSchema, document?: unknown): Array<{ path: string; description: string }> {
  const found: Array<{ path: string; description: string }> = []
  const visit = (current: JsonSchema, prefix: string, depth: number) => {
    if (depth > 8) return
    const resolved = resolveSchema(current, document)
    const properties = recordOf(resolved.properties)
    const required = new Set(Array.isArray(resolved.required) ? resolved.required.filter((name): name is string => typeof name === 'string') : [])
    for (const [key, rawChild] of Object.entries(properties)) {
      const child = recordOf(rawChild)
      const path = prefix ? `${prefix}.${key}` : key
      if (!required.has(key) && typeof child.description === 'string' && child.description.trim()) {
        found.push({ path, description: child.description })
      }
      visit(child, path, depth + 1)
    }
  }
  visit(input, '', 0)
  return found
}

export function bodyDiagnosticRanges(raw: string, errors: Array<{ path: string; message: string }>): Array<{ from: number; to: number; message: string; severity: 'error' }> {
  try { JSON.parse(raw) } catch { return [] }

  const endOfString = (start: number): number => {
    let index = start + 1
    while (index < raw.length) {
      if (raw[index] === '\\') { index += 2; continue }
      if (raw[index++] === '"') break
    }
    return index
  }
  const skipWhitespace = (start: number): number => {
    let index = start
    while (/\s/.test(raw[index] ?? '')) index += 1
    return index
  }
  const rangeForPath = (segments: string[], start: number, depth = 0): { from: number; to: number } | null => {
    if (depth > 16 || segments.length === 0) return null
    const valueStart = skipWhitespace(start)
    const first = segments[0]
    const last = segments.length === 1
    const primitiveEnd = (from: number): number => {
      const initial = raw[from]
      if (initial === '"') return endOfString(from)
      if (initial !== '{' && initial !== '[') {
        let end = from
        while (end < raw.length && !/[\s,}\]]/.test(raw[end])) end += 1
        return end
      }
      const close = initial === '{' ? '}' : ']'
      let nesting = 0
      let inString = false
      let end = from
      for (; end < raw.length; end += 1) {
        const char = raw[end]
        if (char === '"' && raw[end - 1] !== '\\') inString = !inString
        if (inString) continue
        if (char === initial) nesting += 1
        if (char === close && --nesting === 0) return end + 1
      }
      return raw.length
    }

    if (raw[valueStart] === '{') {
      let index = skipWhitespace(valueStart + 1)
      while (index < raw.length && raw[index] !== '}') {
        const keyStart = index
        const keyEnd = endOfString(keyStart)
        let key = ''
        try { key = JSON.parse(raw.slice(keyStart, keyEnd)) as string } catch { return null }
        index = skipWhitespace(keyEnd)
        if (raw[index] !== ':') return null
        index = skipWhitespace(index + 1)
        if (key === first) {
          if (last) return { from: index, to: primitiveEnd(index) }
          return rangeForPath(segments.slice(1), index, depth + 1)
        }
        index = skipWhitespace(primitiveEnd(index))
        if (raw[index] === ',') index = skipWhitespace(index + 1)
        else if (raw[index] !== '}') return null
      }
      return null
    }

    if (raw[valueStart] === '[') {
      let index = skipWhitespace(valueStart + 1)
      let item = 0
      while (index < raw.length && raw[index] !== ']') {
        const itemStart = index
        if (String(item) === first) {
          if (last) return { from: itemStart, to: primitiveEnd(itemStart) }
          return rangeForPath(segments.slice(1), itemStart, depth + 1)
        }
        index = skipWhitespace(primitiveEnd(itemStart))
        if (raw[index] === ',') index = skipWhitespace(index + 1)
        else if (raw[index] !== ']') return null
        item += 1
      }
    }
    return null
  }

  return errors.flatMap(({ path, message }) => {
    if (!path) return []
    const range = rangeForPath(path.split('.').filter(Boolean), 0)
    return range ? [{ ...range, to: Math.max(range.from + 1, range.to), message, severity: 'error' as const }] : []
  })
}

export function consoleWebSocketUrl(baseUrl: string, token?: string | null, pageOrigin?: string): string {
  const base = baseUrl.trim() || pageOrigin || (typeof window === 'undefined' ? 'http://localhost' : window.location.origin)
  const url = new URL('/api/ws', base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  if (token?.trim()) url.searchParams.set('token', token.trim())
  return url.toString()
}

export function describeResponse(input: {
  status: number
  statusText?: string
  elapsedMs: number
  headers: Headers | Record<string, string>
  body: unknown
}): {
  status: number
  statusText: string
  tone: ReturnType<typeof responseTone>
  elapsedMs: number
  serverElapsedMs: number | null
  headers: Record<string, string>
  body: unknown
  errorCard: { code: string; message: string; details: Record<string, unknown> } | null
} {
  const header = (key: string) => input.headers instanceof Headers
    ? input.headers.get(key)
    : Object.entries(input.headers).find(([name]) => name.toLowerCase() === key.toLowerCase())?.[1] ?? null
  const serverTime = Number(header('x-response-time-ms'))
  const body = recordOf(input.body)
  const error = recordOf(body.error)
  const errorCard = typeof error.code === 'string' && typeof error.message === 'string'
    ? { code: error.code, message: error.message, details: recordOf(error.details) }
    : null
  return {
    status: input.status,
    statusText: input.statusText ?? '',
    tone: responseTone(input.status),
    elapsedMs: input.elapsedMs,
    serverElapsedMs: Number.isFinite(serverTime) && header('x-response-time-ms') ? serverTime : null,
    headers: Object.fromEntries(input.headers instanceof Headers ? input.headers.entries() : Object.entries(input.headers)),
    body: input.body,
    errorCard,
  }
}

export function filterSensitive<T extends { path?: string; headers?: Record<string, string>; query?: Record<string, unknown> }>(value: T): T {
  let path = value.path
  if (path) {
    try {
      const url = new URL(path, 'http://local')
      for (const key of [...url.searchParams.keys()]) if (key.toLowerCase() === 'token') url.searchParams.delete(key)
      path = /^https?:\/\//i.test(path) ? url.toString() : `${url.pathname}${url.search}${url.hash}`
    } catch { /* retain malformed paths; request validation will explain them */ }
  }
  const headers = Object.fromEntries(Object.entries(value.headers ?? {}).filter(([key]) => !CREDENTIAL_HEADER.test(key.trim())))
  const query = value.query
    ? Object.fromEntries(Object.entries(value.query).filter(([key]) => key.toLowerCase() !== 'token'))
    : undefined
  return { ...value, ...(path ? { path } : {}), headers, ...(query ? { query } : {}) } as T
}

export function trimHistory<T extends ConsoleRecord>(records: T[]): T[] {
  return records.slice(-HISTORY_MAX_ENTRIES).reverse().map((record) => {
    const clean = filterSensitive(record as T & { path?: string; headers?: Record<string, string> }) as T
    const cleanRecord = clean as Record<string, unknown>
    if ('id' in cleanRecord && encodedBytes(JSON.stringify(cleanRecord.id) ?? '') > HISTORY_MAX_ID_BYTES) {
      cleanRecord.id = typeof cleanRecord.id === 'string'
        ? truncateUtf8(cleanRecord.id, HISTORY_MAX_ID_BYTES)
        : '[已截断]'
      clean.truncated = true
    }
    if (encodedBytes(JSON.stringify(clean)) <= HISTORY_MAX_BYTES) return clean
    const trimmed = { ...clean, truncated: true } as T
    for (const field of ['body', 'response', 'headers'] as const) {
      if (field in trimmed) (trimmed as Record<string, unknown>)[field] = '[已截断]'
      if (encodedBytes(JSON.stringify(trimmed)) <= HISTORY_MAX_BYTES) break
    }
    while (encodedBytes(JSON.stringify(trimmed)) > HISTORY_MAX_BYTES) {
      const largest = Object.entries(trimmed).filter(([key]) => key !== 'id' && key !== 'truncated')
        .sort((left, right) => encodedBytes(JSON.stringify(right[1])) - encodedBytes(JSON.stringify(left[1])))[0]
      if (!largest) break
      const [key, value] = largest
      if (typeof value === 'string' && value.length > 64) {
        ;(trimmed as Record<string, unknown>)[key] = value.slice(0, Math.floor(value.length / 2)) + '…'
      } else {
        delete (trimmed as Record<string, unknown>)[key]
      }
    }
    return trimmed
  })
}

export function saveHistory(storage: Pick<Storage, 'setItem'> | null, records: ConsoleRecord[]): void {
  if (!storage) return
  try {
    storage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(trimHistory(records)))
  } catch {
    // Storage can be disabled or full; an in-memory history should still work.
  }
}

export function loadHistory(storage: Pick<Storage, 'getItem'> | null): ConsoleRecord[] {
  if (!storage) return []
  try {
    const stored = storage.getItem(HISTORY_STORAGE_KEY)
    if (!stored) return []
    const parsed: unknown = JSON.parse(stored)
    if (!Array.isArray(parsed)) return []
    return trimHistory(parsed.slice(0, HISTORY_MAX_ENTRIES).reverse().map((entry) => recordOf(entry)))
  } catch {
    return []
  }
}

export function makeCurl(request: { baseUrl: string; method: string; path: string; body?: string; headers?: Record<string, string>; token?: string; includeToken?: boolean }): string {
  const cleanPath = filterSensitive({ path: request.path }).path ?? request.path
  const url = /^https?:\/\//i.test(cleanPath)
    ? cleanPath
    : `${request.baseUrl.replace(/\/$/, '')}${cleanPath}` || cleanPath
  const explicitAuth = Object.entries(request.headers ?? {}).find(([key]) => /^(authorization|x-token)$/i.test(key.trim()))
  const headers = Object.entries(request.headers ?? {}).filter(([key]) => !CREDENTIAL_HEADER.test(key.trim()))
  const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`
  const authName = explicitAuth?.[0].trim() ?? (request.token ? 'Authorization' : '')
  const authValue = explicitAuth?.[1] ?? (request.token ? `Bearer ${request.token}` : '')
  const authScheme = authName.toLowerCase() === 'authorization' ? authValue.match(/^(\S+)\s+/)?.[1] : undefined
  const exportedAuthValue = request.includeToken ? authValue : authScheme ? `${authScheme} $MAA_TOKEN` : '$MAA_TOKEN'
  const auth = authName
    ? request.includeToken
      ? `-H ${quote(`${authName}: ${exportedAuthValue}`)}`
      : `-H "${authName}: ${exportedAuthValue}"`
    : ''
  const body = request.body ? `--data-raw ${quote(request.body)}` : ''
  const contentType = request.body && !headers.some(([key]) => key.toLowerCase() === 'content-type') ? '-H "Content-Type: application/json"' : ''
  return `# export MAA_TOKEN=...\ncurl -X ${request.method.toUpperCase()} ${quote(url)} ${auth} ${contentType} ${headers.map(([key, value]) => `-H ${quote(`${key}: ${value}`)}`).join(' ')} ${body}`.trim()
}

export function responseTone(status: number): 'success' | 'neutral' | 'warning' | 'danger' {
  if (status <= 0) return 'danger'
  if (status < 300) return 'success'
  if (status < 400) return 'neutral'
  if (status < 500) return 'warning'
  return 'danger'
}

export function selectLogs<T extends Record<string, unknown>>(logs: T[], requestId: string, pipelineId?: string | null): T[] {
  return logs.filter((log) => log.request_id === requestId || Boolean(pipelineId && log.pipeline_id === pipelineId))
}

export function schemaExample(input: Record<string, unknown> = {}, depth = 0, document?: unknown): unknown {
  const schema = resolveSchema(input, document)
  if (Array.isArray(schema.examples) && schema.examples.length > 0) return schema.examples[0]
  if ('example' in schema) return schema.example
  if ('default' in schema) return schema.default
  if (Array.isArray(schema.enum)) return schema.enum[0]
  const branches = Array.isArray(schema.anyOf) ? schema.anyOf : Array.isArray(schema.oneOf) ? schema.oneOf : null
  if (branches) {
    const branch = branches.find((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return false
      const type = (item as Record<string, unknown>).type
      return type !== 'null'
    })
    if (branch && typeof branch === 'object' && !Array.isArray(branch)) return schemaExample(branch as Record<string, unknown>, depth + 1, document)
  }
  if (depth > 5) return null
  if (schema.type === 'object' || schema.properties) {
    const required = Array.isArray(schema.required) ? schema.required : []
    return Object.fromEntries(Object.entries((schema.properties ?? {}) as Record<string, Record<string, unknown>>).filter(([key]) => required.includes(key)).map(([key, child]) => [key, schemaExample(child, depth + 1, document)]))
  }
  if (schema.type === 'array') {
    if (schema.items && typeof schema.items === 'object' && !Array.isArray(schema.items) && depth < 2) {
      const example = schemaExample(schema.items as Record<string, unknown>, depth + 1, document)
      return Object.keys(recordOf(example)).length ? [example] : []
    }
    return []
  }
  if (schema.type === 'integer' || schema.type === 'number') return 0
  if (schema.type === 'boolean') return false
  return ''
}
