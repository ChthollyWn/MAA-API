import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Search } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/layout/PageHeader'

const PAGE_SIZE = 20

interface AuditRecord {
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

interface AuditPageResult {
  items: AuditRecord[]
  total: number
  page: number
  size: number
}

interface AuditFilters {
  caller: string
  tool_name: string
  status: string
  risk_level: string
  since: string
}

const EMPTY_FILTERS: AuditFilters = {
  caller: '',
  tool_name: '',
  status: '',
  risk_level: '',
  since: '',
}

function initialFilters(search: URLSearchParams): AuditFilters {
  const since = search.get('since') ?? ''
  return {
    caller: search.get('caller') ?? '',
    tool_name: search.get('tool_name') ?? '',
    status: search.get('status') ?? '',
    risk_level: search.get('risk_level') ?? '',
    since: since ? since.slice(0, 10) : '',
  }
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: '等待确认', success: '成功', failed: '失败', rejected: '已拒绝', expired: '已过期',
  }
  return labels[value] ?? value
}

function statusVariant(value: string): 'warning' | 'positive' | 'destructive' | 'secondary' {
  if (value === 'pending') return 'warning'
  if (value === 'success') return 'positive'
  if (value === 'failed' || value === 'rejected' || value === 'expired') return 'destructive'
  return 'secondary'
}

