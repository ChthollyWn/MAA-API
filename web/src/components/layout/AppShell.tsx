import { Outlet } from 'react-router'
import { BottomTabBar } from '@/components/layout/BottomTabBar'
import { SideNav } from '@/components/layout/SideNav'
import { useVirtualKeyboard } from '@/components/layout/use-virtual-keyboard'
import { ConfirmationDock } from '@/features/confirmations/ConfirmationDock'
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media'
import { cn } from '@/lib/utils'

/**
 * 应用外壳：手机与平板用底部 Tab，桌面（≥1024px）用左侧栏。
 * 页面自己渲染标题栏（`Page`），外壳只负责导航、安全区与全局待确认面板。
 */
export function AppShell() {
  const desktop = useMediaQuery(DESKTOP_QUERY)
  const keyboardOpen = useVirtualKeyboard()
  return (
    <div className="min-h-dvh bg-background text-foreground">
      {desktop ? <SideNav /> : null}
      <main className={cn('flex min-h-dvh flex-col pb-[calc(var(--nav-offset)+1.5rem)]', desktop && 'pl-60')}>
        <Outlet />
      </main>
      {!desktop && !keyboardOpen ? <BottomTabBar /> : null}
      <ConfirmationDock />
    </div>
  )
}
