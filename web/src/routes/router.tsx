import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, Navigate, RouterProvider, useLocation, useNavigate, type RouteObject } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, LoaderCircle } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { AppShell } from '@/components/layout/AppShell'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/layout/PageHeader'
import { useAuth } from '@/stores/auth'

const DashboardPage = lazy(() => import('@/routes/dashboard'))
const LogsPage = lazy(() => import('@/routes/logs'))
const NotFoundPage = lazy(() => import('@/routes/not-found'))
const TasksPage = lazy(() => import('@/routes/tasks'))
const UpdatesPage = lazy(() => import('@/routes/updates'))
const SchedulesPage = lazy(() => import('@/routes/schedules'))
const SettingsPage = lazy(() => import('@/routes/settings'))
const MorePage = lazy(() => import('@/routes/more'))
const ApiConsolePage = lazy(() => import('@/features/api-console'))
const AuditPage = lazy(() => import('@/features/audit/AuditPage'))
const COOKIE_AUTH_KEY = (token: string) => ['auth', 'cookie', token] as const
const TASK_VIEW_PATHS = { create: '/tasks', queue: '/tasks/queue', history: '/tasks/history' } as const

function taskViewForPath(pathname: string): keyof typeof TASK_VIEW_PATHS {
  if (pathname === TASK_VIEW_PATHS.queue) return 'queue'
  if (pathname === TASK_VIEW_PATHS.history || /^\/tasks\/history\/[^/]+\/?$/.test(pathname)) return 'history'
  return 'create'
}

function taskPipelineIdFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/tasks\/history\/([^/]+)\/?$/)
  if (!match?.[1]) return null
  try {
    return decodeURIComponent(match[1])
  } catch {
    return match[1]
  }
}

function TasksRoutePage() {
  const location = useLocation()
  const navigate = useNavigate()
  const selectedView = taskViewForPath(location.pathname)
  const pipelineId = taskPipelineIdFromPath(location.pathname)
  return <Suspended><TasksPage
    view={selectedView}
    routePipelineId={pipelineId}
    onViewChange={(view) => navigate(TASK_VIEW_PATHS[view])}
    onPipelineChange={(id) => navigate(id ? `/tasks/history/${encodeURIComponent(id)}` : TASK_VIEW_PATHS.history)}
  /></Suspended>
}

async function exchangeStoredCookie(token: string): Promise<true> {
  const { error, response } = await api.POST('/api/system/auth/cookie', {
    headers: { 'X-Token': token },
  })
  if (error) throw toApiError(error, response)
  return true
}

function LoadingPage() {
  return <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground" role="status"><LoaderCircle className="size-4 animate-spin" />正在载入页面</div>
}

function RouteUnavailable({ title, description }: { title: string; description: string }) {
  return (
    <>
      <PageHeader title={title} />
      <Card className="mt-5 border-dashed">
        <CardHeader><CardTitle className="text-base">暂未开放</CardTitle><CardDescription>{description}</CardDescription></CardHeader>
      </Card>
    </>
  )
}

function AccessError({ message, onRetry, returnTo }: { message: string; onRetry: () => void; returnTo: string }) {
  return (
    <Card className="mt-5 border-destructive/40">
      <CardHeader><AlertTriangle aria-hidden="true" className="mb-1 size-5 text-warning" /><CardTitle className="text-base">连接或认证失败</CardTitle><CardDescription>{message}</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={onRetry}>重试</Button>
        <Button asChild variant="ghost"><Link to={`/login?returnTo=${encodeURIComponent(returnTo)}`}>重新输入 token</Link></Button>
      </CardContent>
    </Card>
  )
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const location = useLocation()
  const token = useAuth((state) => state.token)
  const health = useQuery({
    queryKey: keys.system.health(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/system/health')
      if (error) throw toApiError(error, response)
      return data
    },
    staleTime: 30_000,
  })
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
      { index: true, element: <ProtectedRoute><Suspended><DashboardPage /></Suspended></ProtectedRoute> },
      { path: 'tasks', element: <ProtectedRoute><TasksRoutePage /></ProtectedRoute> },
      { path: 'tasks/queue', element: <ProtectedRoute><TasksRoutePage /></ProtectedRoute> },
      { path: 'tasks/history', element: <ProtectedRoute><TasksRoutePage /></ProtectedRoute> },
      { path: 'tasks/history/:pipelineId', element: <ProtectedRoute><TasksRoutePage /></ProtectedRoute> },
      { path: 'logs/*', element: <ProtectedRoute><Suspended><LogsPage /></Suspended></ProtectedRoute> },
      { path: 'agent/*', element: <ProtectedRoute><RouteUnavailable title="Agent" description="Agent 对话与操作入口尚未开放。" /></ProtectedRoute> },
      { path: 'more', element: <ProtectedRoute><Suspended><MorePage /></Suspended></ProtectedRoute> },
      { path: 'more/api-console/guide', element: <ProtectedRoute><Suspended><ApiConsolePage /></Suspended></ProtectedRoute> },
      { path: 'more/api-console', element: <ProtectedRoute><Suspended><ApiConsolePage /></Suspended></ProtectedRoute> },
      { path: 'more/audit', element: <ProtectedRoute><Suspended><AuditPage /></Suspended></ProtectedRoute> },
      { path: 'more/updates', element: <ProtectedRoute><Suspended><UpdatesPage /></Suspended></ProtectedRoute> },
      { path: 'more/schedules', element: <ProtectedRoute><Suspended><SchedulesPage /></Suspended></ProtectedRoute> },
      { path: 'more/settings', element: <ProtectedRoute><Suspended><SettingsPage /></Suspended></ProtectedRoute> },
      { path: '*', element: <ProtectedRoute><Suspended><NotFoundPage /></Suspended></ProtectedRoute> },
    ],
  },
]

export const router = createBrowserRouter(routeConfig)

export function ApplicationRouter() {
  return <RouterProvider router={router} />
}
