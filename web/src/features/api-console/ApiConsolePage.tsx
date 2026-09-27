import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, MousePointerClick } from 'lucide-react'
import createClient from 'openapi-fetch'
import { Link, useLocation, useNavigate } from 'react-router'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { copyText } from '@/lib/clipboard'
import { useAuth } from '@/stores/auth'
import type { paths } from '@/types/api'
import {
  apiBase,
  bodySchema,
  enabledQuery,
  fetchThroughGlobal,
  findOperation,
  findOperationForPath,
  formatJson,
  normalizeStoredHeaders,
  operationDraft,
  randomId,
  recordOf,
  responseBodyText,
  snippetsBase,
  STATUS_LABELS,
  storage,
  toSnippet,
  type ApiSnippet,
  type ApiSnippetWrite,
  type ConsoleHistoryEntry,
  type ConsoleNotice,
  type DrawerSize,
  type DrawerView,
  type OpenApiDocument,
  type ResponseView,
  type SavedRequest,
  type TreeTab,
} from './console-model'
import { EnvironmentRow, EnvironmentSheet, type ConsoleEnvironment } from './EnvironmentSheet'
import { ApiGuidePage } from './GuidePage'
import { DocsLinks, FavoriteList, HistoryList, InterfaceList, TreeToolbar } from './OperationList'
import { NoticeBanner, RequestDock, SendFooter } from './RequestDock'
import { BodySection, CurlSection, FavoriteSection, HeadersSection, ParameterSections, RequestHeading, RequestStepBar } from './RequestEditor'
import { LogContents, ResponseContents, ResponseStatus } from './ResponseViews'
import { useConsoleRealtime } from './use-console-realtime'
import {
  buildOperationTree,
  buildRequestUrl,
  describeResponse,
  filterSensitive,
  loadHistory,
  makeCurl,
  optionalFieldDescriptions,
  pipelineTemplateFromBody,
  saveHistory,
  type ConsoleOperation,
  type QueryDraft,
} from './utils'

const GUIDE_PATH = '/more/api-console/guide'
/** 三栏的最小视口宽度：桌面侧边栏 240px 之外，还要放下接口树、请求、响应三列的最小宽度。 */
const WIDE_LAYOUT_MIN_WIDTH = 1280
/** 外壳 main 的底部留白是 --nav-offset + 1.5rem；三栏刚好占满其余高度，各栏自己滚动。 */
const WIDE_PAGE_CLASS = 'h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[34rem]'
const COLUMN_CLASS = 'flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-card'
const SCHEDULE_EDITOR_PATH = '/tasks/schedules/new'

