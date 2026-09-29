import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'

export type AuthedImageSize = 'thumb' | 'full'

/**
 * 一张需要鉴权的图片从哪来（三种都在服务端的白名单里）。
 *
 * `size` 只对内容寻址的 `/api/images/<sha256>/(thumb|full)` 有意义：截图记录
 * `/api/screenshots/<id>` 与物品图标都只有一个地址。
 */
export type AuthedImageSource =
  | { kind: 'image'; sha256: string; size: AuthedImageSize }
  | { kind: 'screenshot'; id: string; size: AuthedImageSize }
  | { kind: 'resource-icon'; itemId: string }
  | null

export interface AuthedImageState {
  /** 挂到容器元素上：进入视野（或环境没有 IntersectionObserver）后才开始取图。 */
  ref: RefObject<HTMLDivElement | null>
  /** `blob:` 地址；还没有取到时为 null。 */
  url: string | null
  /** 取图失败的原因（`ApiError` 或 Error）；成功时为 null。 */
  error: unknown
  /** 正在取图。 */
  loading: boolean
  /** 重试：重新发起一次取图。 */
  retry: () => void
}

async function fetchBlob(source: NonNullable<AuthedImageSource>, signal: AbortSignal): Promise<Blob> {
  if (source.kind === 'image') {
    const result = source.size === 'thumb'
      ? await api.GET('/api/images/{sha256}/thumb', { params: { path: { sha256: source.sha256 } }, parseAs: 'blob', signal })
      : await api.GET('/api/images/{sha256}/full', { params: { path: { sha256: source.sha256 } }, parseAs: 'blob', signal })
    if (result.error) throw toApiError(result.error, result.response)
    return result.data as unknown as Blob
  }
  if (source.kind === 'screenshot') {
    const result = await api.GET('/api/screenshots/{id}', { params: { path: { id: source.id } }, parseAs: 'blob', signal })
    if (result.error) throw toApiError(result.error, result.response)
    return result.data as unknown as Blob
  }
  const result = await api.GET('/api/resources/items/icon', { params: { query: { item_id: source.itemId } }, parseAs: 'blob', signal })
  if (result.error) throw toApiError(result.error, result.response)
  return result.data as unknown as Blob
}

/**
 * 用 `X-Token`（`api` 客户端）取一张需要鉴权的图片，并管好 `blob:` 地址的生命周期。
 *
 * 这是从 `PipelineScreenshots` 里抽出来的共用逻辑：懒加载（`IntersectionObserver` +
 * 200px 预取）、blob → objectURL、卸载或换图时 revoke。**不依赖 cookie**，所以不能
 * 换成裸 `<img src>`——`/api/images/**`、`/api/screenshots/**`、`/api/resources/items/icon`
 * 都要鉴权。
 *
 * 调用方要把 `ref` 挂在一个**始终渲染**的容器上（哪怕还在加载或失败），否则观察不到
 * 它进入视野；`lazy: false` 用于「已经确定要看」的场景（全屏看图、展开后的原图）。
 */
export function useAuthedImage(source: AuthedImageSource, options: { lazy?: boolean } = {}): AuthedImageState {
  const lazy = options.lazy ?? true
  const ref = useRef<HTMLDivElement | null>(null)
  // jsdom 没有 IntersectionObserver：按「立即加载」处理（与既有 PipelineScreenshots 一致）。
  const [visible, setVisible] = useState(!lazy || typeof IntersectionObserver === 'undefined')
  const [attempt, setAttempt] = useState(0)
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const urlRef = useRef<string | null>(null)

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
    if (!lazy || visible) return
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    }, { rootMargin: '200px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [lazy, visible])

  // 依赖收敛成基本类型：调用方每次渲染新建 source 对象也不会重复取图。
  const kind = source?.kind ?? null
  const id = source ? (source.kind === 'image' ? source.sha256 : source.kind === 'screenshot' ? source.id : source.itemId) : null
  const size = source && source.kind !== 'resource-icon' ? source.size : null

  useEffect(() => {
    if (!visible || kind === null || id === null) {
      replaceUrl(null)
      setError(null)
      setLoading(false)
      return
    }
    const target = (kind === 'image'
      ? { kind, sha256: id, size }
      : kind === 'screenshot'
        ? { kind, id, size }
        : { kind, itemId: id }) as NonNullable<AuthedImageSource>
    let cancelled = false
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    void (async () => {
      try {
        const blob = await fetchBlob(target, controller.signal)
        if (cancelled) return
        replaceUrl(URL.createObjectURL(blob))
        setError(null)
      } catch (cause) {
        if (cancelled) return
        replaceUrl(null)
        setError(cause)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [visible, kind, id, size, attempt, replaceUrl])

  const retry = useCallback(() => setAttempt((value) => value + 1), [])

  return { ref, url, error, loading, retry }
}
