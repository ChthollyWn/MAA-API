import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, FileText } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { formatDateTime } from './format'
import {
  artifactDetailQueryOptions,
  artifactsQueryOptions,
  saveArtifactFile,
  type ArtifactDetailView,
  type ArtifactView,
} from './workspace-api'

/** 产物种类的中文（M75 §4.6）；未知取值原样显示。 */
export const ARTIFACT_KIND_LABELS: Record<string, string> = {
  note: '笔记',
  spill: '外置输出',
  report: '报告',
  chart: '图表',
  table: '表格',
  file: '文件',
  form: '表单',
  dashboard: '仪表盘',
}

/** 字节数 → 人读的大小；列表里只做粗略展示。 */
export function formatArtifactSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

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
                  {artifact.pinned ? <Badge variant="primary">置顶</Badge> : null}
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
    onError: (error) => toast.error(`下载失败：${toApiError(error).message}`),
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
      ) : detail.data ? (
        <div className="space-y-3 pb-4">
          {detail.data.truncated ? (
            <InlineAlert tone="warning">
              正文超过 64 KiB，这里只显示前 64 KiB。
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
