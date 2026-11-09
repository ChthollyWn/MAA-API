import { ChevronRight, Map, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/states'
import { useAuthedImage } from '@/components/ui/use-authed-image'
import { kindLabel, originLabel, type NavNode } from './api'

export interface NodeListProps {
  nodes: NavNode[]
  onSelect: (node: NavNode) => void
  onCreate: () => void
}

/** `thumb_url` 形如 `/api/images/<sha256>/thumb`；取不到 sha 时按「没有缩略图」处理。 */
function thumbSha(url: string | null): string | null {
  if (!url) return null
  const match = /\/api\/images\/([0-9a-f]{64})\/thumb$/.exec(url)
  return match ? match[1] : null
}

function ExemplarThumb({ sha, alt }: { sha: string; alt: string }) {
  const image = useAuthedImage({ kind: 'image', sha256: sha, size: 'thumb' })
  return (
    <div ref={image.ref} className="size-14 shrink-0 overflow-hidden rounded-md border bg-surface-2">
      {image.url ? (
        <img src={image.url} alt={alt} loading="lazy" decoding="async" className="size-full object-cover" />
      ) : (
        <span aria-hidden="true" className="grid size-full place-items-center text-[0.625rem] text-muted-foreground">
          样本
        </span>
      )}
    </div>
  )
}

/**
 * 「界面」区的卡片列表（M86 §4.6）。
 *
 * 每张卡片是标题、id、类型、来源与样本缩略图，整张可点开编辑。空表说明种子还没写入，
 * 并给一个新建入口。
 */
export function NodeList({ nodes, onSelect, onCreate }: NodeListProps) {
  if (nodes.length === 0) {
    return (
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          icon={Map}
          title="还没有界面节点"
          description="重启服务后会写入种子界面"
          action={
            <Button size="sm" variant="outline" onClick={onCreate}>
              <Plus aria-hidden="true" />
              新建界面
            </Button>
          }
        />
      </div>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {nodes.map((node) => {
        const thumbs = node.exemplars
          .map((exemplar, index) => ({ sha: thumbSha(exemplar.thumb_url), index }))
          .filter((item): item is { sha: string; index: number } => item.sha !== null)
        return (
          <button
            key={node.id}
            type="button"
            onClick={() => onSelect(node)}
            aria-label={`编辑界面 ${node.title}`}
            className="flex min-w-0 flex-col rounded-xl border bg-card px-4 py-3 text-left shadow-card transition-colors hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="flex min-w-0 items-start gap-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.9375rem] font-medium">{node.title}</span>
                <span className="mt-0.5 block truncate text-footnote text-muted-foreground">{node.id}</span>
              </span>
              <ChevronRight aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground/70" />
            </span>
            <span className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge variant="neutral">{kindLabel(node.kind)}</Badge>
              <Badge variant="outline">{originLabel(node.origin)}</Badge>
              <span className="text-footnote text-muted-foreground">{node.exemplars.length} 个样本</span>
            </span>
            {thumbs.length > 0 ? (
              <span className="mt-2 flex gap-1.5 overflow-x-auto">
                {thumbs.map((item) => (
                  <ExemplarThumb key={`${node.id}-${item.index}`} sha={item.sha} alt={`${node.title} 样本 ${item.index + 1}`} />
                ))}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