function riskLabel(value: string): string {
  if (value === 'consume') return '资源消耗'
  if (value === 'destructive') return '破坏性操作'
  return '无额外风险'
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [filters, setFilters] = useState<AuditFilters>(() => initialFilters(searchParams))
  const [expanded, setExpanded] = useState<number | null>(null)
  const page = Math.max(1, Number(searchParams.get('page') ?? 1) || 1)
  useEffect(() => {
    setFilters(initialFilters(searchParams))
  }, [searchParams])
  const query = useMemo(() => {
    const value: Record<string, string | number> = { page, size: PAGE_SIZE }
    if (searchParams.get('caller')) value.caller = searchParams.get('caller')!
    if (searchParams.get('tool_name')) value.tool_name = searchParams.get('tool_name')!
    if (searchParams.get('status')) value.status = searchParams.get('status')!
    if (searchParams.get('risk_level')) value.risk_level = searchParams.get('risk_level')!
    if (searchParams.get('since')) value.since = searchParams.get('since')!
    return value
  }, [page, searchParams])

  const audits = useQuery({
    queryKey: keys.agent.auditList(query),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/audits', { params: { query } })
      if (error) throw toApiError(error, response)
      return data as unknown as AuditPageResult
    },
    retry: false,
  })
  const detail = useQuery({
    queryKey: keys.agent.auditDetail(expanded ?? 0),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/agent/audits/{audit_id}', {
        params: { path: { audit_id: expanded ?? 0 } },
      })
      if (error) throw toApiError(error, response)
      return data as unknown as AuditRecord
    },
    enabled: expanded !== null,
    retry: false,
  })

  const applyFilters = () => {
    const next = new URLSearchParams()
    if (filters.caller) next.set('caller', filters.caller)
    if (filters.tool_name.trim()) next.set('tool_name', filters.tool_name.trim())
    if (filters.status) next.set('status', filters.status)
    if (filters.risk_level) next.set('risk_level', filters.risk_level)
    if (filters.since) next.set('since', new Date(`${filters.since}T00:00:00Z`).toISOString())
    setSearchParams(next)
  }

  const resetFilters = () => {
    setFilters(EMPTY_FILTERS)
    setSearchParams(new URLSearchParams())
  }

  const goToPage = (nextPage: number) => {
    const next = new URLSearchParams(searchParams)
    if (nextPage <= 1) next.delete('page')
    else next.set('page', String(nextPage))
    setSearchParams(next)
  }

  return (
    <div className="mx-auto w-full max-w-4xl pb-6">
      <PageHeader title="Agent 审计" description="按调用来源和执行结果回溯 Agent 工具操作" />
      <div className="space-y-4 py-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">筛选记录</CardTitle>
            <CardDescription>筛选参数保存在页面地址中，可复制当前视图。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <label className="space-y-1.5 text-sm font-medium">
                <span>调用方</span>
                <select className="min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="调用方" value={filters.caller} onChange={(event) => setFilters({ ...filters, caller: event.target.value })}>
                  <option value="">全部</option><option value="rest">REST</option><option value="internal">内置 Agent</option>
                </select>
              </label>
              <label className="space-y-1.5 text-sm font-medium">
                <span>工具名</span>
                <Input aria-label="工具名" value={filters.tool_name} onChange={(event) => setFilters({ ...filters, tool_name: event.target.value })} placeholder="例如 submit_pipeline" />
              </label>
              <label className="space-y-1.5 text-sm font-medium">
                <span>状态</span>
                <select className="min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="状态" value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}>
                  <option value="">全部</option><option value="pending">等待确认</option><option value="success">成功</option><option value="failed">失败</option><option value="rejected">已拒绝</option><option value="expired">已过期</option>
                </select>
              </label>
              <label className="space-y-1.5 text-sm font-medium">
                <span>风险</span>
                <select className="min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="风险" value={filters.risk_level} onChange={(event) => setFilters({ ...filters, risk_level: event.target.value })}>
                  <option value="">全部</option><option value="none">无额外风险</option><option value="consume">资源消耗</option><option value="destructive">破坏性操作</option>
                </select>
              </label>
              <label className="space-y-1.5 text-sm font-medium">
                <span>开始日期（UTC）</span>
                <Input aria-label="开始日期" type="date" value={filters.since} onChange={(event) => setFilters({ ...filters, since: event.target.value })} />
              </label>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button type="button" onClick={applyFilters}><Search aria-hidden="true" />应用筛选</Button>
              <Button type="button" variant="outline" onClick={resetFilters}>清除筛选</Button>
              <Button type="button" variant="ghost" disabled={audits.isFetching} onClick={() => void audits.refetch()}>{audits.isFetching ? '正在刷新…' : '刷新'}</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-2">
              <div><CardTitle className="text-base">调用记录</CardTitle><CardDescription className="mt-1">{audits.data?.total ?? 0} 条记录 · 第 {page} 页</CardDescription></div>
            </div>
          </CardHeader>
          <CardContent>
            {audits.isPending ? <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" />正在读取审计记录…</p> : null}
            {audits.error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"><span>读取失败：{queryError(audits.error)}</span><Button type="button" variant="outline" disabled={audits.isFetching} onClick={() => void audits.refetch()}>重试</Button></div> : null}
            {audits.data?.items.length === 0 ? <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">没有符合条件的审计记录。</p> : null}
            {audits.data?.items.length ? <div className="divide-y">
              {audits.data.items.map((record) => {
                const opened = expanded === record.audit_id
                const shown = opened && detail.data?.audit_id === record.audit_id ? detail.data : record
                const pipelineId = typeof shown.result_ref?.pipeline_id === 'string' ? shown.result_ref.pipeline_id : null
                return <article key={record.audit_id} className="py-4 first:pt-0 last:pb-0">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="font-mono text-sm font-semibold">{record.tool_name}</code>
                        <Badge variant={statusVariant(record.status)}>{statusLabel(record.status)}</Badge>
                        <Badge variant="outline">{riskLabel(record.risk_level)}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">{record.caller_detail || record.caller} · {formatDate(record.created_at)} · {record.duration_ms} ms</p>
                      {record.error_code ? <p className="font-mono text-xs text-destructive">{record.error_code}</p> : null}
                    </div>
                    <Button type="button" variant="outline" size="sm" aria-expanded={opened} onClick={() => setExpanded(opened ? null : record.audit_id)}>{opened ? '收起详情' : '查看详情'}</Button>
                  </div>
                  {opened ? <div className="mt-3 space-y-3 rounded-lg bg-muted/35 p-3 text-sm">
                    {detail.isLoading && <p role="status">正在读取完整详情…</p>}
                    {detail.error && <p role="alert" className="text-destructive">详情读取失败：{queryError(detail.error)}</p>}
                    <div className="grid gap-2 text-xs sm:grid-cols-2">
                      <p><span className="text-muted-foreground">审计编号：</span><code>{record.audit_id}</code></p>
                      {shown.request_id ? <p><span className="text-muted-foreground">请求编号：</span><code className="break-all">{shown.request_id}</code></p> : null}
                      {shown.session_id ? <p><span className="text-muted-foreground">会话：</span><code className="break-all">{shown.session_id}</code></p> : null}
                      {shown.confirmation_id ? <p><span className="text-muted-foreground">确认：</span><code className="break-all">{shown.confirmation_id}</code></p> : null}
                      {shown.authorized_by_id ? <p><span className="text-muted-foreground">授权来源：</span><code className="break-all">{shown.authorized_by_id}</code></p> : null}
                    </div>
                    <section className="space-y-1"><h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">参数</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-3 font-mono text-xs leading-5">{prettyJson(shown.arguments)}</pre></section>
                    {shown.result_summary ? <section className="space-y-1"><h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">结果摘要</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-3 font-mono text-xs leading-5">{shown.result_summary}</pre></section> : null}
                    {pipelineId ? <Link className="inline-flex min-h-11 items-center gap-2 font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to={`/tasks/history/${encodeURIComponent(pipelineId)}`}><ExternalLink aria-hidden="true" className="size-4" />查看流水线与日志</Link> : null}
                  </div> : null}
                </article>
              })}
            </div> : null}
            {audits.data && audits.data.total > PAGE_SIZE ? <nav className="mt-4 flex items-center justify-between border-t pt-3" aria-label="审计分页">
              <Button type="button" variant="outline" size="sm" disabled={page <= 1} onClick={() => goToPage(page - 1)}><ChevronLeft aria-hidden="true" />上一页</Button>
              <span className="text-sm text-muted-foreground">第 {page} 页，共 {Math.ceil(audits.data.total / PAGE_SIZE)} 页</span>
              <Button type="button" variant="outline" size="sm" disabled={page * PAGE_SIZE >= audits.data.total} onClick={() => goToPage(page + 1)}>下一页<ChevronRight aria-hidden="true" /></Button>
            </nav> : null}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export default AuditPage
