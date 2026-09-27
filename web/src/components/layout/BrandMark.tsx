import { cn } from '@/lib/utils'

/** 品牌标，与 scripts/generate_pwa_icons.py 生成的图标同一几何。 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true" className={cn('size-8 shrink-0', className)}>
      <rect width="64" height="64" rx="14" fill="#15181D" />
      <path d="M19 45V19l13 16 13-16v26" fill="none" stroke="#8B95F6" strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
