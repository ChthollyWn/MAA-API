import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Camera, LoaderCircle, RefreshCw } from 'lucide-react'
import { ApiError, apiErrorFromResponse, toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuth } from '@/stores/auth'

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

export interface DeviceScreenshotProps {
  connected: boolean
  deviceKnown: boolean
  loading: boolean
  pipelineRunning: boolean
}

export function DeviceScreenshot({ connected, deviceKnown, loading, pipelineRunning }: DeviceScreenshotProps) {
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
    <Card aria-labelledby="device-screenshot-title">
      <CardHeader className="flex-row items-center justify-between gap-3">
        <div>
          <CardTitle id="device-screenshot-title">设备截图</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {pipelineRunning ? '流水线运行中，每 5 秒更新' : '手动刷新以获取当前画面'}
          </p>
        </div>
        <Button
          aria-label="刷新设备截图"
          disabled={!connected || screenshot.isFetching}
          onClick={() => void screenshot.refetch()}
          size="sm"
          variant="outline"
        >
          {screenshot.isFetching
            ? <LoaderCircle aria-hidden="true" className="animate-spin" />
            : <RefreshCw aria-hidden="true" />}
          刷新
        </Button>
      </CardHeader>
      <CardContent>
        <div className="relative grid aspect-video place-items-center overflow-hidden rounded-lg border bg-muted" aria-live="polite">
          {loading ? (
            <p className="text-sm text-muted-foreground" role="status">正在读取设备状态</p>
          ) : !deviceKnown ? (
            <div className="px-5 text-center">
              <Camera aria-hidden="true" className="mx-auto mb-2 size-6 text-muted-foreground" />
              <p className="text-sm font-medium">设备状态暂不可用</p>
              <p className="mt-1 text-xs text-muted-foreground">请刷新服务状态后重试。</p>
            </div>
          ) : !connected ? (
            <div className="px-5 text-center">
              <Camera aria-hidden="true" className="mx-auto mb-2 size-6 text-muted-foreground" />
              <p className="text-sm font-medium">设备尚未连接</p>
              <p className="mt-1 text-xs text-muted-foreground">连接设备后可在此查看实时画面。</p>
            </div>
          ) : imageUrl ? (
            <img alt="当前设备画面" className="absolute inset-0 size-full object-contain" src={imageUrl} />
          ) : screenshot.isPending ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />正在获取截图
            </p>
          ) : message ? (
            <div className="px-5 text-center">
              <Camera aria-hidden="true" className="mx-auto mb-2 size-6 text-muted-foreground" />
              <p className="text-sm font-medium">截图获取失败</p>
              <p className="mt-1 text-xs text-muted-foreground">{message}</p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">暂无截图</p>
          )}
        </div>
        {message ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2" role="alert">
            <p className="text-sm text-destructive">{message}</p>
            <Button disabled={!connected || screenshot.isFetching} onClick={() => void screenshot.refetch()} size="sm" variant="outline">重试截图</Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
