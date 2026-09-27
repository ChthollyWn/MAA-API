import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, KeyRound } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { BrandMark } from '@/components/layout/BrandMark'
import { PwaSecurityNotice } from '@/components/pwa/PwaSecurityNotice'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ui/states'
import { healthQueryOptions } from '@/lib/health'
import { useAuth } from '@/stores/auth'

export const COOKIE_AUTH_KEY = (token: string) => ['auth', 'cookie', token] as const

export async function exchangeCookie(token: string): Promise<true> {
  const { error, response } = await api.POST('/api/system/auth/cookie', {
    headers: { 'X-Token': token },
  })
  if (error) throw toApiError(error, response)
  return true
}

function safeReturnTo(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/login') || value.includes('\\')) return '/'
  return value
}

function LoginFrame({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-background px-5 pt-safe pb-safe">
      <div className="w-full max-w-sm py-10">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <BrandMark className="size-14 shadow-float" />
          <p className="text-[0.9375rem] font-semibold tracking-tight">MAA-API</p>
        </div>
        {children}
      </div>
    </main>
  )
}

export default function LoginPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = useAuth((state) => state.token)
  const setToken = useAuth((state) => state.setToken)
  const health = useQuery(healthQueryOptions)
  const storedTokenCheck = useQuery({
    queryKey: COOKIE_AUTH_KEY(token ?? ''),
    queryFn: () => exchangeCookie(token!),
    enabled: Boolean(health.data?.auth_enabled && token),
    retry: false,
    staleTime: Infinity,
  })
  const [value, setValue] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [inlineError, setInlineError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const returnTo = safeReturnTo(new URLSearchParams(location.search).get('returnTo'))
  const storedAuthError = !value && storedTokenCheck.error
    ? toApiError(storedTokenCheck.error).status === 403
      ? '服务拒绝了已保存 token 的认证请求（403）。token 仍保留，请输入正确的访问 token 后重试。'
      : toApiError(storedTokenCheck.error).status === 401
        ? '已保存的 token 无效，请重新输入。'
        : `无法完成认证：${toApiError(storedTokenCheck.error).message}`
    : null
  const visibleError = inlineError ?? storedAuthError

  useEffect(() => {
    if (health.data?.auth_enabled === false) navigate(returnTo, { replace: true })
  }, [health.data?.auth_enabled, navigate, returnTo])

  useEffect(() => {
    if (storedTokenCheck.data) navigate(returnTo, { replace: true })
  }, [navigate, returnTo, storedTokenCheck.data])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = value.trim()
    if (!normalized || submitting) return
    setInlineError(null)
    setSubmitting(true)
    try {
      await exchangeCookie(normalized)
      setToken(normalized)
      queryClient.setQueryData(COOKIE_AUTH_KEY(normalized), true)
      navigate(returnTo, { replace: true })
    } catch (error) {
      const apiError = toApiError(error)
      setInlineError(
        apiError.status === 401
          ? 'Token 无效，请检查后重试。'
          : apiError.status === 403
            ? '服务拒绝了此认证请求，请确认使用的是访问 token。'
            : `无法完成登录：${apiError.message}`,
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (health.isPending) {
    return (
      <LoginFrame>
        <div className="space-y-4 rounded-2xl border bg-card p-6 shadow-card">
          <Skeleton className="h-6 w-36" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      </LoginFrame>
    )
  }

  if (health.error) {
    return (
      <LoginFrame>
        <div className="rounded-2xl border bg-card shadow-card">
          <ErrorState title="无法连接到 MAA-API" message="请检查服务是否运行后重试。" onRetry={() => void health.refetch()} retryLabel="重试连接" retrying={health.isFetching} />
        </div>
      </LoginFrame>
    )
  }

  if (health.data?.auth_enabled === false || storedTokenCheck.data) return null

  return (
    <LoginFrame>
      <div className="rounded-2xl border bg-card p-6 shadow-card">
        <div className="space-y-1.5">
          <h1 className="text-title font-semibold tracking-tight">连接到 MAA-API</h1>
          <p className="text-sm text-muted-foreground">输入服务访问 token 以继续。token 仅保存在此设备。</p>
        </div>
        <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
          <div className="space-y-2">
            <Label htmlFor="access-token">访问 token</Label>
            <div className="relative">
              <KeyRound aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="access-token"
                name="token"
                type={showToken ? 'text' : 'password'}
                autoComplete="current-password"
                autoCapitalize="none"
                spellCheck={false}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                className="pl-10 pr-12 font-mono placeholder:font-sans"
                placeholder="输入访问 token"
                aria-describedby={visibleError ? 'login-error' : undefined}
                aria-invalid={visibleError ? true : undefined}
                required
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-0 top-0 text-muted-foreground"
                aria-label={showToken ? '隐藏 token' : '显示 token'}
                onClick={() => setShowToken((shown) => !shown)}
              >
                {showToken ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              </Button>
            </div>
          </div>
          {visibleError ? <div id="login-error"><InlineAlert tone="danger" role="alert">{visibleError}</InlineAlert></div> : null}
          <Button className="w-full" size="lg" disabled={!value.trim()} loading={submitting} type="submit">
            {submitting ? '正在验证…' : '登录'}
          </Button>
        </form>
        <p className="mt-5 text-center text-footnote text-muted-foreground">认证后会创建用于实时连接的安全 Cookie。</p>
        <PwaSecurityNotice className="mt-5" />
      </div>
      <Link to="/" className="sr-only">返回首页</Link>
    </LoginFrame>
  )
}
