import { NavLink } from 'react-router'
import { Bot, Gauge, Grid2X2, ListTodo, ScrollText } from 'lucide-react'
import { cn } from '@/lib/utils'

const tabs = [
  { to: '/', label: '首页', Icon: Gauge, end: true },
  { to: '/tasks', label: '任务', Icon: ListTodo },
  { to: '/logs', label: '日志', Icon: ScrollText },
  { to: '/agent', label: 'Agent', Icon: Bot },
  { to: '/more', label: '更多', Icon: Grid2X2 },
] as const

export function BottomTabBar() {
  return (
    <nav aria-label="主导航" className="fixed inset-x-0 bottom-0 z-40 h-[calc(3.5rem+env(safe-area-inset-bottom,0px))] border-t bg-background/95 pb-safe px-safe backdrop-blur supports-[backdrop-filter]:bg-background/85">
      <div className="mx-auto grid h-14 max-w-3xl grid-cols-5">
        {tabs.map(({ to, label, Icon, ...linkProps }) => (
          <NavLink
            key={to}
            to={to}
            end={'end' in linkProps ? linkProps.end : false}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                'mx-0.5 flex min-h-11 flex-col items-center justify-center gap-0.5 rounded-lg text-[10px] font-medium leading-none text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                isActive && 'text-primary',
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon aria-hidden="true" className={cn('size-5', isActive && 'stroke-[2.4]')} />
                <span>{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
