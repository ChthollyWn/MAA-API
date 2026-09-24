import { useState } from 'react'
import { Link, Outlet, useNavigate } from 'react-router'
import { LogOut, Moon, Sun, SunMoon } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { BottomTabBar } from '@/components/layout/BottomTabBar'
import { Badge } from '@/components/ui/badge'
import { PwaSecurityNotice } from '@/components/pwa/PwaSecurityNotice'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/stores/auth'
import { useUiStore, type ThemePreference } from '@/stores/ui'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'

const themeLabels: Record<ThemePreference, string> = {
  dark: '深色主题',
  light: '浅色主题',
  system: '跟随系统主题',
}

export function AppShell() {
  const navigate = useNavigate()
  const token = useAuth((state) => state.token)
  const clearToken = useAuth((state) => state.clearToken)
  const theme = useUiStore((state) => state.theme)
  const setTheme = useUiStore((state) => state.setTheme)
  const [logoutError, setLogoutError] = useState<string | null>(null)
  const [loggingOut, setLoggingOut] = useState(false)
  const realtime = useRealtimeStatus()

  const logout = async () => {
    if (!token || loggingOut) return
    setLogoutError(null)
    setLoggingOut(true)
    const { error, response } = await api.DELETE('/api/system/auth/cookie')
    setLoggingOut(false)
    if (error) {
      const apiError = toApiError(error, response)
      setLogoutError(apiError.status === 403 ? '服务拒绝了登出请求，请检查凭据后重试。' : apiError.message)
      return
    }
    clearToken()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b bg-background/95 pt-safe backdrop-blur supports-[backdrop-filter]:bg-background/85">
        <div className="mx-auto flex min-h-14 max-w-3xl items-center justify-between gap-3 px-4 py-2 sm:px-6">
          <Link className="inline-flex min-h-11 items-center gap-2 rounded-md font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to="/" aria-label="MAA-API 控制台首页">
            <span aria-hidden="true" className="grid size-8 place-items-center rounded-lg bg-primary text-xs font-bold text-primary-foreground">M</span>
            <span>MAA-API</span>
          </Link>
          <div className="flex items-center gap-2">
            <Tabs aria-label="主题" value={theme} onValueChange={(value) => setTheme(value as ThemePreference)}>
              <TabsList className="h-11 min-h-11 px-1">
                <TabsTrigger className="size-11 min-h-11 px-0" value="dark" aria-label={themeLabels.dark} title={themeLabels.dark}><Moon aria-hidden="true" className="size-4" /></TabsTrigger>
                <TabsTrigger className="size-11 min-h-11 px-0" value="light" aria-label={themeLabels.light} title={themeLabels.light}><Sun aria-hidden="true" className="size-4" /></TabsTrigger>
                <TabsTrigger className="size-11 min-h-11 px-0" value="system" aria-label={themeLabels.system} title={themeLabels.system}><SunMoon aria-hidden="true" className="size-4" /></TabsTrigger>
              </TabsList>
            </Tabs>
            {token ? (
              <Button variant="ghost" size="icon" aria-label="登出" title="登出" disabled={loggingOut} onClick={() => void logout()}>
                <LogOut aria-hidden="true" />
              </Button>
            ) : null}
          </div>
        </div>
      </header>
      <PwaSecurityNotice className="mx-auto mt-3 max-w-3xl px-4 sm:px-6" />
      {realtime.status !== 'CONNECTED' ? (
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-2 text-xs text-muted-foreground sm:px-6" role="status">
          <Badge variant={realtime.status === 'OFFLINE' ? 'destructive' : 'warning'}>
            {realtime.status === 'OFFLINE' ? '离线' : '实时连接'}
          </Badge>
          <span>
            {realtime.status === 'OFFLINE'
              ? '实时连接已断开'
              : realtime.status === 'RECONNECTING'
                ? `正在重连（第 ${realtime.reconnectAttempt} 次）`
                : '正在连接实时服务'}
          </span>
        </div>
      ) : null}
      {logoutError ? (
        <div className="mx-auto max-w-3xl px-4 pt-3 sm:px-6" role="alert">
          <p className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{logoutError}</p>
        </div>
      ) : null}
      <main className="mx-auto min-h-[calc(100dvh-3.5rem)] max-w-3xl px-4 pb-[calc(3.5rem+env(safe-area-inset-bottom,0px)+1rem)] sm:px-6">
        <Outlet />
      </main>
      <BottomTabBar />
    </div>
  )
}
