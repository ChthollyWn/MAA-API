import { cn } from '@/lib/utils'

/** 品牌插画（`web/public/logo.webp`）。图标与它同源，见 scripts/generate_pwa_icons.py。 */
export const LOGO_SRC = '/logo.webp'

/**
 * 品牌标：直接用插画本身，不再自己画几何。
 *
 * 插画自带画框与透明留白，所以这里不加圆角容器与内边距，只保留尺寸类；调用方
 * 传进来的类（如登录页的 `shadow-float`）落在 `<img>` 上。
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src={LOGO_SRC}
      alt=""
      aria-hidden="true"
      draggable={false}
      className={cn('size-8 shrink-0 select-none', className)}
    />
  )
}
