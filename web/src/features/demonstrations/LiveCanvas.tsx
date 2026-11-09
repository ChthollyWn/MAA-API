/** 示教实时画布：轮询设备截图，并把页面里的点、滑、长按映射成设备操作（M89 §4.6）。 */

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { api } from '@/api/client'
import { apiErrorFromResponse, toApiError } from '@/api/errors'
import { Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import {
  classifyPointer,
  LONG_PRESS_MS,
  longPressDuration,
  mapToDevice,
  MOVE_THRESHOLD_PX,
  swipeDuration,
  type CanvasRect,
  type ScreenSize,
} from './canvas'

const SCREENSHOT_URL = '/api/device/screenshot?backend=adb&format=jpeg&size=mobile&quality=70'
const FRAME_INTERVAL_MS = 1_000

interface GestureState {
  pointerId: number
  startX: number
  startY: number
  startedAt: number
  rect: CanvasRect
  moved: boolean
  longPressSent: boolean
  timer: number | null
}

export interface LiveCanvasProps {
  recording: boolean
  connected: boolean
  resolution: ScreenSize | null
  /** 不录制时铺在画面上的操作（「开始示教」等）。 */
  overlay?: ReactNode
}

/**
 * 画布只录页面里发生的操作：用户在手机上直接点不会被采集（D1 的边界）。
 * 请求进行中忽略新的 pointerdown，避免同一次操作排成两下。
 */
export function LiveCanvas({ recording, connected, resolution, overlay }: LiveCanvasProps) {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const urlRef = useRef<string | null>(null)
  const gestureRef = useRef<GestureState | null>(null)
  const busyRef = useRef(false)

  const replaceUrl = useCallback((next: string | null) => {
    if (urlRef.current && urlRef.current !== next) URL.revokeObjectURL(urlRef.current)
    urlRef.current = next
    setUrl(next)
  }, [])

  useEffect(() => () => {
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!connected) {
      replaceUrl(null)
      setError(null)
      return
    }
    let cancelled = false
    let interval: number | undefined

    const load = async () => {
      try {
        const response = await fetch(SCREENSHOT_URL, { credentials: 'same-origin' })
        if (!response.ok) {
          let body: unknown
          try {
            body = await response.clone().json()
          } catch {
            body = undefined
          }
          throw apiErrorFromResponse(response, body)
        }
        const blob = await response.blob()
        if (cancelled) return
        replaceUrl(URL.createObjectURL(blob))
        setError(null)
      } catch (caught) {
        if (!cancelled) setError(toApiError(caught).message)
      }
    }

    setLoading(true)
    void load().finally(() => {
      if (!cancelled) setLoading(false)
    })
    if (recording) interval = window.setInterval(() => void load(), FRAME_INTERVAL_MS)
    return () => {
      cancelled = true
      if (interval !== undefined) clearInterval(interval)
    }
  }, [connected, recording, replaceUrl])

  const interactive = recording && connected && resolution !== null && !busy

  const run = async (send: () => Promise<{ error?: unknown; response: Response }>) => {
    busyRef.current = true
    setBusy(true)
    try {
      const { error, response } = await send()
      if (error) throw toApiError(error, response)
    } catch (caught) {
      toast.error(`示教失败：${toApiError(caught).message}`)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  const clearTimer = (state: GestureState) => {
    if (state.timer !== null) {
      clearTimeout(state.timer)
      state.timer = null
    }
  }

  const scheduleLongPress = (state: GestureState) => {
    state.timer = window.setTimeout(() => {
      if (gestureRef.current !== state || state.moved || busyRef.current || resolution === null) return
      state.longPressSent = true
      const held = Date.now() - state.startedAt
      const { x, y } = mapToDevice(state.startX, state.startY, state.rect, resolution)
      void run(() =>
        api.POST('/api/device/long_press', {
          body: { x, y, duration_ms: longPressDuration(held), force: false },
        }),
      )
    }, LONG_PRESS_MS)
  }

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive || gestureRef.current || event.button > 0) return
    const state: GestureState = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startedAt: Date.now(),
      rect: event.currentTarget.getBoundingClientRect(),
      moved: false,
      longPressSent: false,
      timer: null,
    }
    gestureRef.current = state
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId)
    } catch {
      // jsdom 没有指针捕获；浏览器里失败也只影响离开画布后的 pointerup。
    }
    scheduleLongPress(state)
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const state = gestureRef.current
    if (!state || state.pointerId !== event.pointerId) return
    if (Math.hypot(event.clientX - state.startX, event.clientY - state.startY) > MOVE_THRESHOLD_PX) {
      state.moved = true
      clearTimer(state)
    }
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const state = gestureRef.current
    if (!state || state.pointerId !== event.pointerId) return
    gestureRef.current = null
    clearTimer(state)
    if (state.longPressSent || resolution === null) return

    const held = Date.now() - state.startedAt
    const dx = event.clientX - state.startX
    const dy = event.clientY - state.startY
    if (state.moved) {
      // 移出 8px 后又移回来：既不算滑动，也不再补发点击。
      if (Math.hypot(dx, dy) <= MOVE_THRESHOLD_PX) return
      const start = mapToDevice(state.startX, state.startY, state.rect, resolution)
      const end = mapToDevice(event.clientX, event.clientY, state.rect, resolution)
      void run(() =>
        api.POST('/api/device/swipe', {
          body: {
            x1: start.x,
            y1: start.y,
            x2: end.x,
            y2: end.y,
            duration_ms: swipeDuration(held),
            force: false,
          },
        }),
      )
      return
    }

    const { x, y } = mapToDevice(state.startX, state.startY, state.rect, resolution)
    if (classifyPointer(dx, dy, held) === 'long_press') {
      void run(() =>
        api.POST('/api/device/long_press', {
          body: { x, y, duration_ms: longPressDuration(held), force: false },
        }),
      )
      return
    }
    void run(() =>
      api.POST('/api/device/click', {
        body: { x, y, force: false },
      }),
    )
  }

  const onPointerCancel = () => {
    const state = gestureRef.current
    if (state) clearTimer(state)
    gestureRef.current = null
  }

  return (
    <div
      data-testid="live-canvas"
      className="relative min-h-48 touch-none overflow-hidden rounded-xl border bg-black"
      style={{ touchAction: 'none' }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    >
      {url ? (
        <img
          alt="设备画面"
          className="block h-auto w-full select-none"
          draggable={false}
          src={url}
        />
      ) : (
        <div className="grid min-h-48 place-items-center px-6 text-center text-sm text-muted-foreground">
          {loading ? <span className="flex items-center gap-2"><Spinner />正在读取设备画面</span> : '还没有画面'}
        </div>
      )}
      {error ? (
        <p role="alert" className="absolute inset-x-0 bottom-0 bg-black/70 px-3 py-2 text-footnote text-white">
          读取设备画面失败：{error}
        </p>
      ) : null}
      {!recording && overlay ? (
        <div className="absolute inset-0 grid place-items-center bg-black/25 p-4">{overlay}</div>
      ) : null}
    </div>
  )
}

export default LiveCanvas
