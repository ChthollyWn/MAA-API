import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { Markdown } from '@/features/agent/markdown.tsx'
import { ticketDetailQuery } from '@/features/tickets/api'
import { ENDED_STATUSES, formatDateTime } from '@/features/tickets/format'
import {
  artifactDetailQueryOptions,
  saveArtifactFile,
  type ArtifactDetailView,
} from '@/features/tickets/workspace-api'
import { usePinArtifact } from './api'
import { ARTIFACT_KIND_LABELS, formatArtifactSize } from './format'
import { ViewArtifactBody } from './ViewArtifactBody'
import { VIEW_MIME } from './view/schema'

const card = 'rounded-xl border bg-card shadow-card'

function MissingArtifact() {
  return (
    <Page title="产物" back="/more/artifacts">
      <div className={card}>
        <EmptyState
          title="没有这个产物"
          action={(
            <Button asChild variant="secondary" size="sm">
              <Link to="/more/artifacts">返回产物列表</Link>
            </Button>
          )}
        />
      </div>
    </Page>
  )
}

function ArtifactBody({
  detail,
  ticketEnded,
  downloading,
  onDownload,
}: {
  detail: ArtifactDetailView
  ticketEnded: boolean
  downloading: boolean
  onDownload: () => void
}) {
  if (detail.mime === VIEW_MIME) {
    return <ViewArtifactBody key={detail.id} artifact={detail} ticketEnded={ticketEnded} />
  }
  if (detail.mime === 'text/markdown') {
    return (
      <div className={`${card} p-4`}>
        <Markdown text={detail.content} />
      </div>
    )
  }
  if (detail.mime === 'application/json' || detail.mime === 'text/plain' || detail.mime === 'text/csv') {
    return (
      <pre className="max-h-[70dvh] overflow-auto whitespace-pre-wrap break-words rounded-xl border bg-surface-2 p-3 text-sm leading-relaxed">
        {detail.content || '（空）'}
      </pre>
    )
  }
  return (
    <div className={`${card} space-y-3 p-4`}>
      <p className="text-sm text-muted-foreground">这种产物不能在页面里预览</p>
      <Button variant="outline" size="sm" loading={downloading} onClick={onDownload}>下载</Button>
    </div>
  )
}

function ArtifactDetail({ artifactId }: { artifactId: string }) {
  const detail = useQuery(artifactDetailQueryOptions(artifactId))
  const ticketId = detail.data?.ticket_id ?? ''
  const ticket = useQuery({
    ...ticketDetailQuery(ticketId),
    enabled: Boolean(ticketId),
  })
  const pin = usePinArtifact()
  const download = useMutation({
    mutationFn: (artifact: ArtifactDetailView) => saveArtifactFile(artifact),
    onSuccess: (filename) => toast.success(`已下载 ${filename}`),
    onError: (error) => toast.error(`下载产物失败：${toApiError(error).message}`),
  })

  if (detail.isPending) {
    return (
      <Page title="产物" back="/more/artifacts">
        <div className={card}><LoadingState label="正在读取产物…" /></div>
      </Page>
    )
  }

  if (detail.error) {
    const apiError = toApiError(detail.error)
    if (apiError.status === 404 || apiError.code === 'ARTIFACT_NOT_FOUND') return <MissingArtifact />
    return (
      <Page title="产物" back="/more/artifacts">
        <div className={card}>
          <ErrorState
            message={`读取产物失败：${apiError.message}`}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        </div>
      </Page>
    )
  }

  if (!detail.data) return <MissingArtifact />
  const artifact = detail.data
  const ticketTitle = ticket.data?.ticket.title
  const ticketEnded = ticket.data ? ENDED_STATUSES.includes(ticket.data.ticket.status) : false
  const downloadFile = () => download.mutate(artifact)

  const actions = (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        disabled={pin.isPending}
        onClick={() => {
          const pinned = !artifact.pinned
          pin.mutate({ artifactId: artifact.id, pinned }, {
            onSuccess: () => toast.success(pinned ? '已置顶' : '已取消置顶'),
          })
        }}
      >
        {artifact.pinned ? '取消置顶' : '置顶'}
      </Button>
      <Button variant="ghost" size="sm" loading={download.isPending} onClick={downloadFile}>
        下载
      </Button>
    </div>
  )

  return (
    <Page title={artifact.title} back="/more/artifacts" actions={actions}>
      <div className="space-y-3 pb-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-footnote text-muted-foreground">
          <Badge variant="outline">{ARTIFACT_KIND_LABELS[artifact.kind] ?? artifact.kind}</Badge>
          <span>{formatDateTime(artifact.created_at)}</span>
          <span>{formatArtifactSize(artifact.size_bytes)}</span>
          {artifact.ticket_id ? (
            <Link
              to={`/tickets/${artifact.ticket_id}`}
              className="font-medium text-primary-text underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {ticketTitle ? `所属工单「${ticketTitle}」` : `所属工单 ${artifact.ticket_id}`}
            </Link>
          ) : null}
        </div>
        {artifact.truncated ? (
          <InlineAlert
            tone="warning"
            action={(
              <Button variant="outline" size="sm" loading={download.isPending} onClick={downloadFile}>
                下载
              </Button>
            )}
          >
            内容较长，只显示前 64 KiB
          </InlineAlert>
        ) : null}
        <ArtifactBody
          detail={artifact}
          ticketEnded={ticketEnded}
          downloading={download.isPending}
          onDownload={downloadFile}
        />
      </div>
    </Page>
  )
}

/** 路由入口：`useParams` 只在有 id 时渲染带 hooks 的详情组件。 */
export default function ArtifactDetailPage() {
  const { artifactId } = useParams()
  if (!artifactId) return <MissingArtifact />
  return <ArtifactDetail artifactId={artifactId} />
}
