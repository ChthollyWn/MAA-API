import type { ReactNode } from 'react'
import { pageWidthClass, type PageWidth } from '@/components/layout/Page'
import { cn } from '@/lib/utils'

/**
 * 过渡期容器：尚未按新设计重写的页面没有自己的标题栏，这里补上安全区与内边距。
 * M16 各页面重写完成后删除。
 */
export function LegacyPage({ children, width = 'default' }: { children: ReactNode; width?: PageWidth }) {
  return (
    <div className={cn('mx-auto w-full pt-[max(1rem,env(safe-area-inset-top))]', width === 'full' ? 'max-w-none' : cn('px-4 sm:px-6', pageWidthClass[width]))}>
      {children}
    </div>
  )
}
