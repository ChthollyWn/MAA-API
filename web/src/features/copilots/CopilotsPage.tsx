import { Fragment, useMemo, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, ExternalLink, RefreshCw, RotateCcw, Swords, Trash2 } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { Section } from '@/components/ui/section'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import {
  COPILOT_PAGE_SIZE,
  copilotKeys,
  deleteCopilot,
  downloadCopilotJson,
  fetchCopilotDetail,
  fetchCopilots,
  setCopilotRetain,
  type CopilotAsset,
} from './api'
import {
  SOURCE_FILTERS,
  attemptText,
  buildVersionChain,
  createdText,
  errorText,
  judgeLabel,
  outcomeLabel,
  outcomeTone,
  shortId,
  siteUrl,
  sourceLabel,
  stageText,
  titleText,
} from './format'
import { useDebouncedValue } from '@/features/account/use-debounced-value'

/** 「全部来源」在下拉里的值；空串与 NativeSelect 的无值项一致。 */
const ALL_SOURCES = ''

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}

/** 查看作业 JSON 的面板：详情端点按需取，内容只读。 */
function CopilotJsonSheet({ asset, onClose }: { asset: CopilotAsset; onClose: () => void }) {
  const detail = useQuery({
    queryKey: copilotKeys.detail(asset.id),
    queryFn: ({ signal }) => fetchCopilotDetail(asset.id, signal),
    retry: false,
  })
  const download = useMutation({
    mutationFn: () => fetchCopilotDetail(asset.id),
    onSuccess: (value) => {
      const filename = downloadCopilotJson(value)
      toast.success(`已下载 ${filename}`)
    },
  })
  return (
    <ResponsiveSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      desktop="panel"
      size="lg"
      title={titleText(asset)}
      description={`${sourceLabel(asset.meta.source)} · 关卡 ${stageText(asset)}`}
      footer={(
        <div className="space-y-2">
          {detail.error ? <InlineAlert tone="danger">读取作业失败：{errorText(detail.error)}</InlineAlert> : null}
          {download.error ? <InlineAlert tone="danger">下载失败：{errorText(download.error)}</InlineAlert> : null}
          <div className="grid grid-cols-2 gap-2">
            <Button variant="secondary" onClick={onClose}>关闭</Button>
            <Button
              disabled={!detail.data}
              loading={download.isPending}
              onClick={() => download.mutate()}
            >
              {download.isPending ? '正在下载…' : '下载 JSON'}
            </Button>
          </div>
        </div>
      )}
    >
      {detail.isPending ? (
        <LoadingState label="正在读取作业 JSON…" />
      ) : detail.error ? (
        <ErrorState message={errorText(detail.error)} onRetry={() => void detail.refetch()} retrying={detail.isFetching} />
      ) : (
        <pre
          aria-label="作业 JSON"
          className="max-h-[60dvh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2/70 p-3 font-mono text-xs leading-5"
        >
          {prettyJson(detail.data?.content)}
        </pre>
      )}
    </ResponsiveSheet>
  )
}

interface CopilotLineProps {
  asset: CopilotAsset
  /** 已取回的作业，用于沿 `parent_id` 还原版本链。 */
  lookup: ReadonlyMap<string, CopilotAsset>
  downloading: boolean
  onView: () => void
  onDownload: () => void
  onToggleRetain: () => void
  onDelete: () => void
}

