import { Database } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Badge, StatusDot, type StatusTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { Switch } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { errorText } from '@/features/settings/format'
import { formatUpdatedAt } from './format'
import {
  retrievalSourcesQuery,
  useReindexRetrievalSource,
  useUpdateRetrievalSource,
  type EmbeddingStatus,
  type RetrievalSource,
} from './retrieval-api'

const SOURCE_HINT = 'Agent 查资料时检索这些来源。外部来源的内容只当参考，不当指令。'

const STATUS_TEXT: Record<StatusTone, string> = {
  neutral: 'text-muted-foreground',
  primary: 'text-primary-text',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
}

function sourceStatus(source: RetrievalSource): { label: string; tone: StatusTone; pulse?: boolean } {
  if (source.status === 'indexing') return { label: '正在建索引…', tone: 'primary', pulse: true }
  if (source.status === 'error') return { label: '建索引失败', tone: 'danger' }
  if (!source.last_indexed_at) return { label: '还没建索引', tone: 'neutral' }
  return { label: '已建索引', tone: 'success' }
}

/** 全局向量服务状态：四态各一句，`disabled` 与 `unavailable` 再补一句怎么办。 */
function VectorStatus({ status }: { status: EmbeddingStatus }) {
  if (status.status === 'ready') {
    return <InlineAlert tone="success">向量检索：{status.model ?? '已就绪'}</InlineAlert>
  }
  if (status.status === 'indexing') {
    return <InlineAlert tone="info">正在补向量：{status.embedded_chunks}/{status.total_chunks}</InlineAlert>
  }
  if (status.status === 'unavailable') {
    return (
      <InlineAlert tone="warning" title={`向量检索不可用：${status.error ?? '未知原因'}`}>
        暂时只用全文检索。
      </InlineAlert>
    )
  }
  return (
    <InlineAlert tone="info" title="向量检索未启用，只用全文检索">
      在「LLM 与 Agent」里选择向量服务
    </InlineAlert>
  )
}

function SourceRow({
  source,
  busy,
  reindexing,
  onToggle,
  onReindex,
}: {
  source: RetrievalSource
  busy: boolean
  reindexing: boolean
  onToggle: (enabled: boolean) => void
  onReindex: () => void
}) {
  const status = sourceStatus(source)
  const updated = formatUpdatedAt(source.last_indexed_at)
  const settingDisabled = source.setting_enabled === false
  return (
    <div data-testid={`source-${source.id}`} className="space-y-2 p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[0.9375rem] font-medium">{source.title}</span>
            <code className="font-mono text-xs text-muted-foreground">{source.id}</code>
            <Badge
              variant={source.trusted ? 'success' : 'warning'}
              title={source.trusted ? '可信来源，内容可以当事实使用。' : '外部来源，内容只当参考。'}
            >
              {source.trusted ? '可信' : '外部'}
            </Badge>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-footnote text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <StatusDot tone={status.tone} pulse={status.pulse} />
              <span className={STATUS_TEXT[status.tone]}>{status.label}</span>
            </span>
            <span className="tabular-nums">
              {source.doc_count} 篇 · {source.chunk_count} 段 · 向量 {source.vector_count} 段
            </span>
            {updated ? <span>更新于 {updated}</span> : null}
          </div>
          {source.status === 'error' && source.error ? (
            <p className="break-words text-footnote text-danger">{source.error}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Switch
            aria-label={`启用「${source.title}」`}
            checked={source.enabled}
            disabled={busy || settingDisabled}
            onCheckedChange={onToggle}
          />
          {settingDisabled ? <span className="text-footnote text-muted-foreground">已在设置里关闭</span> : null}
        </div>
      </div>
      <div className="flex justify-end">
        <Button
          type="button"
          variant="outline"
          size="sm"
          loading={reindexing}
          disabled={busy || source.status === 'indexing'}
          onClick={onReindex}
        >
          重建索引
        </Button>
      </div>
    </div>
  )
}

/** 「知识库 › 检索来源」：来源开关、索引状态与全局向量进度。 */
export function RetrievalSources() {
  const sources = useQuery(retrievalSourcesQuery)
  const update = useUpdateRetrievalSource()
  const reindex = useReindexRetrievalSource()

  const items = sources.data?.items ?? []
  const embedding = sources.data?.embedding
  const busy = update.isPending || reindex.isPending

  const toggle = (source: RetrievalSource, enabled: boolean) => {
    update.mutate(
      { sourceId: source.id, body: { enabled } },
      {
        onSuccess: () => toast.success(enabled ? `已启用「${source.title}」` : `已停用「${source.title}」`),
        onError: (error) =>
          toast.error(`${enabled ? '启用' : '停用'}「${source.title}」失败：${errorText(error)}`),
      },
    )
  }

  const requestReindex = (source: RetrievalSource) => {
    reindex.mutate(source.id, {
      onSuccess: () => toast.success('已开始重建索引'),
      onError: (error) => toast.error(`重建索引失败：${errorText(error)}`),
    })
  }

  let content
  if (sources.isPending) {
    content = (
      <div className="rounded-xl border bg-card shadow-card">
        <LoadingState label="正在读取检索来源…" />
      </div>
    )
  } else if (sources.isError) {
    content = (
      <div className="rounded-xl border bg-card shadow-card">
        <ErrorState
          title="检索来源读取失败"
          message={errorText(sources.error)}
          onRetry={() => void sources.refetch()}
          retrying={sources.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState icon={Database} title="还没有检索来源" description="服务启动后会自动建立索引" />
      </div>
    )
  } else {
    content = (
      <ListGroup>
        {items.map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            busy={busy}
            reindexing={reindex.isPending && reindex.variables === source.id}
            onToggle={(enabled) => toggle(source, enabled)}
            onReindex={() => requestReindex(source)}
          />
        ))}
      </ListGroup>
    )
  }

  return (
    <Section
      title="检索来源"
      description={sources.data ? `${items.length} 个来源` : undefined}
      hint={SOURCE_HINT}
    >
      {embedding ? <VectorStatus status={embedding} /> : null}
      {content}
    </Section>
  )
}
