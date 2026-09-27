import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Expand, ImageOff } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { ImageViewer } from '@/components/ui/image-viewer'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Button } from '@/components/ui/button'
import { Section } from '@/components/ui/section'
import { LoadingState, Spinner } from '@/components/ui/states'
import { errorMessage } from '../form'
import { formatTime } from '../pipeline-format'

interface ScreenshotRecord {
  id: string
  pipeline_id: string
  task_id: string | null
  trigger: string
  backend: string
  path: string
  format: string
  width: number
  height: number
  size_bytes: number
  deleted_at: string | null
  created_at: string | null
}

const triggers: Record<string, string> = {
  task_snapshot: '任务留档',
  manual: '手动截图',
  agent: 'Agent 截图',
  crash: '内核崩溃现场',
  update: '更新对照',
}

function triggerLabel(trigger: string): string {
  return triggers[trigger] ?? trigger
}

/** 文件名是内容哈希的截图走图片服务的缩略图与原图；其余直接读截图记录（原图）。 */
function imageSource(shot: ScreenshotRecord): { kind: 'hashed'; sha256: string } | { kind: 'record'; id: string } {
  const basename = shot.path.split('/').at(-1) ?? ''
  const match = /^([0-9a-f]{64})(?:\.[a-zA-Z0-9.]+)?$/.exec(basename)
  return match ? { kind: 'hashed', sha256: match[1] } : { kind: 'record', id: shot.id }
}

async function fetchImage(shot: ScreenshotRecord, size: 'thumb' | 'full'): Promise<Blob> {
  const source = imageSource(shot)
  const result = source.kind === 'record'
    ? await api.GET('/api/screenshots/{id}', { params: { path: { id: source.id } }, parseAs: 'blob' })
    : size === 'thumb'
      ? await api.GET('/api/images/{sha256}/thumb', { params: { path: { sha256: source.sha256 } }, parseAs: 'blob' })
      : await api.GET('/api/images/{sha256}/full', { params: { path: { sha256: source.sha256 } }, parseAs: 'blob' })
  if (result.error) throw toApiError(result.error, result.response)
  return result.data as unknown as Blob
}

function useObjectUrl(blob: Blob | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!blob) {
      setUrl(null)
      return
    }
    const next = URL.createObjectURL(blob)
    setUrl(next)
    return () => URL.revokeObjectURL(next)
  }, [blob])
  return url
}

function altText(shot: ScreenshotRecord, index: number): string {
  return `流水线截图 ${index + 1}，${triggerLabel(shot.trigger)}，${shot.width}×${shot.height}`
}

interface Viewing {
  shot: ScreenshotRecord
  index: number
  thumbUrl: string
}

