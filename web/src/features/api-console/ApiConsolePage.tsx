import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BookOpen, ChevronDown, ChevronUp, Copy, Heart, Plus, RefreshCw, Send, Trash2, Wifi, WifiOff } from 'lucide-react'
import { useNavigate } from 'react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import createClient from 'openapi-fetch'
import { toApiError } from '@/api/errors'
import type { components, paths } from '@/types/api'
import { useAuth } from '@/stores/auth'
import { GuideMarkdown } from './GuideMarkdown'
import { useConsoleRealtime } from './use-console-realtime'
import {
  buildOperationTree,
  buildRequestUrl,
  describeResponse,
  filterSensitive,
  loadHistory,
  makeCurl,
  operationParameters,
  optionalFieldDescriptions,
  pipelineTemplateFromBody,
  resolveSchema,
  relativeTimeLabel,
  saveHistory,
  schemaExample,
  searchOperations,
  type ConsoleOperation,
  type ConsoleOperationGroup,
  type ConsoleRecord,
  type QueryDraft,
} from './utils'

type OpenApiDocument = Record<string, unknown>
type ApiSnippet = components['schemas']['ApiSnippetView']
type ApiSnippetWrite = components['schemas']['ApiSnippetWrite']
type ResponseView = ReturnType<typeof describeResponse> & { imageUrl?: string; bodyText: string; requestId: string; url: string }
type WorkbenchTab = 'request' | 'response' | 'logs'
type TreeTab = 'interfaces' | 'favorites' | 'history'
type ConsoleHistoryEntry = ConsoleRecord & {
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

const JsonEditor = lazy(() => import('./JsonEditor').then(({ JsonEditor: Editor }) => ({ default: Editor })))

const STATUS_LABELS: Record<number, string> = {
  200: 'OK', 201: 'Created', 202: 'Accepted', 204: 'No Content', 301: 'Moved Permanently',
  302: 'Found', 304: 'Not Modified', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
  404: 'Not Found', 409: 'Conflict', 422: 'Unprocessable Entity', 429: 'Too Many Requests',
  500: 'Internal Server Error', 502: 'Bad Gateway', 503: 'Service Unavailable', 504: 'Gateway Timeout',
}

function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function storage(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try { return typeof window === 'undefined' ? null : window.localStorage }
  catch { return null }
}

function apiBase(baseUrl: string): string {
  const origin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
  return baseUrl.trim() || origin
}

function snippetsBase(baseUrl: string): string {
  const pageOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
  try { return new URL(apiBase(baseUrl), pageOrigin).origin }
  catch { return 'invalid-base-url://invalid' }
}

const fetchThroughGlobal: typeof fetch = (input, init) => globalThis.fetch(input, init)

function responseLabel(response: ResponseView): string {
  return `${response.status} ${response.statusText || STATUS_LABELS[response.status] || 'HTTP Response'}`
}

function methodTone(method: string): string {
  const tones: Record<string, string> = {
    GET: 'bg-blue-500/10 text-blue-700 dark:text-blue-300', POST: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    PUT: 'bg-amber-500/10 text-amber-700 dark:text-amber-300', DELETE: 'bg-red-500/10 text-red-700 dark:text-red-300',
    PATCH: 'bg-violet-500/10 text-violet-700 dark:text-violet-300', HEAD: 'bg-slate-500/10 text-slate-700 dark:text-slate-300',
  }
  return tones[method] ?? 'bg-muted text-muted-foreground'
}

function toneClasses(tone: unknown): string {
  if (tone === 'success') return 'border-positive/40 bg-positive/5 text-positive'
  if (tone === 'neutral') return 'border-border bg-muted/40 text-muted-foreground'
  if (tone === 'warning') return 'border-warning/40 bg-warning/5 text-warning'
  return 'border-destructive/40 bg-destructive/5 text-destructive'
}

function bodySchema(operation: ConsoleOperation | null, document: OpenApiDocument | undefined): Record<string, unknown> | null {
  if (!operation) return null
  const requestBody = resolveSchema(recordOf(operation.operation.requestBody), document)
  const content = recordOf(requestBody.content)
  const jsonContent = recordOf(content['application/json'] ?? content['application/*+json'])
  const schema = recordOf(jsonContent.schema)
  return Object.keys(schema).length ? resolveSchema(schema, document) : null
}

function parameterSchema(parameter: Record<string, unknown>, document: OpenApiDocument | undefined): Record<string, unknown> {
  return resolveSchema(recordOf(parameter.schema), document)
}

function operationDraft(operation: ConsoleOperation, document?: OpenApiDocument, saved?: Pick<ApiSnippet, 'path_params' | 'query' | 'headers' | 'body'> | ConsoleHistoryEntry) {
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

function findOperation(groups: ConsoleOperationGroup[], method: string, path: string): ConsoleOperation | null {
  return groups.flatMap((group) => group.operations).find((operation) => operation.method === method.toUpperCase() && operation.path === path) ?? null
}

function toSnippet(operation: ConsoleOperation, name: string, pathParams: Record<string, string>, query: Record<string, QueryDraft>, headers: Record<string, string>, body: string): ApiSnippetWrite {
  let parsedBody: unknown = null
  try { if (body) parsedBody = JSON.parse(body) as unknown } catch { parsedBody = body }
  return {
    name: name.trim(),
    method: operation.method as ApiSnippetWrite['method'],
    path: operation.path,
    path_params: { ...pathParams },
    query: filterSensitive({ query: Object.fromEntries(Object.entries(query).filter(([, item]) => item.enabled).map(([key, item]) => [key, item.value])) }).query,
    headers: filterSensitive({ headers }).headers,
    body: parsedBody,
  }
}

function operationFromSnippet(groups: ConsoleOperationGroup[], snippet: ApiSnippet): ConsoleOperation | null {
  return findOperation(groups, snippet.method, snippet.path)
}

function normalizeStoredHeaders(headers: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(headers).filter(([key, value]) => key.trim() && value.trim()))
}

function responseBodyText(body: unknown): string {
  if (typeof body === 'string') return body
  if (body === undefined || body === null) return ''
  return JSON.stringify(body, null, 2)
}

function useNarrowViewport() {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768)
  useEffect(() => {
    const update = () => setNarrow(window.innerWidth < 768)
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  return narrow
}

function useConsoleOpenApi(baseUrl: string, token: string | null) {
  return useQuery({
    queryKey: ['api-console', 'openapi', baseUrl.trim()],
    queryFn: async () => {
      const url = new URL('/openapi.json', apiBase(baseUrl))
      const headers = new Headers()
      if (token) headers.set('Authorization', `Bearer ${token}`)
      const sameOrigin = typeof window !== 'undefined' && url.origin === window.location.origin
      const response = await fetch(url, { headers, credentials: sameOrigin ? 'same-origin' : 'omit' })
      if (!response.ok) throw new Error(`读取 OpenAPI 失败：HTTP ${response.status}`)
      return response.json() as Promise<OpenApiDocument>
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}

function InterfaceList({
  groups, search, setSearch, selected, onSelect, onRefresh, refreshing, error,
}: {
  groups: ConsoleOperationGroup[]
  search: string
  setSearch: (value: string) => void
  selected: ConsoleOperation | null
  onSelect: (operation: ConsoleOperation) => void
  onRefresh: () => void
  refreshing: boolean
  error: unknown
}) {
  const matches = useMemo(() => searchOperations(groups, search), [groups, search])
  return <div className="flex h-full min-h-0 flex-col">
    <div className="space-y-3 border-b p-3">
      <div className="flex items-center justify-between gap-2">
        <div><h2 className="font-semibold">接口列表</h2><p className="text-xs text-muted-foreground">{matches.length} 个操作 · 来自 `/openapi.json`</p></div>
        <Button type="button" variant="outline" size="icon" aria-label="刷新 OpenAPI" disabled={refreshing} onClick={onRefresh}><RefreshCw className={refreshing ? 'animate-spin' : ''} aria-hidden="true" /></Button>
      </div>
      <Input aria-label="搜索接口" placeholder="搜索 path、summary、operationId" value={search} onChange={(event) => setSearch(event.target.value)} />
      {error ? <p role="alert" className="text-xs text-destructive">{error instanceof Error ? error.message : '读取 OpenAPI 失败'}</p> : null}
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      {matches.length === 0 ? <p className="p-4 text-center text-sm text-muted-foreground">{search ? '没有匹配的接口' : '接口目录为空'}</p> : groups.map((group) => {
        const operations = group.operations.filter((operation) => matches.includes(operation))
        if (!operations.length) return null
        return <details key={group.name} open className="group mb-1">
          <summary className="flex min-h-10 cursor-pointer list-none items-center justify-between gap-2 rounded-md px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground [&::-webkit-details-marker]:hidden">
            <span className="truncate" title={group.description || group.name}>{group.name}</span><span>{operations.length}</span>
          </summary>
          <ul className="space-y-1 pb-2">{operations.map((operation) => {
            const active = selected?.path === operation.path && selected.method === operation.method
            return <li key={`${operation.method}:${operation.path}`}>
              <button type="button" onClick={() => onSelect(operation)} aria-pressed={active} aria-label={`${operation.method} ${operation.path} ${operation.summary}`} className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${active ? 'border-primary/50 bg-primary/5' : 'border-transparent hover:border-border hover:bg-muted/50'}`}>
                <span className="flex items-start gap-2"><Badge className={`h-5 shrink-0 px-1.5 font-mono text-[10px] ${methodTone(operation.method)}`}>{operation.method}</Badge><span className="min-w-0 flex-1"><code className="block break-all text-xs font-semibold">{operation.path}</code>{operation.summary && <span className="mt-1 block line-clamp-2 text-xs text-muted-foreground">{operation.summary}</span>}</span></span>
              </button>
            </li>
          })}</ul>
        </details>
      })}
    </div>
  </div>
}

function ParameterInputs({
  operation, document, pathParams, setPathParams, query, setQuery,
}: {
  operation: ConsoleOperation
  document?: OpenApiDocument
  pathParams: Record<string, string>
  setPathParams: (value: Record<string, string>) => void
  query: Record<string, QueryDraft>
  setQuery: (value: Record<string, QueryDraft>) => void
}) {
  const parameters = operationParameters(operation.operation, document)
  const pathParameters = parameters.filter((parameter) => parameter.in === 'path')
  const queryParameters = parameters.filter((parameter) => parameter.in === 'query')
  const schema = (parameter: Record<string, unknown>) => parameterSchema(parameter, document)
  const type = (parameter: Record<string, unknown>) => String(schema(parameter).type ?? 'string')
  return <div className="space-y-4">
    {pathParameters.length > 0 && <section className="space-y-2" aria-label="路径参数"><h3 className="text-sm font-semibold">路径参数</h3>{pathParameters.map((parameter) => {
      const name = String(parameter.name)
      const fieldSchema = schema(parameter)
      return <div key={name} className="grid grid-cols-[minmax(0,1fr)_minmax(6rem,1fr)] items-center gap-3"><Label htmlFor={`path-${name}`} className="min-w-0 break-all text-xs">{name}<span className="ml-1 text-destructive">*</span>{typeof parameter.description === 'string' && <span className="mt-0.5 block font-normal text-muted-foreground">{parameter.description}</span>}</Label>{Array.isArray(fieldSchema.enum) ? <select id={`path-${name}`} className="min-h-10 min-w-0 rounded-md border bg-background px-2 text-sm" value={pathParams[name] ?? ''} onChange={(event) => setPathParams({ ...pathParams, [name]: event.target.value })}>{!parameter.required && <option value="">请选择</option>}{fieldSchema.enum.map((value) => <option key={String(value)} value={String(value)}>{String(value)}</option>)}</select> : <Input id={`path-${name}`} type={type(parameter) === 'integer' || type(parameter) === 'number' ? 'number' : 'text'} value={pathParams[name] ?? ''} onChange={(event) => setPathParams({ ...pathParams, [name]: event.target.value })} placeholder={fieldSchema.default == null ? '' : String(fieldSchema.default)} required />}</div>
    })}</section>}
    {queryParameters.length > 0 && <section className="space-y-2" aria-label="Query 参数"><h3 className="text-sm font-semibold">Query 参数</h3>{queryParameters.map((parameter) => {
      const name = String(parameter.name)
      const item = query[name] ?? { enabled: parameter.required === true, value: '' }
      const fieldSchema = schema(parameter)
      return <div key={name} className={`grid grid-cols-[1.4rem_minmax(0,1fr)] gap-x-2 rounded-lg border p-2 ${item.enabled ? 'bg-background' : 'bg-muted/30 opacity-75'}`}>
        <input aria-label={`启用 query 参数 ${name}`} type="checkbox" className="mt-1 size-4 accent-primary" checked={item.enabled} disabled={parameter.required === true} onChange={(event) => setQuery({ ...query, [name]: { ...item, enabled: event.target.checked } })} />
        <div className="min-w-0 space-y-1"><div className="flex flex-wrap items-baseline gap-x-2"><Label htmlFor={`query-${name}`} className="font-mono text-xs">{name}</Label>{parameter.required === true && <span className="text-[10px] text-destructive">必填</span>}{typeof parameter.description === 'string' && <span className="text-[10px] text-muted-foreground">{parameter.description}</span>}</div>
          <Input id={`query-${name}`} type={fieldSchema.type === 'integer' || fieldSchema.type === 'number' ? 'number' : 'text'} value={item.value} disabled={!item.enabled} onChange={(event) => setQuery({ ...query, [name]: { ...item, value: event.target.value } })} />
        </div>
      </div>
    })}</section>}
  </div>
}

function ErrorCard({ error }: { error: unknown }) {
  const item = recordOf(error)
  return <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><Badge variant="destructive" className="font-mono">{String(item.code)}</Badge><a href="/docs" className="text-xs text-primary underline underline-offset-2">查看错误码与 API 文档</a></div>
    <p className="mt-2 font-medium">{String(item.message)}</p>
    {Object.keys(recordOf(item.details)).length > 0 && <details className="mt-2"><summary className="cursor-pointer text-xs font-medium">展开 details</summary><pre className="mt-2 overflow-x-auto rounded bg-background p-2 text-xs">{JSON.stringify(item.details, null, 2)}</pre></details>}
  </div>
}

function ResponseContents({ response, openApi }: { response: ResponseView | null; openApi?: OpenApiDocument }) {
  if (!response) return <div className="grid min-h-44 place-items-center rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">发送一个请求后，这里会显示状态、耗时、响应头与响应体。</div>
  const body = response.body
  const error = response.errorCard
  const contentType = String(recordOf(response.headers)['content-type'] ?? '').toLowerCase()
  const isJson = contentType.includes('json') || typeof body === 'object' && body !== null || response.bodyText.trim().startsWith('{') || response.bodyText.trim().startsWith('[')
  return <div className="space-y-3">
    <div className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 ${toneClasses(response.tone)}`}>
      <span className="font-mono text-sm font-semibold">{responseLabel(response)}</span>
      <span className="text-xs">往返 {Number(response.elapsedMs).toFixed(1)} ms{typeof response.serverElapsedMs === 'number' ? ` · 服务端 ${response.serverElapsedMs.toFixed(1)} ms` : ''}</span>
    </div>
    {error ? <ErrorCard error={error} /> : null}
    {response.imageUrl ? <figure className="overflow-hidden rounded-lg border bg-black/5 p-2"><img src={response.imageUrl} alt="API 图片响应" className="mx-auto max-h-[42dvh] max-w-full object-contain" /><figcaption className="mt-2 text-center text-xs text-muted-foreground">{String(recordOf(response.headers)['content-type'] ?? '图片响应')}</figcaption></figure>
      : response.bodyText ? <details open className="rounded-lg border"><summary className="flex min-h-10 cursor-pointer items-center justify-between gap-2 px-3 text-xs font-semibold"><span>响应体</span><span className="font-normal text-muted-foreground">{isJson ? 'JSON' : 'text'}</span></summary><div className="border-t p-2">{isJson ? <Suspense fallback={<p className="p-3 text-xs text-muted-foreground">正在载入 JSON 查看器…</p>}><JsonEditor value={response.bodyText} schema={undefined} openApi={openApi} readOnly ariaLabel="只读 JSON 响应" /></Suspense> : <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/30 p-3 font-mono text-xs">{response.bodyText}</pre>}</div></details>
        : <p className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">响应没有内容。</p>}
    <details className="rounded-lg border"><summary className="min-h-10 cursor-pointer px-3 py-2 text-xs font-semibold">响应头 <span className="ml-1 font-normal text-muted-foreground">{Object.keys(recordOf(response.headers)).length}</span></summary><dl className="space-y-1 border-t p-3 text-xs">{Object.entries(recordOf(response.headers)).map(([key, value]) => <div key={key} className="grid grid-cols-[minmax(6rem,0.8fr)_minmax(0,1.4fr)] gap-2"><dt className="break-all font-mono text-muted-foreground">{key}</dt><dd className="break-all font-mono">{key.toLowerCase() === 'set-cookie' ? '••••••' : String(value)}</dd></div>)}</dl></details>
  </div>
}

function LogContents({ logs, timeline, requestId, connection }: {
  logs: Record<string, unknown>[]
  timeline: Array<{ type: string; data: Record<string, unknown>; at: number }>
  requestId: string | null
  connection: string
}) {
  const label: Record<string, string> = { core_status: '内核状态', device_status: '设备状态', pipeline_status: '流水线状态' }
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2 text-xs">{connection === 'CONNECTED' ? <Wifi aria-hidden="true" className="size-4 text-positive" /> : <WifiOff aria-hidden="true" className="size-4 text-muted-foreground" />}<span>专用 WebSocket：{connection === 'CONNECTED' ? '已连接' : connection === 'CONNECTING' ? '连接中' : '离线'}</span></div><span className="font-mono text-[10px] text-muted-foreground">request_id {requestId ?? '等待发送请求'}</span></div>
    <section className="space-y-2" aria-label="状态时间线"><h3 className="text-xs font-semibold">状态时间线</h3>{timeline.length === 0 ? <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">暂无状态变化；这条时间线仅在本调试台显示。</p> : <ol className="space-y-1 border-l pl-3">{timeline.slice(-6).map((item, index) => <li key={`${item.at}-${index}`} className="relative rounded bg-muted/40 p-2 text-xs"><span className="absolute -left-[1.05rem] top-3 size-2 rounded-full bg-primary" /><span className="font-medium">{label[item.type] ?? item.type}</span><code className="ml-2 break-all text-muted-foreground">{JSON.stringify(item.data)}</code></li>)}</ol>}</section>
    <section className="space-y-2" aria-label="服务端日志"><h3 className="text-xs font-semibold">服务端日志 <span className="font-normal text-muted-foreground">{logs.length}</span></h3>{logs.length === 0 ? <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">暂未收到 request_id 或关联 pipeline_id 的日志。</p> : <ol className="max-h-[40dvh] space-y-1 overflow-y-auto">{logs.map((record, index) => <li key={String(record.id ?? index)} className="rounded-lg border p-2 text-xs"><div className="flex flex-wrap justify-between gap-2"><span className="font-mono font-medium">{String(record.level ?? 'INFO')} · {String(record.source ?? 'server')}</span><span className="text-muted-foreground">{record.request_id ? 'request' : ''}{record.pipeline_id ? `pipeline ${record.pipeline_id}` : ''}</span></div><p className="mt-1 break-words">{String(record.content ?? record.message ?? JSON.stringify(record))}</p></li>)}</ol>}</section>
  </div>
}

function ApiConsolePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = useAuth((state) => state.token)
  const narrow = useNarrowViewport()
  const [baseUrl, setBaseUrl] = useState('')
  const [temporaryToken, setTemporaryToken] = useState('')
  const [tokenOverride, setTokenOverride] = useState(false)
  const [revealToken, setRevealToken] = useState(false)
  const [search, setSearch] = useState('')
  const [treeTab, setTreeTab] = useState<TreeTab>('interfaces')
  const [selected, setSelected] = useState<ConsoleOperation | null>(null)
  const [mobileStep, setMobileStep] = useState<'tree' | 'request'>('tree')
  const [mobileTab, setMobileTab] = useState<WorkbenchTab>('request')
  const [drawerSize, setDrawerSize] = useState<'peek' | 'half' | 'full'>('half')
  const [pathParams, setPathParams] = useState<Record<string, string>>({})
  const [query, setQuery] = useState<Record<string, QueryDraft>>({})
  const [headers, setHeaders] = useState<Record<string, string>>({})
  const [body, setBody] = useState('')
  const [requestId, setRequestId] = useState<string | null>(null)
  const [pipelineId, setPipelineId] = useState<string | null>(null)
  const [response, setResponse] = useState<ResponseView | null>(null)
  const [sending, setSending] = useState(false)
  const [history, setHistory] = useState<ConsoleHistoryEntry[]>(() => loadHistory(storage()) as ConsoleHistoryEntry[])
  const [favoriteName, setFavoriteName] = useState('')
  const [editingFavorite, setEditingFavorite] = useState<string | null>(null)
  const [curlIncludeToken, setCurlIncludeToken] = useState(false)
  const [notice, setNotice] = useState('')
  const imageUrlRef = useRef<string | null>(null)
  const drawerTouchStart = useRef<number | null>(null)

  const effectiveToken = tokenOverride ? temporaryToken.trim() || null : token
  const openApiQuery = useConsoleOpenApi(baseUrl, effectiveToken)
  const groups = useMemo(() => openApiQuery.data ? buildOperationTree(openApiQuery.data) : [], [openApiQuery.data])
  const snippetApi = useMemo(() => createClient<paths>({ baseUrl: snippetsBase(baseUrl), fetch: fetchThroughGlobal }), [baseUrl])
  const snippetHeaders = effectiveToken ? { 'X-Token': effectiveToken } : undefined
  const previousSnippetToken = useRef(effectiveToken)
  const snippetQuery = useQuery({
    queryKey: ['api-console', 'snippets', snippetsBase(baseUrl)],
    queryFn: async () => {
      const { data, error, response: resultResponse } = await snippetApi.GET('/api/snippets', {
        headers: snippetHeaders,
        credentials: 'same-origin',
      })
      if (error) throw toApiError(error, resultResponse)
      return data?.items ?? []
    },
    retry: false,
    staleTime: 30_000,
  })
  useEffect(() => {
    if (previousSnippetToken.current === effectiveToken) return
    previousSnippetToken.current = effectiveToken
    void snippetQuery.refetch()
  }, [effectiveToken, snippetQuery.refetch])
  const favorites = snippetQuery.data ?? []
  const selectedSchema = bodySchema(selected, openApiQuery.data)
  const optionalFields = useMemo(() => selectedSchema ? optionalFieldDescriptions(selectedSchema, openApiQuery.data) : [], [selectedSchema, openApiQuery.data])
  const socketToken = useMemo(() => {
    if (tokenOverride) return effectiveToken
    if (!effectiveToken) return null
    try {
      return new URL(apiBase(baseUrl)).origin === window.location.origin ? null : effectiveToken
    } catch { return effectiveToken }
  }, [baseUrl, effectiveToken, tokenOverride])
  const realtime = useConsoleRealtime(baseUrl, socketToken, requestId, pipelineId)

  useEffect(() => () => {
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
  }, [])

  const chooseOperation = (operation: ConsoleOperation, saved?: Pick<ApiSnippet, 'path_params' | 'query' | 'headers' | 'body'> | ConsoleHistoryEntry) => {
    setSelected(operation)
    const draft = operationDraft(operation, openApiQuery.data, saved)
    setPathParams(draft.pathParams)
    setQuery(draft.query)
    setHeaders(draft.headers)
    setBody(draft.body)
    setEditingFavorite(saved && 'id' in saved ? saved.id : null)
    setFavoriteName(saved && 'name' in saved && typeof saved.name === 'string' ? saved.name : '')
    setRequestId(null)
    setPipelineId(null)
    setResponse(null)
    setNotice('')
    setMobileStep('request')
    setMobileTab('request')
  }

  const saveHistoryEntry = (entry: ConsoleHistoryEntry) => {
    const next = [entry, ...history].slice(0, 100)
    setHistory(next)
    saveHistory(storage(), [...next].reverse())
  }

  const sendRequest = async () => {
    if (!selected || sending) return
    setNotice('')
    let url: URL
    try {
      url = new URL(buildRequestUrl({ baseUrl: apiBase(baseUrl), path: selected.path, pathParams, query }))
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '请求地址无效')
      return
    }
    const currentRequestId = randomId()
    setRequestId(currentRequestId)
    setPipelineId(null)
    setSending(true)
    const requestHeaders = new Headers(normalizeStoredHeaders(headers))
    requestHeaders.set('X-Request-Id', currentRequestId)
    const credentials = Object.keys(headers).some((key) => /^(authorization|x-token)$/i.test(key))
    if (!credentials && effectiveToken) requestHeaders.set('Authorization', `Bearer ${effectiveToken}`)
    const sendsBody = !['GET', 'HEAD'].includes(selected.method)
    if (sendsBody && body && !requestHeaders.has('Content-Type')) requestHeaders.set('Content-Type', 'application/json')
    const start = performance.now()
    try {
      const sameOrigin = url.origin === window.location.origin
      const result = await fetch(url, {
        method: selected.method,
        headers: requestHeaders,
        ...(sendsBody && body ? { body } : {}),
        credentials: sameOrigin ? 'same-origin' : 'omit',
      })
      const elapsedMs = Math.max(0, performance.now() - start)
      const contentType = result.headers.get('content-type') ?? ''
      let responseContent: unknown
      let responseText = ''
      let imageUrl: string | undefined
      if (contentType.toLowerCase().startsWith('image/')) {
        const blob = await result.blob()
        imageUrl = URL.createObjectURL(blob)
        responseContent = { content_type: contentType, size: blob.size }
      } else if (contentType.toLowerCase().includes('json')) {
        responseText = await result.text()
        try { responseContent = responseText ? JSON.parse(responseText) as unknown : null }
        catch { responseContent = responseText }
      } else {
        responseText = await result.text()
        responseContent = responseText
      }
      const details = describeResponse({ status: result.status, statusText: result.statusText || STATUS_LABELS[result.status], elapsedMs, headers: result.headers, body: responseContent })
      const pipelineValue = recordOf(responseContent).pipeline_id
      const nextPipeline = typeof pipelineValue === 'string' ? pipelineValue : null
      setPipelineId(nextPipeline)
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
      imageUrlRef.current = imageUrl ?? null
      setResponse({ ...details, bodyText: responseText || responseBodyText(responseContent), requestId: currentRequestId, url: url.toString(), ...(imageUrl ? { imageUrl } : {}) })
      setDrawerSize('half')
      setMobileTab('response')
      const historyUrl = new URL(url.toString())
      const historyEntry = filterSensitive({
        id: randomId(), method: selected.method, path: historyUrl.pathname,
        path_params: pathParams,
        query: Object.fromEntries(Object.entries(query).filter(([, item]) => item.enabled).map(([key, item]) => [key, item.value])),
        headers: normalizeStoredHeaders(headers),
        body: body || undefined,
        status: result.status,
        elapsed_ms: elapsedMs,
        timestamp: Date.now(),
        request_id: currentRequestId,
        response: contentType.toLowerCase().startsWith('image/') ? '[图片响应未保存]' : responseContent,
      }) as ConsoleHistoryEntry
      saveHistoryEntry(historyEntry)
    } catch (error) {
      const message = error instanceof Error ? error.message : '网络错误'
      setNotice(`请求失败：${message}`)
      const elapsedMs = Math.max(0, performance.now() - start)
      const errorBody = { error: { code: 'NETWORK_ERROR', message } }
      const details = describeResponse({ status: 0, statusText: 'Network Error', elapsedMs, headers: {}, body: errorBody })
      setResponse({ ...details, bodyText: JSON.stringify(errorBody, null, 2), requestId: currentRequestId, url: url.toString() })
      const historyEntry = filterSensitive({
        id: randomId(), method: selected.method, path: url.pathname,
        path_params: pathParams,
        query: Object.fromEntries(Object.entries(query).filter(([, item]) => item.enabled).map(([key, item]) => [key, item.value])),
        headers: normalizeStoredHeaders(headers),
        body: body || undefined,
        status: 0,
        elapsed_ms: elapsedMs,
        timestamp: Date.now(),
        request_id: currentRequestId,
        response: errorBody,
      }) as ConsoleHistoryEntry
      saveHistoryEntry(historyEntry)
      setDrawerSize('half')
      setMobileTab('response')
    } finally {
      setSending(false)
    }
  }

  const favoriteMutation = useMutation({
    mutationFn: async (input: { payload: ApiSnippetWrite; id: string | null }) => {
      if (input.id) {
        const result = await snippetApi.PUT('/api/snippets/{snippet_id}', {
          params: { path: { snippet_id: input.id } }, body: input.payload,
          headers: snippetHeaders, credentials: 'same-origin',
        })
        if (result.error) throw toApiError(result.error, result.response)
        return result.data
      }
      const result = await snippetApi.POST('/api/snippets', {
        body: input.payload, headers: snippetHeaders, credentials: 'same-origin',
      })
      if (result.error) throw toApiError(result.error, result.response)
      return result.data
    },
    onSuccess: async (item) => {
      setNotice('收藏已保存；鉴权凭据不会写入收藏。')
      setEditingFavorite(item?.id ?? null)
      setFavoriteName(item?.name ?? favoriteName)
      await queryClient.invalidateQueries({ queryKey: ['api-console', 'snippets'] })
    },
    onError: (error) => setNotice(`收藏保存失败：${toApiError(error).message}`),
  })
  const deleteFavoriteMutation = useMutation({
    mutationFn: async (id: string) => {
      const result = await snippetApi.DELETE('/api/snippets/{snippet_id}', {
        params: { path: { snippet_id: id } }, headers: snippetHeaders, credentials: 'same-origin',
      })
      if (result.error) throw toApiError(result.error, result.response)
    },
    onSuccess: async () => {
      setNotice('收藏已删除。')
      if (editingFavorite) { setEditingFavorite(null); setFavoriteName('') }
      await queryClient.invalidateQueries({ queryKey: ['api-console', 'snippets'] })
    },
    onError: (error) => setNotice(`收藏删除失败：${toApiError(error).message}`),
  })

  const selectFavorite = (favorite: ApiSnippet) => {
    const operation = operationFromSnippet(groups, favorite)
    if (!operation) {
      setNotice(`收藏目标 ${favorite.method} ${favorite.path} 已不在当前 OpenAPI 契约中。`)
      return
    }
    chooseOperation(operation, favorite)
  }

  const restoreHistory = (entry: ConsoleHistoryEntry) => {
    const operation = findOperation(groups, entry.method, entry.path)
    if (!operation) {
      setNotice(`历史目标 ${entry.method} ${entry.path} 已不在当前 OpenAPI 契约中。`)
      return
    }
    chooseOperation(operation, entry)
  }

  const currentUrl = useMemo(() => {
    if (!selected) return ''
    try { return buildRequestUrl({ baseUrl: apiBase(baseUrl), path: selected.path, pathParams, query }) }
    catch { return `${apiBase(baseUrl)}${selected.path}` }
  }, [baseUrl, selected, pathParams, query])

  const exportCurl = async () => {
    if (!selected) return
    const command = makeCurl({
      baseUrl: apiBase(baseUrl), method: selected.method, path: currentUrl, body,
      headers: normalizeStoredHeaders(headers), token: effectiveToken ?? undefined, includeToken: curlIncludeToken,
    })
    try {
      await navigator.clipboard.writeText(command)
      setNotice('cURL 命令已复制。默认使用 $MAA_TOKEN 占位符。')
    } catch {
      setNotice(command)
    }
  }

  const saveFavorite = () => {
    if (!selected) return
    if (!favoriteName.trim()) { setNotice('请先填写收藏名称。'); return }
    favoriteMutation.mutate({ payload: toSnippet(selected, favoriteName, pathParams, query, headers, body), id: editingFavorite })
  }

  const handoffSchedule = () => {
    if (!selected || selected.path !== '/api/pipelines') return
    const template = pipelineTemplateFromBody(body)
    if (template.length === 0) { setNotice('当前请求体没有可转入 schedule 的任务模板。'); return }
    navigate('/more/schedules', { state: { apiConsolePrefill: { template } } })
  }

  const handoffFavoriteSchedule = (favorite: ApiSnippet) => {
    const template = pipelineTemplateFromBody(favorite.body)
    if (template.length === 0) { setNotice('此收藏没有可转入 schedule 的任务模板。'); return }
    navigate('/more/schedules', { state: { apiConsolePrefill: { template } } })
  }

  const logContent = <LogContents logs={realtime.logs} timeline={realtime.timeline} requestId={requestId} connection={realtime.connection} />
  const responseContent = <ResponseContents response={response} openApi={openApiQuery.data} />

  const treePanel = <aside aria-label="接口列表" className="flex min-h-[30rem] min-w-0 flex-col overflow-hidden rounded-xl border bg-card">
    <div className="grid grid-cols-3 gap-1 border-b p-2" role="tablist" aria-label="请求资源">
      {([['interfaces', '接口'], ['favorites', '收藏'], ['history', '历史']] as const).map(([tab, label]) => <Button key={tab} type="button" size="sm" variant={treeTab === tab ? 'secondary' : 'ghost'} role="tab" aria-selected={treeTab === tab} onClick={() => setTreeTab(tab)}>{label}</Button>)}
    </div>
    {treeTab === 'interfaces' ? <InterfaceList groups={groups} search={search} setSearch={setSearch} selected={selected} onSelect={chooseOperation} onRefresh={() => void openApiQuery.refetch()} refreshing={openApiQuery.isFetching} error={openApiQuery.error} />
      : treeTab === 'favorites' ? <div className="min-h-0 flex-1 overflow-y-auto p-3" aria-label="已命名收藏"><div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">已命名收藏</h2><Button type="button" variant="ghost" size="icon" aria-label="刷新收藏" onClick={() => void snippetQuery.refetch()}><RefreshCw aria-hidden="true" /></Button></div>{snippetQuery.error ? <p role="alert" className="text-xs text-destructive">收藏读取失败：{toApiError(snippetQuery.error).message}</p> : null}{favorites.length === 0 ? <p className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">收藏保存在当前服务；在请求编辑器中命名并保存。</p> : <ul className="space-y-2">{favorites.map((favorite) => <li key={favorite.id} className="rounded-lg border p-2"><button type="button" onClick={() => selectFavorite(favorite)} className="block w-full text-left"><span className="block truncate text-sm font-medium">{favorite.name}</span><span className="mt-1 block truncate font-mono text-[10px] text-muted-foreground">{favorite.method} {favorite.path}</span></button><div className="mt-2 flex flex-wrap justify-end gap-1"><Button type="button" size="sm" variant="outline" onClick={() => selectFavorite(favorite)}>重放</Button>{favorite.path === '/api/pipelines' && <Button type="button" size="sm" variant="outline" onClick={() => handoffFavoriteSchedule(favorite)}>转为定时任务</Button>}<Button type="button" size="icon" variant="ghost" aria-label={`删除收藏 ${favorite.name}`} onClick={() => deleteFavoriteMutation.mutate(favorite.id)}><Trash2 aria-hidden="true" /></Button></div></li>)}</ul>}</div>
        : <div className="min-h-0 flex-1 overflow-y-auto p-3" aria-label="本机请求历史"><h2 className="mb-2 font-semibold">本机历史 <span className="text-xs font-normal text-muted-foreground">{history.length}/100</span></h2>{history.length === 0 ? <p className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">请求只在本机保存最多 100 条。</p> : <ul className="space-y-2">{history.map((entry) => <li key={entry.id}><button type="button" onClick={() => restoreHistory(entry)} className="w-full rounded-lg border p-2 text-left hover:bg-muted/40"><span className="flex items-center gap-2"><Badge className={`px-1.5 font-mono text-[10px] ${methodTone(entry.method)}`}>{entry.method}</Badge><code className="min-w-0 flex-1 truncate text-xs">{entry.path}</code><span className={`font-mono text-xs ${entry.status && entry.status >= 400 ? 'text-destructive' : 'text-positive'}`}>{entry.status ?? 'ERR'}</span></span><span className="mt-1 block text-[10px] text-muted-foreground">{relativeTimeLabel(entry.timestamp)} · {Number(entry.elapsed_ms ?? 0).toFixed(0)} ms{entry.truncated ? ' · 已截断' : ''}</span></button></li>)}</ul>}</div>}
    <div className="flex flex-wrap items-center gap-2 border-t p-3 text-xs"><a href="/docs" className="inline-flex items-center gap-1 text-primary underline underline-offset-2"><BookOpen className="size-3.5" aria-hidden="true" />OpenAPI 文档</a><span aria-hidden="true" className="text-muted-foreground">·</span><a href="/redoc" className="text-primary underline underline-offset-2">ReDoc</a></div>
  </aside>

  const requestPanel = <section aria-label="请求构造器" className="min-w-0 rounded-xl border bg-card">
    <div className="border-b p-3">
      <div className="flex items-start gap-2">
        {narrow && <Button type="button" variant="outline" size="icon" aria-label="返回接口列表" onClick={() => setMobileStep('tree')}><ArrowLeft aria-hidden="true" /></Button>}
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><Badge className={`font-mono ${methodTone(selected?.method ?? 'GET')}`}>{selected?.method ?? 'HTTP'}</Badge><code className="break-all text-sm font-semibold">{selected?.path ?? '选择一个接口'}</code></div><p className="mt-1 break-words text-xs text-muted-foreground">{selected?.summary ?? '从 OpenAPI 动态接口树中选择目标。'}</p></div>
      </div>
      {selected && <div className="mt-3 flex flex-wrap items-center gap-2">{!narrow && <Button type="button" onClick={() => void sendRequest()} disabled={sending} className="min-h-11"><Send aria-hidden="true" />{sending ? '发送中…' : '发送请求'}</Button>}<Button type="button" variant="outline" onClick={() => void exportCurl()}><Copy aria-hidden="true" />复制 cURL</Button>{selected.path === '/api/pipelines' && <Button type="button" variant="outline" onClick={handoffSchedule}>转为定时任务</Button>}</div>}
    </div>
    {selected ? <div className="space-y-4 p-3">
      <ParameterInputs operation={selected} document={openApiQuery.data} pathParams={pathParams} setPathParams={setPathParams} query={query} setQuery={setQuery} />
      <details className="rounded-lg border"><summary className="flex min-h-11 cursor-pointer items-center justify-between px-3 text-sm font-semibold">请求头 <span className="text-xs font-normal text-muted-foreground">Authorization 自动注入</span></summary><div className="space-y-3 border-t p-3"><div className="rounded-md bg-muted/40 p-2 text-xs"><div className="flex flex-wrap items-center gap-2"><span className="font-mono">Authorization</span><code>{effectiveToken ? revealToken ? `Bearer ${effectiveToken}` : 'Bearer ••••••••' : '未设置'}</code><Button type="button" variant="link" className="h-auto min-h-0 p-0 text-[10px]" onClick={() => setRevealToken(!revealToken)}>{revealToken ? '隐藏' : '显示'}</Button></div><span className="mt-1 block text-muted-foreground">会话凭据为只读；临时 token 只在当前页状态。</span></div><div className="rounded-md bg-muted/40 p-2 text-xs"><span className="font-mono">X-Request-Id</span><span className="ml-2 font-mono text-muted-foreground">{requestId ?? '发送时生成'}</span></div>{Object.entries(headers).map(([key, value], index) => <div key={`${key}-${index}`} className="grid grid-cols-[minmax(4rem,0.8fr)_minmax(0,1.2fr)_2.75rem] gap-2"><Input aria-label={`自定义 header 名称 ${index + 1}`} value={key} placeholder="Header" onChange={(event) => { const name = event.target.value; if (/^(authorization|cookie)$/i.test(name)) { setNotice("Authorization 由 token 管理，Cookie 不能在调试台编辑。"); return } const next = { ...headers }; delete next[key]; next[name] = value; setHeaders(next) }} /><Input aria-label={`自定义 header 值 ${index + 1}`} value={value} placeholder="Value" onChange={(event) => setHeaders({ ...headers, [key]: event.target.value })} /><Button type="button" size="icon" variant="ghost" aria-label={`删除 header ${key || index + 1}`} onClick={() => { const next = { ...headers }; delete next[key]; setHeaders(next) }}><Trash2 aria-hidden="true" /></Button></div>)}<Button type="button" variant="outline" size="sm" onClick={() => setHeaders({ ...headers, [`X-Custom-${Object.keys(headers).length + 1}`]: '' })}><Plus aria-hidden="true" />添加 header</Button><p className="text-[10px] text-muted-foreground">可用自定义 `X-Token` 渠道；提交后不会保存 Authorization、X-Token 或 Cookie。</p></div></details>
      {selectedSchema ? <section className="space-y-2"><div className="flex flex-wrap items-baseline justify-between gap-2"><Label className="text-sm font-semibold">JSON 请求体</Label><span className="text-[10px] text-muted-foreground">Mod+Shift+F 格式化 · 支持字段补全与折叠</span></div><Suspense fallback={<p className="min-h-44 rounded-lg border p-3 text-xs text-muted-foreground">正在载入 JSON 编辑器…</p>}><JsonEditor value={body} onChange={setBody} schema={selectedSchema} openApi={openApiQuery.data} ariaLabel="JSON 请求体编辑器" /></Suspense>{optionalFields.length > 0 && <details className="rounded-lg border"><summary className="min-h-10 cursor-pointer px-3 py-2 text-xs font-semibold">可选字段说明 <span className="font-normal text-muted-foreground">{optionalFields.length}</span></summary><dl className="space-y-2 border-t p-3">{optionalFields.map((field) => <div key={field.path} className="text-xs"><dt className="font-mono font-medium">{field.path}</dt><dd className="mt-0.5 text-muted-foreground">{field.description}</dd></div>)}</dl></details>}</section> : null}
      <section className="space-y-2 rounded-lg border p-3" aria-label="保存请求收藏"><div className="flex items-center gap-2"><Heart aria-hidden="true" className="size-4 text-primary" /><h3 className="text-sm font-semibold">命名收藏</h3></div><div className="flex gap-2"><Input aria-label="收藏名称" value={favoriteName} onChange={(event) => setFavoriteName(event.target.value)} placeholder="例如：查询服务健康" maxLength={64} /><Button type="button" onClick={saveFavorite} disabled={favoriteMutation.isPending}>{editingFavorite ? '更新' : '保存'}</Button></div><p className="text-[10px] text-muted-foreground">收藏由 `/api/snippets` 同步到服务端，发送重放时使用当前页 token。</p></section>
      <details className="rounded-lg border"><summary className="min-h-10 cursor-pointer px-3 py-2 text-xs font-semibold">cURL 导出</summary><div className="space-y-2 border-t p-3"><label className="flex items-center gap-2 text-xs"><input type="checkbox" className="size-4 accent-primary" checked={curlIncludeToken} onChange={(event) => setCurlIncludeToken(event.target.checked)} />包含真实 token（仅供自用）</label><pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-muted/40 p-2 font-mono text-[10px]">{makeCurl({ baseUrl: apiBase(baseUrl), method: selected.method, path: currentUrl, body, headers: normalizeStoredHeaders(headers), token: effectiveToken ?? undefined, includeToken: curlIncludeToken })}</pre></div></details>
      {notice && <p className="break-words rounded-lg border border-warning/30 bg-warning/5 p-2 text-xs" role="status">{notice}</p>}
    </div> : <div className="grid min-h-[20rem] place-items-center p-8 text-center text-sm text-muted-foreground">接口选中后，请求编辑器会在此打开。</div>}
    {selected && <div className="sticky bottom-0 z-10 flex items-center justify-between gap-2 border-t bg-card/95 p-3 text-[10px] text-muted-foreground backdrop-blur"><code className="min-w-0 flex-1 truncate">{currentUrl}</code><span className="shrink-0 font-mono">X-Request-Id {requestId ?? '发送时生成'}</span></div>}
  </section>

  const responsePanel = <section aria-label="响应与日志" className="min-w-0 rounded-xl border bg-card">
    <div className="grid grid-cols-2 border-b p-2"><h2 className="px-2 py-1 text-sm font-semibold">响应</h2><span className="px-2 py-1 text-right text-xs text-muted-foreground">{response ? `${Number(response.elapsedMs).toFixed(1)} ms` : '等待请求'}</span></div>
    <div className="max-h-[56dvh] min-h-40 overflow-y-auto p-3">{responseContent}</div>
    <div className="flex items-center justify-between border-y bg-muted/20 px-3 py-2"><h2 className="text-sm font-semibold">请求关联日志</h2><span className="text-xs text-muted-foreground">{realtime.logs.length}</span></div>
    <div className="max-h-[40dvh] overflow-y-auto p-3">{logContent}</div>
  </section>

  return <div className="min-h-[calc(100dvh-8rem)] space-y-3 px-3 pb-5 pt-4 sm:px-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">更多 · OpenAPI</p><h1 className="text-2xl font-semibold tracking-tight">API 调试台</h1><p className="mt-1 text-sm text-muted-foreground">动态接口、请求追踪和命名收藏集中在一处。</p></div><details className="max-w-full"><summary className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border bg-card px-3 text-sm font-medium"><BookOpen aria-hidden="true" className="size-4" />接入指南<ChevronDown aria-hidden="true" className="size-4" /></summary><Card className="absolute right-3 z-30 mt-2 max-h-[75dvh] w-[min(42rem,calc(100vw-1.5rem))] overflow-y-auto shadow-lg"><CardHeader><CardTitle>指南预览</CardTitle></CardHeader><CardContent className="space-y-3"><GuideMarkdown /></CardContent></Card></details></header>
    <div className="grid gap-2 rounded-xl border bg-muted/20 p-3 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_auto] md:items-end"><div className="space-y-1"><Label htmlFor="console-global-base" className="text-xs">Base URL</Label><Input id="console-global-base" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="同源相对地址" /></div><div className="space-y-1"><Label htmlFor="console-global-token" className="text-xs">临时 token（当前页）</Label><Input id="console-global-token" type="password" value={temporaryToken} onChange={(event) => { setTemporaryToken(event.target.value); setTokenOverride(true) }} placeholder={token ? '默认使用登录 token' : '仅当前页有效'} /></div><div className="flex items-center gap-2 md:justify-end"><Badge variant={baseUrl.trim() || tokenOverride ? 'warning' : 'secondary'}>{baseUrl.trim() || tokenOverride ? '自定义环境' : '默认环境'}</Badge><Button type="button" variant="outline" size="sm" onClick={() => { setTokenOverride(false); setTemporaryToken('') }}>重置 token</Button></div></div>
    {openApiQuery.isLoading ? <p className="rounded-xl border p-6 text-center text-sm text-muted-foreground" role="status">正在读取 `/openapi.json`…</p> : null}
    <div data-testid="api-console-grid" data-layout={narrow ? 'mobile' : 'desktop'} className="grid min-h-[calc(100dvh-14rem)] gap-3 md:grid-cols-[280px_minmax(22rem,1fr)_minmax(20rem,40%)] md:items-start">
      {!narrow || mobileStep === 'tree' ? treePanel : null}
      {!narrow || mobileStep === 'request' ? <div className={narrow ? 'min-h-0' : 'min-w-0'}>
        {narrow && selected && <div className="mb-2 grid grid-cols-3 gap-1 rounded-lg border bg-muted/40 p-1" role="tablist" aria-label="请求、响应、日志">{([['request', '请求'], ['response', '响应'], ['logs', '日志']] as const).map(([tab, label]) => <Button key={tab} type="button" role="tab" aria-selected={mobileTab === tab} variant={mobileTab === tab ? 'secondary' : 'ghost'} onClick={() => { setMobileTab(tab); setDrawerSize(tab === 'request' ? 'peek' : 'half') }}>{label}{tab === 'logs' && realtime.logs.length > 0 ? <Badge className="ml-1 px-1">{realtime.logs.length}</Badge> : null}</Button>)}</div>}
        {(!narrow || mobileTab === 'request') ? requestPanel : null}
      </div> : null}
      {!narrow ? responsePanel : null}
    </div>
    {narrow && response && mobileTab !== 'request' ? <section aria-label="响应抽屉" onTouchStart={(event) => { drawerTouchStart.current = event.touches[0]?.clientY ?? null }} onTouchEnd={(event) => {
      const from = drawerTouchStart.current
      const to = event.changedTouches[0]?.clientY
      drawerTouchStart.current = null
      if (from === null || to === undefined || Math.abs(from - to) < 40) return
      if (from - to > 0) setDrawerSize((current) => current === 'peek' ? 'half' : 'full')
      else setDrawerSize((current) => current === 'full' ? 'half' : 'peek')
    }} className={`fixed inset-x-0 bottom-[calc(3.5rem+env(safe-area-inset-bottom,0px))] z-20 overflow-hidden rounded-t-2xl border bg-card shadow-[0_-12px_40px_rgba(0,0,0,0.18)] transition-[height] ${drawerSize === 'peek' ? 'h-12' : drawerSize === 'full' ? 'h-[calc(100dvh-3.5rem-env(safe-area-inset-bottom,0px))]' : 'h-[60dvh]'}`}>
      <div className="flex min-h-12 items-center justify-between gap-2 border-b px-4"><span className="truncate font-mono text-sm font-semibold">{responseLabel(response)} <span className="font-sans text-xs font-normal text-muted-foreground">· {Number(response.elapsedMs).toFixed(1)} ms</span></span><Button type="button" variant="ghost" size="icon" aria-label={drawerSize === 'peek' ? '展开响应' : drawerSize === 'full' ? '收回响应' : '全屏响应'} onClick={() => setDrawerSize(drawerSize === 'peek' ? 'half' : drawerSize === 'half' ? 'full' : 'half')}>{drawerSize === 'peek' ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}</Button></div>
      {drawerSize !== 'peek' && <div className={`overflow-y-auto p-3 ${drawerSize === 'full' ? 'h-[calc(100%-3rem)]' : 'h-[calc(60dvh-3rem)]'}`}>{mobileTab === 'response' ? responseContent : logContent}</div>}
    </section> : null}
    {narrow && selected && mobileTab === 'request' && <div className="fixed inset-x-0 bottom-[calc(3.5rem+env(safe-area-inset-bottom,0px))] z-10 border-t bg-card/95 p-2 backdrop-blur"><Button type="button" className="min-h-12 w-full" onClick={() => void sendRequest()} disabled={sending}>{sending ? '发送中…' : '发送请求'}</Button></div>}
  </div>
}

export default ApiConsolePage
