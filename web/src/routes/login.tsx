import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, KeyRound, ShieldCheck } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { PwaSecurityNotice } from '@/components/pwa/PwaSecurityNotice'
import { keys } from '@/api/keys'
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

export default function LoginPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = useAuth((state) => state.token)
  const setToken = useAuth((state) => state.setToken)
  const health = useQuery({
    queryKey: keys.system.health(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/system/health')
      if (error) throw toApiError(error, response)
      return data
    },
    staleTime: 30_000,
  })
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
      <main className="grid min-h-dvh place-items-center px-4 pt-safe pb-safe">
        <Card className="w-full max-w-sm"><CardContent className="space-y-4 p-5"><Skeleton className="h-6 w-36" /><Skeleton className="h-11 w-full" /><Skeleton className="h-11 w-full" /></CardContent></Card>
      </main>
    )
  }

  if (health.error) {
    return (
      <main className="grid min-h-dvh place-items-center px-4 pt-safe pb-safe">
        <Card className="w-full max-w-sm">
          <CardHeader><CardTitle>无法连接到 MAA-API</CardTitle><CardDescription>请检查服务是否运行后重试。</CardDescription></CardHeader>
          <CardContent><Button className="w-full" variant="outline" onClick={() => void health.refetch()}>重试连接</Button></CardContent>
        </Card>
      </main>
    )
  }

  if (health.data?.auth_enabled === false || storedTokenCheck.data) return null

  return (
    <main className="grid min-h-dvh place-items-center px-4 pt-safe pb-safe">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-3">
          <div className="grid size-11 place-items-center rounded-xl bg-primary/15 text-primary"><ShieldCheck aria-hidden="true" /></div>
          <div className="space-y-1.5">
            <CardTitle className="text-xl">连接到 MAA-API</CardTitle>
            <CardDescription>输入服务访问 token 以继续。token 仅保存在此设备。</CardDescription>
          </div>
        </CardHeader>
        <PwaSecurityNotice className="mx-4 mb-4" />
        <CardContent>
          <form className="space-y-4" onSubmit={(event) => void submit(event)}>
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
                  className="pr-12 pl-10"
                  placeholder="输入访问 token"
                  aria-describedby={visibleError ? 'login-error' : undefined}
                  required
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0"
                  aria-label={showToken ? '隐藏 token' : '显示 token'}
                  onClick={() => setShowToken((shown) => !shown)}
                >
                  {showToken ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                </Button>
              </div>
            </div>
            {visibleError ? <p id="login-error" role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{visibleError}</p> : null}
            <Button className="w-full" disabled={!value.trim() || submitting} type="submit">
              {submitting ? '正在验证…' : '登录'}
            </Button>
          </form>
          <p className="mt-5 text-center text-xs text-muted-foreground">认证后会创建用于实时连接的安全 Cookie。</p>
        </CardContent>
      </Card>
      <Link to="/" className="sr-only">返回首页</Link>
    </main>
  )
}
