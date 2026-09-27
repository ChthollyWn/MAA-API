import { Link, NavLink } from 'react-router'
import { BrandMark } from '@/components/layout/BrandMark'
import { primaryNav, secondaryNav, type NavItem } from '@/components/layout/nav'
import { StatusDot } from '@/components/ui/badge'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'
import { cn } from '@/lib/utils'

function SideLink({ to, label, icon: Icon, end }: NavItem) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          'flex h-10 items-center gap-3 rounded-lg px-3 text-[0.9375rem] font-medium text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isActive && 'bg-primary-soft text-primary-text hover:bg-primary-soft hover:text-primary-text',
        )
      }
    >
      <Icon aria-hidden="true" className="size-[1.125rem] shrink-0" />
      <span className="truncate">{label}</span>
    </NavLink>
  )
}

const realtimeLabel = {
  CONNECTED: '实时连接正常',
  CONNECTING: '正在连接实时服务',
  RECONNECTING: '实时连接重连中',
  OFFLINE: '实时连接已断开',
} as const

/** 桌面（≥1024px）左侧固定栏：主入口 + 「更多」里的次级入口直接展开。 */
export function SideNav() {
  const realtime = useRealtimeStatus()
  const tone = realtime.status === 'CONNECTED' ? 'success' : realtime.status === 'OFFLINE' ? 'danger' : 'warning'
  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r bg-surface">
      <Link to="/" className="flex h-16 shrink-0 items-center gap-2.5 px-5 font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" aria-label="MAA-API 首页">
        <BrandMark className="size-7" />
        <span className="text-[0.9375rem]">MAA-API</span>
      </Link>
      <nav aria-label="主导航" className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-3 pb-4">
        <div className="flex flex-col gap-0.5">
          {primaryNav.map((item) => <SideLink key={item.to} {...item} />)}
        </div>
        <div className="flex flex-col gap-0.5">
          <p className="px-3 pb-1.5 text-xs font-medium text-muted-foreground">系统</p>
          {secondaryNav.map(({ description: _description, ...item }) => <SideLink key={item.to} {...item} />)}
        </div>
      </nav>
      <div className="flex shrink-0 items-center gap-2 border-t px-5 py-3 text-xs text-muted-foreground" role="status">
        <StatusDot tone={tone} pulse={realtime.status === 'RECONNECTING'} />
        {realtimeLabel[realtime.status]}
      </div>
    </aside>
  )
}
