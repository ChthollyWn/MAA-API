import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Download, FileText } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { VIEW_MIME } from '@/features/artifacts/view/schema'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { ARTIFACT_KIND_LABELS, formatArtifactSize } from '@/features/artifacts/format'
import { formatDateTime } from './format'
import {
  artifactDetailQueryOptions,
  artifactsQueryOptions,
  saveArtifactFile,
  type ArtifactDetailView,
  type ArtifactView,
} from './workspace-api'

function kindLabel(kind: string): string {
  return ARTIFACT_KIND_LABELS[kind] ?? kind
}

export interface ArtifactListProps {
  ticketId: string
}

/** 工单侧栏的产物列表（M75 §4.6）：标题、种类、大小、时间；点击拉正文。 */
export function ArtifactList({ ticketId }: ArtifactListProps) {
  const artifacts = useQuery(artifactsQueryOptions(ticketId))
  const [selected, setSelected] = useState<ArtifactView | null>(null)
  const items = artifacts.data ?? []

  return (
    <Section title="产物" description={items.length > 0 ? `共 ${items.length} 条` : undefined}>
      {artifacts.isPending ? (
        <LoadingState label="正在读取产物…" />
      ) : artifacts.error && items.length === 0 ? (
        <ErrorState
          message={`读取产物失败：${toApiError(artifacts.error).message}`}
          onRetry={() => void artifacts.refetch()}
          retrying={artifacts.isFetching}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="还没有产物"
          description="长工具输出、笔记与报告会出现在这里"
        />
      ) : (
        <ListGroup>
          {items.map((artifact) => (
            <ListItem
              key={artifact.id}
              icon={FileText}
              title={artifact.title}
              description={`${formatArtifactSize(artifact.size_bytes)} · ${formatDateTime(artifact.created_at)}`}
              trailing={(
                <>
                  <Badge variant="outline">{kindLabel(artifact.kind)}</Badge>
                  {artifact.pinned ? <Badge variant="primary">已置顶</Badge> : null}
                </>
              )}
              onClick={() => setSelected(artifact)}
              aria-label={`查看产物 ${artifact.title}`}
            />
          ))}
        </ListGroup>
      )}
      <ArtifactDrawer artifact={selected} onClose={() => setSelected(null)} />
    </Section>
  )
}

function ArtifactDrawer({
  artifact,
  onClose,
}: {
  artifact: ArtifactView | null
  onClose: () => void
}) {
  const detail = useQuery(artifactDetailQueryOptions(artifact?.id))
  const download = useMutation({
    mutationFn: (value: ArtifactDetailView) => saveArtifactFile(value),
    onSuccess: (filename) => toast.success(`已下载 ${filename}`),
    onError: (error) => toast.error(`下载产物失败：${toApiError(error).message}`),
  })

  return (
    <ResponsiveSheet
      open={Boolean(artifact)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={artifact?.title ?? '产物'}
      description={artifact
        ? `${kindLabel(artifact.kind)} · ${formatArtifactSize(artifact.size_bytes)} · ${formatDateTime(artifact.created_at)}`
        : undefined}
      desktop="panel"
    >
      {detail.isPending ? (
        <LoadingState label="正在读取正文…" />
      ) : detail.error ? (
        <ErrorState
          message={`读取产物失败：${toApiError(detail.error).message}`}
          onRetry={() => void detail.refetch()}
          retrying={detail.isFetching}
        />
      ) : detail.data && detail.data.mime === VIEW_MIME ? (
        <div className="pb-4">
          <Button asChild variant="outline" size="sm">
            <Link to={`/more/artifacts/${detail.data.id}`}>在产物页打开</Link>
          </Button>
        </div>
      ) : detail.data ? (
        <div className="space-y-3 pb-4">
          {detail.data.truncated ? (
            <InlineAlert tone="warning">
              内容较长，只显示前 64 KiB
            </InlineAlert>
          ) : null}
          <pre className="whitespace-pre-wrap break-words rounded-lg bg-surface-2 px-3 py-2.5 text-sm leading-relaxed">
            {detail.data.content || '（空）'}
          </pre>
          {detail.data.truncated ? (
            <Button
              variant="outline"
              size="sm"
              loading={download.isPending}
              disabled={download.isPending}
              onClick={() => download.mutate(detail.data)}
            >
              <Download aria-hidden="true" />
              下载全文
            </Button>
          ) : null}
        </div>
      ) : null}
    </ResponsiveSheet>
  )
}