function ScreenshotTile({ shot, index, onOpen }: { shot: ScreenshotRecord; index: number; onOpen: (viewing: Viewing) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [nearViewport, setNearViewport] = useState(typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setNearViewport(true)
        observer.disconnect()
      }
    }, { rootMargin: '200px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  const expired = shot.deleted_at !== null
  const image = useQuery({
    queryKey: [...keys.pipelines.detail(shot.pipeline_id), 'screenshot-image', shot.id],
    queryFn: () => fetchImage(shot, 'thumb'),
    enabled: !expired && nearViewport,
    staleTime: Infinity,
    retry: false,
  })
  const url = useObjectUrl(image.data)
  const alt = altText(shot, index)
  const placeholder = expired
    ? '截图已过期清理'
    : image.error
      ? `截图读取失败：${errorMessage(image.error)}`
      : null

  return (
    <div ref={ref} className="min-w-0">
      {url ? (
        <button
          type="button"
          aria-label={`查看${alt}`}
          onClick={() => onOpen({ shot, index, thumbUrl: url })}
          className="group relative block aspect-video w-full overflow-hidden rounded-lg border bg-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <img src={url} alt={alt} loading="lazy" decoding="async" className="size-full object-contain" />
          <span aria-hidden="true" className="absolute bottom-1.5 right-1.5 grid size-7 place-items-center rounded-full bg-black/50 text-white opacity-85 transition-opacity group-hover:opacity-100">
            <Expand className="size-3.5" />
          </span>
        </button>
      ) : (
        <div className="grid aspect-video place-items-center rounded-lg border bg-surface-2 px-3 text-center text-footnote text-muted-foreground">
          {placeholder ? (
            <span className="flex flex-col items-center gap-1.5"><ImageOff aria-hidden="true" className="size-5" />{placeholder}</span>
          ) : (
            <span role="status" className="flex items-center gap-2"><Spinner />正在加载截图</span>
          )}
        </div>
      )}
      <p className="mt-1.5 truncate text-xs text-muted-foreground">{triggerLabel(shot.trigger)} · {formatTime(shot.created_at)}</p>
    </div>
  )
}

function ScreenshotViewer({ viewing, onClose }: { viewing: Viewing | null; onClose: () => void }) {
  // 关闭动画期间继续显示刚才那张。
  const [shown, setShown] = useState<Viewing | null>(viewing)
  if (viewing && viewing !== shown) setShown(viewing)
  const shot = shown?.shot
  const hashed = shot ? imageSource(shot).kind === 'hashed' : false
  const full = useQuery({
    queryKey: [...keys.pipelines.detail(shot?.pipeline_id ?? ''), 'screenshot-full', shot?.id ?? ''],
    queryFn: () => fetchImage(shot!, 'full'),
    enabled: viewing !== null && hashed,
    staleTime: Infinity,
    retry: false,
  })
  const fullUrl = useObjectUrl(full.data)
  const kilobytes = shot ? Math.max(1, Math.round(shot.size_bytes / 1024)) : 0
  return (
    <ImageViewer
      open={viewing !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      src={fullUrl ?? shown?.thumbUrl ?? null}
      alt={shot && shown ? `${altText(shot, shown.index)}（原图）` : '流水线截图'}
      caption={shot ? `${triggerLabel(shot.trigger)} · ${shot.width}×${shot.height} · ${kilobytes} KB · ${formatTime(shot.created_at, { seconds: true })}` : undefined}
    />
  )
}

/** 流水线截图网格：缩略图进入视野再加载，点开全屏看原图。 */
export function PipelineScreenshots({ pipelineId, active }: { pipelineId: string; active: boolean }) {
  const [viewing, setViewing] = useState<Viewing | null>(null)
  const screenshots = useQuery({
    queryKey: [...keys.pipelines.detail(pipelineId), 'screenshots'],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/{pipeline_id}/screenshots', { params: { path: { pipeline_id: pipelineId } } })
      if (error) throw toApiError(error, response)
      return data as { items: ScreenshotRecord[]; total: number }
    },
    refetchInterval: active ? 5_000 : false,
    retry: false,
  })
  const items = screenshots.data?.items ?? []

  return (
    <Section id="pipeline-screenshots" title="截图" description={items.length > 0 ? `${items.length} 张` : undefined}>
      {screenshots.isPending ? (
        <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取截图…" /></div>
      ) : screenshots.error ? (
        <InlineAlert tone="danger" action={<Button variant="outline" size="sm" onClick={() => void screenshots.refetch()}>重试</Button>}>
          截图列表读取失败：{errorMessage(screenshots.error)}
        </InlineAlert>
      ) : items.length === 0 ? (
        <p className="rounded-xl border bg-card px-4 py-3.5 text-footnote text-muted-foreground shadow-card">这条流水线没有截图。</p>
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {items.map((shot, index) => (
            <li key={shot.id}><ScreenshotTile shot={shot} index={index} onOpen={setViewing} /></li>
          ))}
        </ul>
      )}
      <ScreenshotViewer viewing={viewing} onClose={() => setViewing(null)} />
    </Section>
  )
}