function useWideLayout(): boolean {
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.innerWidth >= WIDE_LAYOUT_MIN_WIDTH)
  useEffect(() => {
    const update = () => setWide(window.innerWidth >= WIDE_LAYOUT_MIN_WIDTH)
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  return wide
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

function pathAndQuery(url: string): string {
  try {
    const parsed = new URL(url)
    return `${parsed.pathname}${parsed.search}`
  } catch {
    return url
  }
}

/** `/more/api-console/guide` 是整页的接入指南，其余情况是调试台本身。 */
export default function ApiConsoleRoute() {
  const location = useLocation()
  return location.pathname.replace(/\/$/, '') === GUIDE_PATH ? <ApiGuidePage /> : <ApiConsolePage />
}

function ApiConsolePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = useAuth((state) => state.token)
  const wide = useWideLayout()
  const [confirm, confirmElement] = useConfirm()
  const [baseUrl, setBaseUrl] = useState('')
  const [temporaryToken, setTemporaryToken] = useState('')
  const [tokenOverride, setTokenOverride] = useState(false)
  const [environmentOpen, setEnvironmentOpen] = useState(false)
  const [revealToken, setRevealToken] = useState(false)
  const [search, setSearch] = useState('')
  const [treeTab, setTreeTab] = useState<TreeTab>('interfaces')
  const [selected, setSelected] = useState<ConsoleOperation | null>(null)
  const [step, setStep] = useState<'tree' | 'request'>('tree')
  const [drawerSize, setDrawerSize] = useState<DrawerSize>('half')
  const [drawerView, setDrawerView] = useState<DrawerView>('response')
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
  const [requestNotice, setRequestNotice] = useState<ConsoleNotice | null>(null)
  const [listNotice, setListNotice] = useState<ConsoleNotice | null>(null)
  const imageUrlRef = useRef<string | null>(null)
  const listScrollTop = useRef(0)

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

  // 手机分步工作流：进入请求页回到顶部，返回列表时回到刚才的位置。
  useLayoutEffect(() => {
    if (wide) return
    const scroller = document.scrollingElement
    if (scroller) scroller.scrollTop = step === 'tree' ? listScrollTop.current : 0
  }, [step, wide])

  const chooseOperation = (operation: ConsoleOperation, saved?: SavedRequest, favorite?: ApiSnippet) => {
    setSelected(operation)
    const draft = operationDraft(operation, openApiQuery.data, saved)
    setPathParams(draft.pathParams)
    setQuery(draft.query)
    setHeaders(draft.headers)
    setBody(draft.body)
    setEditingFavorite(favorite?.id ?? null)
    setFavoriteName(favorite?.name ?? '')
    setRequestId(null)
    setPipelineId(null)
    setResponse(null)
    setRequestNotice(null)
    setListNotice(null)
    setDrawerView('response')
    if (step === 'tree') listScrollTop.current = document.scrollingElement?.scrollTop ?? 0
    setStep('request')
  }

  const saveHistoryEntry = (entry: ConsoleHistoryEntry) => {
    const next = [entry, ...history].slice(0, 100)
    setHistory(next)
    saveHistory(storage(), [...next].reverse())
  }

  const sendRequest = async () => {
    if (!selected || sending) return
    setRequestNotice(null)
    let url: URL
    try {
      url = new URL(buildRequestUrl({ baseUrl: apiBase(baseUrl), path: selected.path, pathParams, query }))
    } catch (error) {
      setRequestNotice({ tone: 'danger', text: error instanceof Error ? error.message : '请求地址无效' })
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
      setDrawerView('response')
      const historyUrl = new URL(url.toString())
      const historyEntry = filterSensitive({
        id: randomId(), method: selected.method, path: historyUrl.pathname,
        path_params: pathParams,
        query: enabledQuery(query),
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
      setRequestNotice({ tone: 'danger', text: `请求失败：${message}` })
      const elapsedMs = Math.max(0, performance.now() - start)
      const errorBody = { error: { code: 'NETWORK_ERROR', message } }
      const details = describeResponse({ status: 0, statusText: 'Network Error', elapsedMs, headers: {}, body: errorBody })
      setResponse({ ...details, bodyText: JSON.stringify(errorBody, null, 2), requestId: currentRequestId, url: url.toString() })
      const historyEntry = filterSensitive({
        id: randomId(), method: selected.method, path: url.pathname,
        path_params: pathParams,
        query: enabledQuery(query),
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
      setDrawerView('response')
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
      toast.success('收藏已保存，鉴权凭据不会写入收藏')
      setRequestNotice(null)
      setEditingFavorite(item?.id ?? null)
      setFavoriteName(item?.name ?? favoriteName)
      await queryClient.invalidateQueries({ queryKey: ['api-console', 'snippets'] })
    },
    onError: (error) => setRequestNotice({ tone: 'danger', text: `收藏保存失败：${toApiError(error).message}` }),
  })
  const deleteFavoriteMutation = useMutation({
    mutationFn: async (id: string) => {
      const result = await snippetApi.DELETE('/api/snippets/{snippet_id}', {
        params: { path: { snippet_id: id } }, headers: snippetHeaders, credentials: 'same-origin',
      })
      if (result.error) throw toApiError(result.error, result.response)
    },
    onSuccess: async (_data, id) => {
      toast.success('收藏已删除')
      if (id === editingFavorite) {
        setEditingFavorite(null)
        setFavoriteName('')
      }
      await queryClient.invalidateQueries({ queryKey: ['api-console', 'snippets'] })
    },
    onError: (error) => setListNotice({ tone: 'danger', text: `收藏删除失败：${toApiError(error).message}` }),
  })

  const requestDeleteFavorite = async (favorite: ApiSnippet) => {
    const confirmed = await confirm({
      title: `删除收藏「${favorite.name}」？`,
      description: `${favorite.method} ${favorite.path} 会从当前服务上删除，无法恢复。`,
      confirmText: '删除收藏',
      tone: 'danger',
    })
    if (confirmed) deleteFavoriteMutation.mutate(favorite.id)
  }

  const selectFavorite = (favorite: ApiSnippet) => {
    const operation = findOperation(groups, favorite.method, favorite.path)
    if (!operation) {
      setListNotice({ tone: 'warning', text: `收藏目标 ${favorite.method} ${favorite.path} 已不在当前 OpenAPI 契约中。` })
      return
    }
    chooseOperation(operation, favorite, favorite)
  }

  const restoreHistory = (entry: ConsoleHistoryEntry) => {
    const operation = findOperationForPath(groups, entry.method, entry.path)
    if (!operation) {
      setListNotice({ tone: 'warning', text: `历史目标 ${entry.method} ${entry.path} 已不在当前 OpenAPI 契约中。` })
      return
    }
    chooseOperation(operation, entry)
  }

  const currentUrl = useMemo(() => {
    if (!selected) return ''
    try { return buildRequestUrl({ baseUrl: apiBase(baseUrl), path: selected.path, pathParams, query }) }
    catch { return `${apiBase(baseUrl)}${selected.path}` }
  }, [baseUrl, selected, pathParams, query])

  const curlCommand = useMemo(() => selected
    ? makeCurl({
      baseUrl: apiBase(baseUrl), method: selected.method, path: currentUrl, body,
      headers: normalizeStoredHeaders(headers), token: effectiveToken ?? undefined, includeToken: curlIncludeToken,
    })
    : '', [baseUrl, body, curlIncludeToken, currentUrl, effectiveToken, headers, selected])

  const exportCurl = async () => {
    if (!selected) return
    if (await copyText(curlCommand)) {
      toast.success(curlIncludeToken ? '已复制 cURL 命令（含真实 token）' : '已复制 cURL 命令，token 用 $MAA_TOKEN 占位')
    } else {
      setRequestNotice({ tone: 'warning', text: '无法写入剪贴板，请在「cURL 导出」里手动复制命令。' })
    }
  }

  const saveFavorite = () => {
    if (!selected || favoriteMutation.isPending) return
    if (!favoriteName.trim()) {
      setRequestNotice({ tone: 'warning', text: '请先填写收藏名称。' })
      return
    }
    favoriteMutation.mutate({ payload: toSnippet(selected, favoriteName, pathParams, query, headers, body), id: editingFavorite })
  }

  const handoffSchedule = () => {
    if (!selected || selected.path !== '/api/pipelines') return
    const template = pipelineTemplateFromBody(body)
    if (template.length === 0) {
      setRequestNotice({ tone: 'warning', text: '当前请求体里没有可转入定时任务的任务模板。' })
      return
    }
    navigate(SCHEDULE_EDITOR_PATH, { state: { apiConsolePrefill: { template } } })
  }

  const handoffFavoriteSchedule = (favorite: ApiSnippet) => {
    const template = pipelineTemplateFromBody(favorite.body)
    if (template.length === 0) {
      setListNotice({ tone: 'warning', text: '这条收藏没有可转入定时任务的任务模板。' })
      return
    }
    navigate(SCHEDULE_EDITOR_PATH, { state: { apiConsolePrefill: { template } } })
  }

  const formatBody = () => {
    const formatted = formatJson(body)
    if (formatted === null) setRequestNotice({ tone: 'warning', text: 'JSON 格式不正确，无法格式化。' })
    else setBody(formatted)
  }

  const environment: ConsoleEnvironment = { baseUrl, temporaryToken, tokenOverride, loginToken: token }
  const changeTemporaryToken = (value: string) => {
    setTemporaryToken(value)
    setTokenOverride(true)
  }
  const restoreLoginToken = () => {
    setTokenOverride(false)
    setTemporaryToken('')
  }

  const guideLink = (
    <Button asChild variant="ghost" size="sm" className="text-primary-text">
      <Link to={GUIDE_PATH}><BookOpen aria-hidden="true" />接入指南</Link>
    </Button>
  )
  const environmentRow = <EnvironmentRow environment={environment} onOpen={() => setEnvironmentOpen(true)} />
  const environmentSheet = (
    <EnvironmentSheet
      open={environmentOpen}
      onOpenChange={setEnvironmentOpen}
      environment={environment}
      onBaseUrlChange={setBaseUrl}
      onTemporaryTokenChange={changeTemporaryToken}
      onUseLoginToken={restoreLoginToken}
      onReset={() => {
        setBaseUrl('')
        restoreLoginToken()
      }}
    />
  )
  const treeToolbar = <TreeToolbar tab={treeTab} onTabChange={setTreeTab} search={search} onSearchChange={setSearch} />
  const listNoticeBanner = listNotice ? <NoticeBanner notice={listNotice} onDismiss={() => setListNotice(null)} /> : null
  const treeBody = treeTab === 'interfaces' ? (
    <InterfaceList
      groups={groups}
      search={search}
      onClearSearch={() => setSearch('')}
      selected={selected}
      onSelect={(operation) => chooseOperation(operation)}
      loading={openApiQuery.isLoading}
      error={openApiQuery.error}
      refreshing={openApiQuery.isFetching}
      onRefresh={() => void openApiQuery.refetch()}
      inset={wide}
    />
  ) : treeTab === 'favorites' ? (
    <FavoriteList
      favorites={favorites}
      loading={snippetQuery.isLoading}
      error={snippetQuery.error}
      refreshing={snippetQuery.isFetching}
      onRefresh={() => void snippetQuery.refetch()}
      onOpen={selectFavorite}
      onSchedule={handoffFavoriteSchedule}
      onDelete={(favorite) => void requestDeleteFavorite(favorite)}
      inset={wide}
    />
  ) : (
    <HistoryList entries={history} onRestore={restoreHistory} inset={wide} />
  )
  const requestSections = selected ? (
    <>
      <ParameterSections operation={selected} document={openApiQuery.data} pathParams={pathParams} onPathParamsChange={setPathParams} query={query} onQueryChange={setQuery} />
      <HeadersSection
        headers={headers}
        onHeadersChange={setHeaders}
        onBlockedHeader={() => setRequestNotice({ tone: 'warning', text: 'Authorization 由 token 管理，Cookie 不能在调试台编辑。' })}
        token={effectiveToken}
        revealToken={revealToken}
        onRevealTokenChange={setRevealToken}
        requestId={requestId}
      />
      {selectedSchema ? <BodySection body={body} onBodyChange={setBody} schema={selectedSchema} openApi={openApiQuery.data} optionalFields={optionalFields} onFormat={formatBody} /> : null}
      <FavoriteSection
        name={favoriteName}
        onNameChange={setFavoriteName}
        editing={Boolean(editingFavorite)}
        saving={favoriteMutation.isPending}
        onSave={saveFavorite}
        canSchedule={selected.path === '/api/pipelines'}
        onSchedule={handoffSchedule}
      />
      <CurlSection command={curlCommand} includeToken={curlIncludeToken} onIncludeTokenChange={setCurlIncludeToken} onCopy={() => void exportCurl()} />
    </>
  ) : null
  const responseContent = <ResponseContents response={response} openApi={openApiQuery.data} />
  const logContent = <LogContents logs={realtime.logs} timeline={realtime.timeline} requestId={requestId} connection={realtime.connection} historyTruncated={realtime.historyTruncated} />

  if (wide) {
    return (
      <Page title="API 调试台" back="/more" width="full" actions={guideLink} className={WIDE_PAGE_CLASS} contentClassName="flex min-h-0 flex-col pt-4 lg:pt-5">
        <div data-testid="api-console-grid" data-layout="desktop" className="grid min-h-0 flex-1 grid-cols-[18.5rem_minmax(22rem,1fr)_minmax(20rem,0.85fr)] gap-4">
          <aside aria-label="接口列表" className={COLUMN_CLASS}>
            <div className="shrink-0 border-b p-3">{treeToolbar}</div>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              {listNoticeBanner ? <div className="p-3 pb-0">{listNoticeBanner}</div> : null}
              {treeBody}
            </div>
            <DocsLinks className="shrink-0 border-t px-4" />
          </aside>
          <section aria-label="请求构造器" className={COLUMN_CLASS}>
            <div className="shrink-0 border-b p-1.5">{environmentRow}</div>
            {selected ? (
              <>
                <RequestHeading operation={selected} showPath className="shrink-0 border-b px-5 py-3" />
                <div className="min-h-0 flex-1 divide-y overflow-y-auto overscroll-contain">{requestSections}</div>
                <SendFooter
                  url={currentUrl}
                  requestId={requestId}
                  sending={sending}
                  onSend={() => void sendRequest()}
                  onCopyCurl={() => void exportCurl()}
                  notice={requestNotice}
                  onDismissNotice={() => setRequestNotice(null)}
                />
              </>
            ) : (
              <EmptyState icon={MousePointerClick} title="选择一个接口" description="从左侧接口列表选择后，这里会按 OpenAPI 生成请求表单。" className="flex-1" />
            )}
          </section>
          <section aria-label="响应与日志" className={COLUMN_CLASS}>
            <div className="flex min-h-12 shrink-0 items-center gap-3 border-b px-4 py-2">
              <h2 className="text-[0.9375rem] font-semibold">响应</h2>
              {response
                ? <ResponseStatus response={response} className="ml-auto justify-end" />
                : <span className="ml-auto text-footnote text-muted-foreground">等待请求</span>}
            </div>
            <div className="min-h-0 flex-[3] overflow-y-auto overscroll-contain p-4">{responseContent}</div>
            <div className="flex min-h-12 shrink-0 items-center gap-2 border-y px-4 py-2">
              <h2 className="text-[0.9375rem] font-semibold">请求关联日志</h2>
              <span className="text-footnote text-muted-foreground tabular-nums">{realtime.logs.length}</span>
            </div>
            <div className="min-h-0 flex-[2] overflow-y-auto overscroll-contain p-4">{logContent}</div>
          </section>
        </div>
        {environmentSheet}
        {confirmElement}
      </Page>
    )
  }

  const editing = step === 'request' ? selected : null
  return (
    <Page
      title="API 调试台"
      back="/more"
      width="default"
      actions={guideLink}
      toolbar={editing ? <RequestStepBar operation={editing} onBack={() => setStep('tree')} /> : treeToolbar}
    >
      <div data-testid="api-console-grid" data-layout="mobile" className="pb-4">
        {editing ? (
          <section aria-label="请求构造器" className="space-y-3">
            <div className="rounded-xl border bg-card p-1.5 shadow-card">{environmentRow}</div>
            <div className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
              <RequestHeading operation={editing} showPath={false} className="px-5 py-3" />
              {requestSections}
            </div>
          </section>
        ) : (
          <aside aria-label="接口列表" className="space-y-4">
            {listNoticeBanner}
            {treeTab === 'history' ? null : <div className="rounded-xl border bg-card p-1.5 shadow-card">{environmentRow}</div>}
            {treeBody}
            <DocsLinks className="px-1" />
          </aside>
        )}
      </div>
      {editing ? (
        <RequestDock
          url={baseUrl.trim() ? currentUrl : pathAndQuery(currentUrl)}
          sending={sending}
          onSend={() => void sendRequest()}
          onCopyCurl={() => void exportCurl()}
          notice={requestNotice}
          onDismissNotice={() => setRequestNotice(null)}
          response={response}
          drawerSize={drawerSize}
          onDrawerSizeChange={setDrawerSize}
          drawerView={drawerView}
          onDrawerViewChange={setDrawerView}
          logCount={realtime.logs.length}
          responseContent={responseContent}
          logContent={logContent}
        />
      ) : null}
      {environmentSheet}
      {confirmElement}
    </Page>
  )
}
