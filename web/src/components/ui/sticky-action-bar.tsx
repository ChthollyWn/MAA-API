import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface StickyActionBarProps {
  children: ReactNode
  /** 操作栏左侧的说明，例如「3 项任务」「有未保存的更改」。 */
  summary?: ReactNode
  className?: string
}

/**
 * 页面主操作栏：手机上固定在 Tab 栏之上（随虚拟键盘上移），桌面上吸附在内容列底部。
 * 手机上会留出等高的占位，内容不会被遮住。
 */
export function StickyActionBar({ children, summary, className }: StickyActionBarProps) {
  return (
    <>
      <div aria-hidden="true" className="h-20 lg:hidden" />
      <div
        className={cn(
          'fixed inset-x-0 z-30 border-t bg-surface/90 px-4 pt-3 backdrop-blur-xl supports-[backdrop-filter]:bg-surface/75',
          'lg:sticky lg:inset-x-auto lg:mt-6 lg:rounded-xl lg:border lg:px-4 lg:shadow-float',
          className,
        )}
        style={{
          bottom: 'calc(var(--nav-offset) + var(--keyboard-inset))',
          paddingBottom: 'calc(0.75rem + var(--nav-safe))',
        }}
      >
        <div className="mx-auto flex w-full max-w-3xl items-center gap-2">
          {summary ? <div className="mr-auto min-w-0 truncate text-sm text-muted-foreground">{summary}</div> : null}
          <div className={cn('flex min-w-0 items-center gap-2', summary ? 'shrink-0' : 'flex-1 [&>*]:flex-1 lg:ml-auto lg:flex-none lg:[&>*]:flex-none')}>
            {children}
          </div>
        </div>
      </div>
    </>
  )
}
