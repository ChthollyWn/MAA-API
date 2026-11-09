import { useQuery } from '@tanstack/react-query'
import { ClipboardList, LayoutDashboard } from 'lucide-react'
import { Link } from 'react-router'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Skeleton } from '@/components/ui/skeleton'
import { artifactDetailQueryOptions } from '@/features/tickets/workspace-api'
import { ARTIFACT_KIND_LABELS } from './format'
import { ArtifactChip } from './ArtifactChip'
import { ViewArtifactBody } from './ViewArtifactBody'
import { VIEW_MIME } from './view/schema'

export interface ViewArtifactCardProps {
  artifactId: string
  ticketEnded: boolean
}

const card = 'overflow-hidden rounded-xl border bg-card shadow-card'

/**
 * 会话里的视图卡（M90 §4.6.4）：从 `create_view` 的 `tool_result` 挂出来。
 *
 * 详情从 `artifact(id)` 读；视图渲染与提交都在 `ViewArtifactBody` 里，非视图
 * 产物退化成胶囊链接，不渲染正文。
 */
export function ViewArtifactCard({ artifactId, ticketEnded }: ViewArtifactCardProps) {
  const detail = useQuery(artifactDetailQueryOptions(artifactId))

  if (detail.isPending) return <Skeleton className="h-24 w-full" />
  if (detail.error) {
    return (
      <InlineAlert tone="danger">
        读取产物失败：{toApiError(detail.error).message}
      </InlineAlert>
    )
  }

  const artifact = detail.data
  if (!artifact) return null
  if (artifact.mime !== VIEW_MIME) {
    return <ArtifactChip artifactId={artifact.id} title={artifact.title} kind={artifact.kind} />
  }

  const Icon = artifact.kind === 'dashboard' ? LayoutDashboard : ClipboardList
  return (
    <section aria-label={`视图 ${artifact.title}`} className={card}>
      <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
        <p className="min-w-0 flex-1 truncate text-sm font-medium">{artifact.title}</p>
        <Badge variant="outline">{ARTIFACT_KIND_LABELS[artifact.kind] ?? artifact.kind}</Badge>
        <Link
          to={`/more/artifacts/${artifact.id}`}
          className="shrink-0 text-footnote font-medium text-primary-text underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          在产物页打开
        </Link>
      </header>
      <div className="p-3">
        <ViewArtifactBody key={artifact.id} artifact={artifact} ticketEnded={ticketEnded} />
      </div>
    </section>
  )
}