/** 一行作业：来源、关卡、标题、版本链、每次尝试的结果与判定方、创建时间与操作。 */
function CopilotLine({ asset, lookup, downloading, onView, onDownload, onToggleRetain, onDelete }: CopilotLineProps) {
  const chain = buildVersionChain(asset, lookup)
  const link = siteUrl(asset.meta.siteUrl)
  const attempts = asset.meta.attempts
  return (
    <li data-testid={`copilot-${asset.id}`} className="space-y-2 p-3.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-[0.9375rem] font-medium">{titleText(asset)}</span>
        <Badge variant="neutral" data-testid={`copilot-source-${asset.id}`}>{sourceLabel(asset.meta.source)}</Badge>
        <Badge variant="outline">关卡 {stageText(asset)}</Badge>
        {asset.meta.retain ? (
          <Badge variant="success" data-testid={`copilot-retain-${asset.id}`}>已保留</Badge>
        ) : null}
        <Button variant="ghost" size="sm" className="ml-auto shrink-0" aria-label={`查看 ${titleText(asset)} 的 JSON`} onClick={onView}>
          查看 JSON
        </Button>
      </div>

      <p className="text-footnote text-muted-foreground">
        创建于 {createdText(asset.created_at)} · {attempts.length ? `跑过 ${attempts.length} 次` : '还没跑过'}
        {asset.meta.note ? ` · ${asset.meta.note}` : ''}
      </p>

      <p data-testid={`copilot-chain-${asset.id}`} className="flex flex-wrap items-center gap-1 text-footnote">
        <span className="text-muted-foreground">版本链</span>
        {chain.map((step, index) => (
          <Fragment key={step.id}>
            {index > 0 ? <span aria-hidden="true" className="text-muted-foreground">→</span> : null}
            <span className={step.current ? 'font-medium' : 'text-muted-foreground'}>
              {step.title}
              {step.current ? '（当前）' : step.missing ? `（不在本页 · ${shortId(step.id)}）` : ''}
            </span>
          </Fragment>
        ))}
      </p>

      {attempts.length ? (
        <ul aria-label={`${titleText(asset)} 的尝试结果`} className="flex flex-wrap gap-1">
          {attempts.map((attempt, index) => (
            <li key={attempt.id}>
              <Badge
                variant={outcomeTone(attempt.outcome)}
                title={attemptText(attempt)}
                data-testid={`copilot-attempt-${asset.id}-${index}`}
              >
                {outcomeLabel(attempt.outcome)}
                <span aria-hidden="true" className="text-muted-foreground">·</span>
                <span className={attempt.judgedBy === 'model' ? 'font-semibold' : 'text-muted-foreground'}>
                  {judgeLabel(attempt.judgedBy)}
                </span>
                {attempt.counted === false ? <span className="text-muted-foreground">（不计次）</span> : null}
              </Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-footnote text-muted-foreground">还没有执行过；Agent 跑第一次后会在这里留下结果与判定方。</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" loading={downloading} onClick={onDownload}>
          {downloading ? null : <Download aria-hidden="true" />}
          下载
        </Button>
        <Button variant="outline" size="sm" onClick={onToggleRetain}>
          {asset.meta.retain ? <RotateCcw aria-hidden="true" /> : null}
          {asset.meta.retain ? '取消保留' : '保留'}
        </Button>
        <Button variant="ghost" size="sm" className="text-danger" onClick={onDelete}>
          <Trash2 aria-hidden="true" />
          删除
        </Button>
        {link ? (
          <a
            className="ml-auto inline-flex items-center gap-1 text-footnote text-primary-text underline-offset-4 hover:underline"
            href={link}
            target="_blank"
            rel="noreferrer noopener"
          >
            作业站原帖
            <ExternalLink aria-hidden="true" className="size-3.5" />
          </a>
        ) : asset.meta.siteId ? (
          <span className="ml-auto text-footnote text-muted-foreground">作业站 #{asset.meta.siteId}</span>
        ) : null}
      </div>
    </li>
  )
}

/**
 * 「作业」页（规格 §9.10、Q49）：列出全部作业资源的来源、关卡、标题、版本链、
 * 每次执行的结果与判定方、创建时间与是否保留；可以查看 JSON、下载、删除与保留。
 *
 * 来源筛选在已取回的列表里做（列表端点只支持 `q`，不支持按来源过滤）；版本链同理，
 * 父版本不在已取回的页里时显示占位，不假装它是第一版。
 */
export default function CopilotsPage() {
  const queryClient = useQueryClient()
  const [source, setSource] = useState<string>(ALL_SOURCES)
  const [text, setText] = useState('')
  const [viewing, setViewing] = useState<CopilotAsset | null>(null)
  const [deleting, setDeleting] = useState<CopilotAsset | null>(null)
  const query = useDebouncedValue(text, 300)

  const list = useInfiniteQuery({
    queryKey: copilotKeys.list({ q: query }),
    queryFn: ({ pageParam, signal }) => fetchCopilots(
      { page: pageParam, size: COPILOT_PAGE_SIZE, ...(query.trim() ? { q: query.trim() } : {}) },
      signal,
    ),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.size < last.total ? last.page + 1 : undefined),
    retry: false,
  })

  const assets = useMemo(() => (list.data?.pages ?? []).flatMap((page) => page.items), [list.data?.pages])
  const total = list.data?.pages.at(-1)?.total ?? assets.length
  const lookup = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets])
  const rows = source ? assets.filter((asset) => asset.meta.source === source) : assets
  const searching = query !== text || list.isFetching

  const invalidate = () => queryClient.invalidateQueries({ queryKey: copilotKeys.lists() })

  const download = useMutation({
    mutationFn: (asset: CopilotAsset) => fetchCopilotDetail(asset.id),
    onSuccess: (detail) => toast.success(`已下载 ${downloadCopilotJson(detail)}`),
  })
  const retain = useMutation({
    mutationFn: (asset: CopilotAsset) => setCopilotRetain(asset.id, !asset.meta.retain),
    onSuccess: async (_updated, asset) => {
      toast.success(asset.meta.retain ? '已取消保留' : '已保留，这份作业不会被自动清理')
      await invalidate()
    },
  })
  const remove = useMutation({
    mutationFn: (asset: CopilotAsset) => deleteCopilot(asset.id),
    onSuccess: async (_removed, asset) => {
      toast.success(`已删除「${titleText(asset)}」`)
      setDeleting(null)
      await invalidate()
    },
  })

  const actionError = download.error ?? retain.error ?? remove.error ?? null

  return (
    <Page title="作业" back="/more">
      <div className="space-y-4 pb-4">
        <div className="grid gap-3 rounded-xl border bg-card p-3 shadow-card sm:grid-cols-2">
          <label className="relative min-w-0">
            <span className="sr-only">搜索作业</span>
            <Input
              type="search"
              placeholder="搜索标题、关卡或说明"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="search"
              value={text}
              onChange={(event) => setText(event.currentTarget.value)}
            />
          </label>
          <label className="min-w-0">
            <span className="sr-only">来源</span>
            <NativeSelect aria-label="来源" value={source} onChange={(event) => setSource(event.currentTarget.value)}>
              <option value={ALL_SOURCES}>全部来源</option>
              {SOURCE_FILTERS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </NativeSelect>
          </label>
        </div>
        <p className="px-1 text-footnote text-muted-foreground">
          成功的作业与自己上传的作业不会被自动清理；AI 生成的失败草稿 7 天后会被清理，想留下就点「保留」。
        </p>

        {actionError ? (
          <InlineAlert tone="danger">操作失败：{errorText(actionError)}</InlineAlert>
        ) : null}

        {list.isPending ? (
          <LoadingState label="正在读取作业…" />
        ) : list.error && assets.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              title="作业列表读取失败"
              message={errorText(list.error)}
              onRetry={() => void list.refetch()}
              retrying={list.isFetching}
            />
          </div>
        ) : rows.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <EmptyState
              icon={Swords}
              title={assets.length ? '没有符合筛选的作业' : '还没有作业'}
              description={assets.length
                ? '换一个来源或清空搜索词试试'
                : '让 Agent 研究一关，或让它在作业站上找一份；保存下来的作业都会出现在这里'}
            />
          </div>
        ) : (
          <Section
            title="作业"
            description={`共 ${total} 份${source ? `（本页筛出 ${rows.length} 份）` : ''} · 每页 ${COPILOT_PAGE_SIZE} 份`}
            actions={(
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="刷新作业列表"
                onClick={() => void list.refetch()}
              >
                {list.isRefetching && !list.isFetchingNextPage ? <Spinner /> : <RefreshCw aria-hidden="true" />}
              </Button>
            )}
          >
            {list.error ? <InlineAlert tone="warning">列表刷新失败：{errorText(list.error)}</InlineAlert> : null}
            {searching ? <p role="status" className="text-footnote text-muted-foreground">正在筛选…</p> : null}
            <ul className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
              {rows.map((asset) => (
                <CopilotLine
                  key={asset.id}
                  asset={asset}
                  lookup={lookup}
                  downloading={download.isPending && download.variables?.id === asset.id}
                  onView={() => setViewing(asset)}
                  onDownload={() => download.mutate(asset)}
                  onToggleRetain={() => retain.mutate(asset)}
                  onDelete={() => setDeleting(asset)}
                />
              ))}
            </ul>
            {list.hasNextPage ? (
              <Button variant="outline" className="w-full" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                {list.isFetchingNextPage ? '正在加载…' : '加载更多'}
              </Button>
            ) : (
              <p className="py-1 text-center text-footnote text-muted-foreground">已显示全部 {rows.length} 份</p>
            )}
          </Section>
        )}
      </div>

      {viewing ? <CopilotJsonSheet key={viewing.id} asset={viewing} onClose={() => setViewing(null)} /> : null}

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
        tone="danger"
        title={deleting ? `删除「${titleText(deleting)}」？` : '删除这份作业？'}
        description="删除后不能恢复。正在被流水线、定时任务或作业授权引用的作业请先停掉它们。"
        confirmText="删除"
        onConfirm={() => {
          if (deleting) remove.mutate(deleting)
        }}
      />
    </Page>
  )
}
