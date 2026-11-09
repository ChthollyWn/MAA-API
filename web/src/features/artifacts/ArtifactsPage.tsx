import { useInfiniteQuery } from '@tanstack/react-query'
import {
  ChartLine,
  ClipboardList,
  ExternalLink,
  File,
  FileText,
  LayoutDashboard,
  NotebookPen,
  Table,
  type LucideIcon,
} from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RemovableChip } from '@/components/ui/chip'
import { ListGroup, ListItem } from '@/components/ui/list'
import { NativeSelect } from '@/components/ui/native-select'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { artifactsListInfiniteQuery, type ArtifactKind } from './api'
import { ARTIFACT_KIND_LABELS, formatArtifactSize } from './format'
import { formatDateTime } from '@/features/tickets/format'

const KIND_ICONS: Record<string, LucideIcon> = {
  note: NotebookPen,
  spill: ExternalLink,
  report: FileText,
  chart: ChartLine,
  table: Table,
  file: File,
  form: ClipboardList,
  dashboard: LayoutDashboard,
}

const card = 'rounded-xl border bg-card shadow-card'

function kindOf(value: string | null): ArtifactKind | undefined {
  return value && Object.hasOwn(ARTIFACT_KIND_LABELS, value) ? value as ArtifactKind : undefined
}

/** 「更多 › 产物」（M90 §4.6.3）：全部产物的分页列表与种类筛选。 */
export default function ArtifactsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const kind = kindOf(searchParams.get('kind'))
  const ticketId = searchParams.get('ticket_id') ?? undefined
  const artifacts = useInfiniteQuery(artifactsListInfiniteQuery({ kind, ticketId }))
  const items = artifacts.data?.pages.flatMap((page) => page.items) ?? []

  const updateFilter = (name: 'kind' | 'ticket_id', value: string | null) => {
    const params = new URLSearchParams(searchParams)
    if (value) params.set(name, value)
    else params.delete(name)
    setSearchParams(params, { replace: true })
  }

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor="artifact-kind" className="text-footnote font-medium text-muted-foreground">种类</label>
      <NativeSelect
        id="artifact-kind"
        value={kind ?? ''}
        wrapperClassName="w-32"
        className="h-9"
        onChange={(event) => updateFilter('kind', event.target.value || null)}
      >
        <option value="">全部</option>
        {Object.entries(ARTIFACT_KIND_LABELS).map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </NativeSelect>
      {ticketId ? (
        <RemovableChip
          removeLabel="移除筛选条件：只看这个工单"
          onRemove={() => updateFilter('ticket_id', null)}
        >
          只看这个工单
        </RemovableChip>
      ) : null}
    </div>
  )

  let content
  if (artifacts.isPending) {
    content = <div className={card}><LoadingState label="正在读取产物…" /></div>
  } else if (artifacts.error && items.length === 0) {
    content = (
      <div className={card}>
        <ErrorState
          message={`读取产物失败：${toApiError(artifacts.error).message}`}
          onRetry={() => void artifacts.refetch()}
          retrying={artifacts.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={FileText}
          title="还没有产物"
          description="Worker 生成报告、表单或看板后会列出来"
          action={(
            <Button asChild size="sm">
              <Link to="/">前往指挥台</Link>
            </Button>
          )}
        />
      </div>
    )
  } else {
    content = (
      <div className="space-y-3">
        <ListGroup>
          {items.map((artifact) => (
            <ListItem
              key={artifact.id}
              to={`/more/artifacts/${artifact.id}`}
              icon={KIND_ICONS[artifact.kind] ?? FileText}
              title={artifact.title}
              description={`${ARTIFACT_KIND_LABELS[artifact.kind] ?? artifact.kind} · ${formatArtifactSize(artifact.size_bytes)} · ${formatDateTime(artifact.created_at)}`}
              trailing={artifact.pinned ? <Badge variant="primary">已置顶</Badge> : null}
            />
          ))}
        </ListGroup>
        {artifacts.hasNextPage ? (
          <Button
            variant="outline"
            className="w-full"
            loading={artifacts.isFetchingNextPage}
            onClick={() => void artifacts.fetchNextPage()}
          >
            加载更多
          </Button>
        ) : null}
      </div>
    )
  }

  return (
    <Page title="产物" back="/more" toolbar={toolbar}>
      <div className="pb-4">{content}</div>
    </Page>
  )
}
