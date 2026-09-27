import type { ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { ChevronLeft, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'
import { useConfirmationUi } from '@/stores/confirmations-ui'
import { cn } from '@/lib/utils'

export type PageWidth = 'narrow' | 'default' | 'wide' | 'full'

export const pageWidthClass: Record<PageWidth, string> = {
  narrow: 'max-w-xl',
  default: 'max-w-3xl',
  wide: 'max-w-6xl',
  full: 'max-w-none',
}

export interface TopBarProps {
  title: string
  /** 子页面的上级路径；有值时显示返回按钮。 */
  back?: string
  /** 标题栏右侧的页面操作。 */
  actions?: ReactNode
  /** 随标题栏吸顶的工具条，例如分段导航、搜索与筛选。 */
  toolbar?: ReactNode
  width?: PageWidth
  /** 首页用横幅提示待确认，不在标题栏重复显示。 */
  hideConfirmationEntry?: boolean
}

function RealtimeBanner() {
  const realtime = useRealtimeStatus()
  if (realtime.status !== 'OFFLINE' && realtime.status !== 'RECONNECTING') return null
  return (
    <div role="status" className="border-t border-warning/15 bg-warning-soft px-4 py-1.5 text-center text-xs font-medium text-warning">
      {realtime.status === 'OFFLINE'
        ? '实时连接已断开，页面数据可能不是最新'
        : `正在重连实时服务（第 ${realtime.reconnectAttempt} 次）`}
    </div>
  )
}

/** 页面标题栏：吸顶、适配刘海安全区；子页面带返回按钮。 */
export function TopBar({ title, back, actions, toolbar, width = 'default', hideConfirmationEntry = false }: TopBarProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const pendingCount = useConfirmationUi((state) => state.pendingCount)
  const openConfirmations = useConfirmationUi((state) => state.setOpen)
  const goBack = () => {
    // 应用内有上一页时后退，保持浏览器历史自然；从通知深链直接打开时回到上级页面。
    if (location.key !== 'default') navigate(-1)
    else if (back) navigate(back, { replace: true })
  }
  return (
    <header className="sticky top-0 z-30 border-b border-border/70 bg-background/85 pt-safe backdrop-blur-xl supports-[backdrop-filter]:bg-background/70">
      <div className={cn('mx-auto flex h-14 w-full items-center gap-1.5 px-4 sm:px-6 lg:px-8', pageWidthClass[width])}>
        {back ? (
          <Button variant="ghost" size="icon" className="-ml-3 shrink-0" aria-label="返回" onClick={goBack}>
            <ChevronLeft aria-hidden="true" className="size-6!" />
          </Button>
        ) : null}
        <h1 className={cn('min-w-0 flex-1 truncate font-semibold tracking-tight', back ? 'text-headline' : 'text-title')}>{title}</h1>
        {!hideConfirmationEntry && pendingCount > 0 ? (
          <button
            type="button"
            onClick={() => openConfirmations(true)}
            className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-warning-soft px-3 text-[0.8125rem] font-semibold text-warning transition-opacity hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ShieldAlert aria-hidden="true" className="size-4" />
            {pendingCount} 项待确认
          </button>
        ) : null}
        {actions ? <div className="-mr-2 flex shrink-0 items-center gap-0.5">{actions}</div> : null}
      </div>
      {toolbar ? <div className={cn('mx-auto w-full px-4 pb-3 sm:px-6 lg:px-8', pageWidthClass[width])}>{toolbar}</div> : null}
      <RealtimeBanner />
    </header>
  )
}

export interface PageProps extends TopBarProps {
  children: ReactNode
  className?: string
  contentClassName?: string
}

/** 页面框架：标题栏 + 居中的内容列。每个路由页面都用它。 */
export function Page({ children, className, contentClassName, ...topBar }: PageProps) {
  return (
    <div className={cn('flex min-h-full flex-col', className)}>
      <TopBar {...topBar} />
      <div className={cn('mx-auto w-full flex-1 px-4 pt-4 sm:px-6 lg:px-8 lg:pt-6', pageWidthClass[topBar.width ?? 'default'], contentClassName)}>
        {children}
      </div>
    </div>
  )
}
