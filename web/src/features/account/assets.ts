import { useEffect, useState } from 'react'
import { useAuth } from '@/stores/auth'

/**
 * 卡片上要用的游戏资源地址与展示换算（M43 规格 §4.4 + 后续修订）。
 *
 * **图片走本机后端代理**（`/api/account/assets/{kind}/{id}`），不是官方 CDN 直连：官方
 * `web.hycdn.cn` 有防盗链，浏览器一定带 `Referer`，实测同一张头像不带 Referer 是 200、
 * 带上就是 403——直连的结果就是页面上全是灰占位。后端取回来落盘缓存，前端再从本机读。
 *
 * 又因为鉴权走 `X-Token` 头（`<img src>` 带不了头），这里用 `fetch` 取 blob 再转
 * object URL——与设备画面（`features/dashboard/DeviceScreenshot.tsx`）同一套写法。
 */

/** 干员头像的后端代理路径。`charId` 如 `char_002_amiya`。 */
export function avatarPath(charId: string): string {
  return `/api/account/assets/avatar/${encodeURIComponent(charId)}`
}

/** 技能图标的后端代理路径。`skillId` 如 `skchr_amiya_3`（可能含 `[]`）。 */
export function skillIconPath(skillId: string): string {
  return `/api/account/assets/skill/${encodeURIComponent(skillId)}`
}

/**
 * 模组类型字母 → 游戏里的显示字形（规格 §2.4，照一图流的口径）：
 * `X` / `Y` 原样，`D` 是 Δ、`A` 是 α。未知类型原样返回，不猜。
 */
export function moduleTypeGlyph(type: string): string {
  if (type === 'D') return 'Δ'
  if (type === 'A') return 'α'
  return type
}

/** 稀有度 → 卡片主色/副色（规格 §2.4，与游戏截图口径一致）。 */
export const RARITY_COLORS: Record<number, { primary: string; secondary: string }> = {
  6: { primary: '#FF8A3D', secondary: '#FFD24A' },
  5: { primary: '#E5C100', secondary: '#F2C75C' },
  4: { primary: '#A855F7', secondary: '#D8C6FF' },
  3: { primary: '#3B82F6', secondary: '#7FB7FF' },
  2: { primary: '#6EE7B7', secondary: '#7FD88F' },
  1: { primary: '#9CA3AF', secondary: '#C5C9D3' },
}

/** 未拥有/稀有度读不出来时的灰色。`null` 与 `0` 都走这里。 */
export const UNOWNED_COLORS = { primary: '#4B5563', secondary: '#6B7280' }

/** 进程内共享的取图缓存：同一张图被多张卡请求时只打一次网络。 */
const assetBlobs = new Map<string, Promise<Blob | null>>()

function tokenHeader(): Record<string, string> {
  const token = useAuth.getState().token
  return token ? { 'X-Token': token } : {}
}

async function loadAsset(path: string): Promise<Blob | null> {
  const response = await fetch(path, { headers: tokenHeader() })
  if (response.status === 401) useAuth.getState().onUnauthorized()
  if (!response.ok) return null
  const blob = await response.blob()
  if (!blob.type.startsWith('image/')) return null
  return blob
}

function assetBlob(path: string): Promise<Blob | null> {
  const cached = assetBlobs.get(path)
  if (cached) return cached
  const pending = loadAsset(path).catch(() => null)
  assetBlobs.set(path, pending)
  return pending
}

/**
 * 取一张资源图并转成 object URL；`path` 为 `null`（未拥有 / 没有 id）或取不到时返回 `null`。
 *
 * 卸载时 revoke object URL；同一 `path` 的 blob 在模块级缓存里复用，翻页回来不再重新下载。
 */
export function useAssetUrl(path: string | null): string | null {
  const [blob, setBlob] = useState<Blob | null>(null)

  useEffect(() => {
    if (!path) {
      setBlob(null)
      return
    }
    let active = true
    void assetBlob(path).then((next) => {
      // 竞态保护：快速翻页时旧请求的结果不能覆盖新图片。
      if (active) setBlob(next)
    })
    return () => {
      active = false
    }
  }, [path])

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

/** 测试用：清掉进程内的取图缓存（避免用例之间互相串）。 */
export function resetAssetCache(): void {
  assetBlobs.clear()
}
