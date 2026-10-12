import type { LucideIcon } from 'lucide-react'
import { User } from 'lucide-react'
import { useAssetUrl } from './assets'

/**
 * 一张走本机代理取回的账号资源图（干员头像 / 技能图标 / 道具图标，M43 §4.4 + M44 §4.4）。
 *
 * **为什么不直接 `<img src>`**：取图接口要 `X-Token` 头，`<img src>` 带不了；而且官方 CDN
 * 有防盗链，浏览器直连会被 403。所以走 `fetch` + blob + object URL（在 `useAssetUrl` 里）。
 *
 * `path` 为 `null`（未拥有、没有 id）或取不到图（上游没有、鉴权失败）时一律退回占位，
 * 绝不把破图留在页面上。占位图标由 `placeholderIcon` 决定：干员是头像的 `User`，仓库道具
 * 是 `Package`——占位本身也要说得出「这里本该是什么」。
 */
export function AssetImage({
  path,
  testId,
  className,
  placeholderIcon: Placeholder = User,
  placeholderClassName,
}: {
  path: string | null
  /** 图片的 testid；占位用 `${testId}-placeholder`（与 OperatorCard 的既有约定一致）。 */
  testId: string
  className: string
  placeholderIcon?: LucideIcon
  /** 占位图标的大小类；默认 `size-4`（卡片角上的小方框）。 */
  placeholderClassName?: string
}) {
  const url = useAssetUrl(path)
  if (!url) {
    return (
      <span
        aria-hidden="true"
        data-testid={`${testId}-placeholder`}
        className="flex size-full items-center justify-center bg-muted text-muted-foreground"
      >
        <Placeholder className={placeholderClassName ?? 'size-4'} />
      </span>
    )
  }
  return <img src={url} alt="" data-testid={testId} className={className} decoding="async" />
}
