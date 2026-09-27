import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Camera, Expand, RefreshCw } from 'lucide-react'
import { ApiError, apiErrorFromResponse, toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { ImageViewer } from '@/components/ui/image-viewer'
import { Spinner } from '@/components/ui/states'
import { useAuth } from '@/stores/auth'
import { cn } from '@/lib/utils'

const SCREENSHOT_URL = '/api/device/screenshot?backend=adb&format=jpeg&size=mobile'

async function fetchScreenshot(): Promise<Blob> {
  const response = await fetch(SCREENSHOT_URL, {
    credentials: 'same-origin',
    headers: { Accept: 'image/jpeg' },
  })

  if (!response.ok) {
    let body: unknown
    try {
      body = await response.clone().json()
    } catch {
      body = undefined
    }
    if (response.status === 401) useAuth.getState().onUnauthorized()
    throw apiErrorFromResponse(response, body)
  }

  const blob = await response.blob()
  if (!blob.type.startsWith('image/')) {
    throw new ApiError({ message: '服务没有返回设备图像，请稍后重试。', status: response.status })
  }
  return blob
}

function useObjectUrl(blob: Blob | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!blob) {
      setUrl(null)
      return
    }
    const nextUrl = URL.createObjectURL(blob)
    setUrl(nextUrl)
    return () => URL.revokeObjectURL(nextUrl)
  }, [blob])

  return url
}

function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <div className="px-6 text-center">
      <Camera aria-hidden="true" className="mx-auto mb-2 size-6 text-muted-foreground" />
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-footnote text-muted-foreground">{description}</p>
    </div>
  )
}

export interface DeviceScreenshotProps {
  connected: boolean
  deviceKnown: boolean
  loading: boolean
  pipelineRunning: boolean
}

/** 设备画面：运行中每 5 秒自动刷新，空闲时手动刷新；点图片全屏查看。 */
export function DeviceScreenshot({ connected, deviceKnown, loading, pipelineRunning }: DeviceScreenshotProps) {
  const [viewerOpen, setViewerOpen] = useState(false)
  const screenshot = useQuery({
    queryKey: ['dashboard', 'device-screenshot'],
    queryFn: fetchScreenshot,
    enabled: connected,
    refetchInterval: connected && pipelineRunning ? 5_000 : false,
    retry: false,
  })
  const imageUrl = useObjectUrl(screenshot.data)
  const message = screenshot.error ? toApiError(screenshot.error).message : null

  return (
    <section aria-labelledby="device-screenshot-title" className="rounded-xl border bg-card p-4 shadow-card">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 id="device-screenshot-title" className="text-[0.9375rem] font-semibold">设备画面</h2>
          <p className="mt-0.5 text-footnote text-muted-foreground">
            {pipelineRunning ? '任务运行中，每 5 秒自动刷新' : '点右侧按钮获取当前画面'}
          </p>
        </div>
        <Button
          aria-label="刷新设备截图"
          disabled={!connected || screenshot.isFetching}
          onClick={() => void screenshot.refetch()}
          size="icon-sm"
          variant="outline"
        >
          {screenshot.isFetching ? <Spinner /> : <RefreshCw aria-hidden="true" />}
        </Button>
      </div>
      <div
        className={cn('relative mt-3 grid aspect-video place-items-center overflow-hidden rounded-lg bg-surface-2', imageUrl && 'bg-black')}
        aria-live="polite"
      >
        {loading ? (
          <p className="text-sm text-muted-foreground" role="status">正在读取设备状态</p>
        ) : !deviceKnown ? (
          <Placeholder title="设备状态暂不可用" description="请刷新服务状态后重试。" />
        ) : !connected ? (
          <Placeholder title="设备尚未连接" description="连接设备后可在此查看实时画面。" />
        ) : imageUrl ? (
          <button
            type="button"
            className="group absolute inset-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            aria-label="全屏查看设备画面"
            onClick={() => setViewerOpen(true)}
          >
            <img alt="当前设备画面" className="size-full object-contain" src={imageUrl} />
            <span aria-hidden="true" className="absolute bottom-2 right-2 grid size-8 place-items-center rounded-full bg-black/45 text-white opacity-80 transition-opacity group-hover:opacity-100">
              <Expand className="size-4" />
            </span>
          </button>
        ) : screenshot.isPending ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><Spinner />正在获取截图</p>
        ) : message ? (
          <Placeholder title="截图获取失败" description={message} />
        ) : (
          <p className="text-sm text-muted-foreground">暂无截图</p>
        )}
      </div>
      {message ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2" role="alert">
          <p className="min-w-0 text-sm text-danger">{message}</p>
          <Button disabled={!connected || screenshot.isFetching} onClick={() => void screenshot.refetch()} size="sm" variant="outline">重试截图</Button>
        </div>
      ) : null}
      <ImageViewer open={viewerOpen} onOpenChange={setViewerOpen} src={imageUrl} alt="设备画面（全屏）" caption="设备画面" />
    </section>
  )
}
