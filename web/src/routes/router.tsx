import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, Link, Navigate, RouterProvider, useLocation, type RouteObject } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { healthQueryOptions } from '@/lib/health'
import { useAuth } from '@/stores/auth'

const DashboardPage = lazy(() => import('@/routes/dashboard'))
const LogsPage = lazy(() => import('@/routes/logs'))
const NotFoundPage = lazy(() => import('@/routes/not-found'))
const TasksPage = lazy(() => import('@/routes/tasks'))
const PipelineDetailPage = lazy(() => import('@/routes/tasks/detail'))
const UpdatesPage = lazy(() => import('@/routes/updates'))
const SchedulesPage = lazy(() => import('@/routes/schedules'))
const ScheduleEditorPage = lazy(() => import('@/routes/schedules/editor'))
const SettingsPage = lazy(() => import('@/routes/settings'))
const MorePage = lazy(() => import('@/routes/more'))
const ApiConsolePage = lazy(() => import('@/features/api-console'))
const AuditPage = lazy(() => import('@/features/audit/AuditPage'))
const AgentPage = lazy(() => import('@/features/agent/AgentPage'))
const COOKIE_AUTH_KEY = (token: string) => ['auth', 'cookie', token] as const

/** 定时任务从「更多」迁到了「任务」下；旧地址重定向，并保留 API 调试台带来的预填 state。 */
function SchedulesRedirect() {
  const location = useLocation()
  const suffix = location.pathname.replace(/^\/more\/schedules/, '')
  return (
    <Navigate
      to={{ pathname: `/tasks/schedules${suffix}`, search: location.search, hash: location.hash }}
      state={location.state}
      replace
    />
  )
}

async function exchangeStoredCookie(token: string): Promise<true> {
  const { error, response } = await api.POST('/api/system/auth/cookie', {
    headers: { 'X-Token': token },
  })
  if (error) throw toApiError(error, response)
  return true
}

function LoadingPage() {
  return <LoadingState label="正在载入页面" className="min-h-[40dvh]" />
}

function AccessError({ message, onRetry, returnTo }: { message: string; onRetry: () => void; returnTo: string }) {
  return (
    <div className="mx-auto w-full max-w-md px-4 pt-[max(3rem,env(safe-area-inset-top))]">
      <ErrorState title="连接或认证失败" message={message} onRetry={onRetry} />
      <div className="flex justify-center">
        <Button asChild variant="ghost"><Link to={`/login?returnTo=${encodeURIComponent(returnTo)}`}>重新输入 token</Link></Button>
      </div>
    </div>
  )
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const location = useLocation()
  const token = useAuth((state) => state.token)
  const health = useQuery(healthQueryOptions)
  const cookieCheck = useQuery({
    queryKey: COOKIE_AUTH_KEY(token ?? ''),
    queryFn: () => exchangeStoredCookie(token!),
    enabled: Boolean(health.data?.auth_enabled && token),
    retry: false,
    staleTime: Infinity,
  })
  const returnTo = `${location.pathname}${location.search}${location.hash}`

  if (health.isPending) return <LoadingPage />
  if (health.error) {
    return <AccessError message={`无法连接到 MAA-API：${toApiError(health.error).message}`} onRetry={() => void health.refetch()} returnTo={returnTo} />
  }
  if (health.data?.auth_enabled && !token) {
    return <Navigate to={`/login?returnTo=${encodeURIComponent(returnTo)}`} replace />
  }
  if (health.data?.auth_enabled && token && cookieCheck.isPending) return <LoadingPage />
  if (health.data?.auth_enabled && token && cookieCheck.error) {
    const error = toApiError(cookieCheck.error)
    if (error.status === 401) return <Navigate to={`/login?returnTo=${encodeURIComponent(returnTo)}`} replace />
    return <AccessError message={error.status === 403 ? '认证请求被拒绝（403）。当前 token 仍保留，请检查连接或重新输入 token。' : error.message} onRetry={() => void cookieCheck.refetch()} returnTo={returnTo} />
  }
  return <>{children}</>
}

function ShellLayout() {
  return <AppShell />
}

const Suspended = ({ children }: { children: ReactNode }) => <Suspense fallback={<LoadingPage />}>{children}</Suspense>

/** 受保护的懒加载页面。 */
const page = (element: ReactNode) => <ProtectedRoute><Suspended>{element}</Suspended></ProtectedRoute>

export const routeConfig: RouteObject[] = [
  {
    path: '/login',
    lazy: async () => {
      const module = await import('@/routes/login')
      return { Component: module.default }
    },
  },
  {
    path: '/',
    element: <ShellLayout />,
    children: [
      { index: true, element: page(<DashboardPage />) },
      { path: 'tasks', element: page(<TasksPage />) },
      { path: 'tasks/queue', element: page(<TasksPage />) },
      { path: 'tasks/history', element: page(<TasksPage />) },
      { path: 'tasks/history/:pipelineId', element: page(<PipelineDetailPage />) },
      { path: 'tasks/schedules', element: page(<SchedulesPage />) },
      { path: 'tasks/schedules/new', element: page(<ScheduleEditorPage />) },
      { path: 'tasks/schedules/:scheduleId', element: page(<ScheduleEditorPage />) },
      { path: 'logs/*', element: page(<LogsPage />) },
      { path: 'agent/*', element: page(<AgentPage />) },
      { path: 'more', element: page(<MorePage />) },
      { path: 'more/schedules/*', element: <SchedulesRedirect /> },
      { path: 'more/api-console/guide', element: page(<ApiConsolePage />) },
      { path: 'more/api-console', element: page(<ApiConsolePage />) },
      { path: 'more/audit', element: page(<AuditPage />) },
      { path: 'more/updates', element: page(<UpdatesPage />) },
      { path: 'more/settings', element: page(<SettingsPage />) },
      { path: 'more/settings/:section', element: page(<SettingsPage />) },
      { path: '*', element: page(<NotFoundPage />) },
    ],
  },
]

export const router = createBrowserRouter(routeConfig)

export function ApplicationRouter() {
  return <RouterProvider router={router} />
}
