import { NavLink } from 'react-router'
import { primaryNav } from '@/components/layout/nav'
import { cn } from '@/lib/utils'

/** 手机与平板的底部主导航；虚拟键盘弹出时由外壳隐藏。 */
export function BottomTabBar() {
  return (
    <nav
      aria-label="主导航"
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-surface/90 pb-safe px-safe backdrop-blur-xl supports-[backdrop-filter]:bg-surface/75"
    >
      <div className="mx-auto grid h-[var(--tabbar-height)] max-w-xl grid-cols-5">
        {primaryNav.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'relative flex flex-col items-center justify-center gap-1 text-[0.6875rem] font-medium leading-none text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                isActive && 'text-primary-text',
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon aria-hidden="true" className="size-[1.375rem]" strokeWidth={isActive ? 2.2 : 1.75} />
                <span>{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
