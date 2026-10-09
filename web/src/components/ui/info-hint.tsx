import { useId, useRef, useState, type ReactNode } from 'react'
import { HelpCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface InfoHintProps {
  /** 说明正文。只在用户主动查看时显示。 */
  children: ReactNode
  /** 无障碍名称，同一屏上有多个提示时用于区分。 */
  label?: string
  /** 面板对齐方向；默认左对齐，靠右侧的提示用 `end` 避免溢出。 */
  align?: 'start' | 'end'
  className?: string
}

/**
 * 小问号提示：悬浮、聚焦或点击展开一段说明。
 *
 * 用在「解释为什么 / 附带条件」这类句子原来直接铺在界面上的地方 —— 正文只留必要信息，
 * 细节挪到这里。点击可固定展开，触摸屏不必长按；Esc 关闭。
 */
export function InfoHint({ children, label = '说明', align = 'start', className }: InfoHintProps) {
  const panelId = useId()
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const containerRef = useRef<HTMLSpanElement>(null)

  const show = () => setOpen(true)
  const hide = () => {
    if (!pinned) setOpen(false)
  }

  return (
    <span
      ref={containerRef}
      className={cn('relative inline-flex align-middle', className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onBlur={(event) => {
        if (!containerRef.current?.contains(event.relatedTarget as Node | null)) {
          setPinned(false)
          setOpen(false)
        }
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.stopPropagation()
          setPinned(false)
          setOpen(false)
        }
      }}
    >
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          const next = !pinned
          setPinned(next)
          setOpen(next ? true : open)
        }}
        onFocus={show}
        className="grid size-4 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <HelpCircle aria-hidden="true" className="size-3.5" />
      </button>
      {open ? (
        <span
          id={panelId}
          role="tooltip"
          className={cn(
            'absolute top-full z-40 mt-1.5 w-60 rounded-lg border bg-card px-3 py-2 text-left text-footnote font-normal text-muted-foreground shadow-card',
            align === 'end' ? 'right-0' : 'left-0',
          )}
        >
          {children}
        </span>
      ) : null}
    </span>
  )
}
