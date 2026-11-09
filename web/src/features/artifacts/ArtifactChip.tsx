import { Link } from 'react-router'
import { ARTIFACT_KIND_LABELS } from './format'

export interface ArtifactChipProps {
  artifactId: string
  title: string
  kind: string
}

/**
 * 非视图产物的胶囊入口（M90 §4.6.3）。
 *
 * 只复用 `Chip` 的外观，不直接渲染它：`Chip` 是 button，里面不能再嵌链接；
 * 这里用应用内 `Link` 指向产物页，不渲染正文。
 */
export function ArtifactChip({ artifactId, title, kind }: ArtifactChipProps) {
  return (
    <Link
      to={`/more/artifacts/${artifactId}`}
      className="inline-flex h-9 max-w-full items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 text-sm font-medium transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="truncate">{title}</span>
      <span className="shrink-0 text-footnote text-muted-foreground">
        {ARTIFACT_KIND_LABELS[kind] ?? kind}
      </span>
    </Link>
  )
}
