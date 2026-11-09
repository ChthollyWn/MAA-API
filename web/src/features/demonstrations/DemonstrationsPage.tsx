/** 「更多 › 示教录制」（M89 规格 §4.6）：实时画布、步骤列表、转换预览与注册确认。 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { MousePointerClick } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Badge, type StatusTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { formatDateTime } from '@/features/goals/format'
import { cn } from '@/lib/utils'
import ConvertPanel from './ConvertPanel'
import LiveCanvas from './LiveCanvas'
import StepList from './StepList'
import {
  DEMONSTRATION_FILTERS,
  deviceStatusQuery,
  useConvertDemonstration,
  useDemonstrations,
  useStartDemonstration,
  useStopDemonstration,
  type DemonstrationConvertOut,
  type DemonstrationStatus,
} from './useDemonstrations'

const STATUS_LABELS: Record<DemonstrationStatus, string> = {
  recording: '录制中',
  recorded: '已录制',
  converted: '已转换',
  discarded: '已丢弃',
}

const STATUS_TONES: Record<DemonstrationStatus, StatusTone> = {
  recording: 'warning',
  recorded: 'primary',
  converted: 'success',
  discarded: 'neutral',
}

const card = 'rounded-xl border bg-card shadow-card'

export default function DemonstrationsPage() {
  const demonstrations = useDemonstrations(DEMONSTRATION_FILTERS)
  const device = useQuery(deviceStatusQuery)
  const start = useStartDemonstration()
  const stop = useStopDemonstration()
  const convert = useConvertDemonstration()
  const [confirm, confirmElement] = useConfirm()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ id: string; data: DemonstrationConvertOut } | null>(null)
  const [stopResult, setStopResult] = useState<{ id: string; message: string } | null>(null)

  const items = demonstrations.data?.items ?? []
  const recording = items.find((item) => item.status === 'recording') ?? null
  const selected =
    recording ??
    (selectedId === null ? undefined : items.find((item) => item.id === selectedId)) ??
    items[0] ??
    null
  const previewData = preview !== null && preview.id === selected?.id ? preview.data : null

  const connected = device.data?.state === 'connected'
  const resolution = device.data?.resolution ?? null
  const deviceReady = connected && resolution !== null
  const previewing = convert.isPending && convert.variables?.register === false
  const registering = convert.isPending && convert.variables?.register === true

  const startRecording = async () => {
    try {
      const created = await start.mutateAsync()
      setSelectedId(created.id)
      setPreview(null)
      setStopResult(null)
    } catch (error) {
      toast.error(`开始示教失败：${toApiError(error).message}`)
    }
  }

  const stopRecording = async (discard: boolean) => {
    if (!recording) return
    try {
      const stopped = await stop.mutateAsync({ id: recording.id, discard })
      setSelectedId(stopped.id)
      setPreview(null)
      // 显式丢弃只改状态标签；「停止结果」只描述停止动作。
      setStopResult(
        discard
          ? null
          : {
              id: stopped.id,
              message:
                stopped.status === 'recorded' ? '示教已保存' : '示教没有步骤，已丢弃',
            },
      )
    } catch (error) {
      toast.error(`${discard ? '丢弃示教' : '停止示教'}失败：${toApiError(error).message}`)
    }
  }

  const runPreview = async () => {
    if (!selected) return
    try {
      const data = await convert.mutateAsync({ id: selected.id, register: false })
      setPreview({ id: data.id, data })
    } catch (error) {
      toast.error(`预览任务失败：${toApiError(error).message}`)
    }
  }

  const runRegister = async () => {
    if (!selected || !previewData?.compile) return
    const compile = previewData.compile
    if (!compile.valid || compile.errors.length > 0 || compile.chain_errors.length > 0) return
    const nodes = Object.keys(compile.tasks).length
    const ok = await confirm({
      title: '注册为自定义任务？',
      description: `会写入 ${nodes} 个任务节点并重载资源`,
      confirmText: '注册任务',
    })
    if (!ok) return
    try {
      const data = await convert.mutateAsync({ id: selected.id, register: true })
      setPreview({ id: data.id, data })
      toast.success(`已注册 ${nodes} 个任务`)
    } catch (error) {
      toast.error(`注册任务失败：${toApiError(error).message}`)
    }
  }

  const refresh = () => {
    void demonstrations.refetch()
    void device.refetch()
  }

  const overlay = (
    <div className="flex flex-col items-center gap-2 text-center">
      {!device.isPending && !deviceReady ? (
        <span className="rounded-full bg-black/60 px-3 py-1 text-sm text-white">设备未连接</span>
      ) : null}
      <Button
        disabled={!deviceReady || Boolean(recording)}
        loading={start.isPending}
        onClick={() => void startRecording()}
      >
        开始示教
      </Button>
    </div>
  )

  let listContent
  if (demonstrations.isPending) {
    listContent = <LoadingState label="正在读取示教列表…" />
  } else if (demonstrations.error && items.length === 0) {
    listContent = (
      <ErrorState
        title="示教列表读取失败"
        message={`读取示教列表失败：${toApiError(demonstrations.error).message}`}
        onRetry={() => void demonstrations.refetch()}
        retrying={demonstrations.isFetching}
      />
    )
  } else if (items.length === 0) {
    listContent = (
      <EmptyState
        icon={MousePointerClick}
        title="还没有示教"
        description="在画面里点一下、滑一下，停止后就能生成自定义任务"
      />
    )
  } else {
    listContent = (
      <div className="divide-y divide-border">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={selected?.id === item.id ? 'true' : undefined}
            className={cn(
              'flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
              selected?.id === item.id && 'bg-surface-2/70',
            )}
            onClick={() => setSelectedId(item.id)}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{item.title}</span>
              <span className="mt-0.5 block text-footnote text-muted-foreground">
                {item.steps.length} 步 · {formatDateTime(item.created_at)}
              </span>
            </span>
            <Badge variant={STATUS_TONES[item.status]}>{STATUS_LABELS[item.status]}</Badge>
          </button>
        ))}
      </div>
    )
  }

  return (
    <Page title="示教录制" back="/more" width="wide">
      <div className="grid gap-4 pb-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className={card}>
          <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <div className="min-w-0">
              <h2 className="text-[0.9375rem] font-semibold">示教</h2>
              {selected ? <p className="truncate text-footnote text-muted-foreground">{selected.title}</p> : null}
            </div>
            {selected ? (
              <Badge variant={STATUS_TONES[selected.status]}>{STATUS_LABELS[selected.status]}</Badge>
            ) : null}
          </header>
          <div className="space-y-3 p-4">
            <LiveCanvas
              connected={connected}
              overlay={overlay}
              recording={Boolean(recording)}
              resolution={resolution}
            />

            {recording ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={stop.isPending}
                    loading={stop.isPending && stop.variables?.discard === false}
                    onClick={() => void stopRecording(false)}
                  >
                    停止示教
                  </Button>
                  <Button
                    disabled={stop.isPending}
                    loading={stop.isPending && stop.variables?.discard === true}
                    onClick={() => void stopRecording(true)}
                    variant="outline"
                  >
                    丢弃示教
                  </Button>
                </div>
                <p className="text-footnote text-muted-foreground">
                  正在示教。这里的点击与滑动会直接操作设备，手机上同时操作会互相抢。
                </p>
              </>
            ) : null}

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-footnote text-muted-foreground">
              <span>按住 0.6 秒是长按</span>
              <span>滑动是否生效取决于设备与界面，点击最可靠</span>
            </div>

            {stopResult && selected?.id === stopResult.id ? (
              <p role="status" className="text-sm text-success">{stopResult.message}</p>
            ) : null}

            {selected && selected.steps.length > 0 ? <StepList steps={selected.steps} /> : null}

            {selected && (selected.status === 'recorded' || selected.status === 'converted') ? (
              <ConvertPanel
                demonstration={selected}
                onPreview={() => void runPreview()}
                onRegister={() => void runRegister()}
                preview={previewData}
                previewing={previewing}
                registering={registering}
              />
            ) : null}
          </div>
        </section>

        <section className={card}>
          <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <h2 className="text-[0.9375rem] font-semibold">示教列表</h2>
            <Button
              disabled={demonstrations.isFetching}
              onClick={refresh}
              size="sm"
              variant="outline"
            >
              {demonstrations.isFetching ? <Spinner /> : null}
              刷新
            </Button>
          </header>
          {listContent}
        </section>
      </div>
      {confirmElement}
    </Page>
  )
}
