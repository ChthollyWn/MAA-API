import { useCallback, useEffect, useState } from 'react'
import { BellRing, LoaderCircle, Smartphone } from 'lucide-react'
import { api } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PwaSecurityNotice } from '@/components/pwa/PwaSecurityNotice'
import {
  PUSH_AVAILABLE,
  currentPermission,
  disablePush,
  enablePush,
  hasActiveSubscription,
  type PushPermission,
} from '@/pwa/push'

interface Subscription {
  id: string
  endpoint_host: string
  user_agent?: string | null
  created_at: string
  last_success_at?: string | null
  disabled: boolean
}

const PERMISSION_LABEL: Record<PushPermission, string> = {
  unsupported: '当前环境不支持',
  default: '尚未授权',
  granted: '已授权',
  denied: '已被浏览器拒绝',
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString()
}

/**
 * Web Push 设置（M14，docs/09 §13.1）。
 *
 * 非安全上下文下整块隐藏推送配置并给出说明：Service Worker 与 Push API 在局域网
 * HTTP 地址下必然不可用，这是浏览器约束，把按钮渲染出来只会让用户以为坏了。
 */
export function PushNotifications() {
  const [permission, setPermission] = useState<PushPermission>(() => currentPermission())
  const [subscribedHere, setSubscribedHere] = useState(false)
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const load = useCallback(async () => {
    const { data } = await api.GET('/api/notifications/push-subscriptions', {
      params: { query: { include_disabled: true } },
    })
    const items = (data as { items?: Subscription[] } | undefined)?.items ?? []
    setSubscriptions(items)
  }, [])

  const refreshLocalState = useCallback(async () => {
    setSubscribedHere(await hasActiveSubscription())
  }, [])

  useEffect(() => {
    if (!PUSH_AVAILABLE) return
    void load()
    void refreshLocalState()
  }, [load, refreshLocalState])

  // 权限可能在浏览器设置里被改动，回到页面时以浏览器为准重读一次。
  useEffect(() => {
    if (!PUSH_AVAILABLE) return
    const onFocus = () => setPermission(currentPermission())
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  const onEnable = async () => {
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      await enablePush()
      setPermission(currentPermission())
      setStatus('推送已启用：没有打开页面时，需要你确认的操作会推送到这台设备。')
      await Promise.all([load(), refreshLocalState()])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '启用推送失败。')
      setPermission(currentPermission())
    } finally {
      setBusy(false)
    }
  }

  const onDisable = async () => {
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      await disablePush()
      setStatus('已在本机注销推送订阅。')
      await Promise.all([load(), refreshLocalState()])
    } catch {
      setError('注销推送订阅失败，请稍后重试。')
    } finally {
      setBusy(false)
    }
  }

  const removeSubscription = async (id: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.DELETE('/api/notifications/push-subscriptions/{subscription_id}', {
        params: { path: { subscription_id: id } },
      })
      await load()
    } catch {
      setError('删除订阅失败，请稍后重试。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="space-y-1.5">
        <CardTitle className="flex items-center gap-2">
          <BellRing className="size-4" aria-hidden="true" />推送通知
        </CardTitle>
        <CardDescription>
          页面没打开时，需要人工确认的操作会以系统通知送达；通知里带深链，点击即回到确认卡片。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!PUSH_AVAILABLE ? (
          <>
            <PwaSecurityNotice />
            <p className="text-sm text-muted-foreground">
              推送需要 HTTPS 访问（例如 Tailscale Serve 提供的
              <code className="mx-1 rounded bg-muted px-1">*.ts.net</code>
              地址）并把应用安装到主屏；当前环境下该入口整体隐藏。
            </p>
          </>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={permission === 'granted' ? 'positive' : 'outline'}>
                通知权限：{PERMISSION_LABEL[permission]}
              </Badge>
              <Badge variant={subscribedHere ? 'positive' : 'outline'}>
                {subscribedHere ? '本机已订阅' : '本机未订阅'}
              </Badge>
              <Button
                type="button"
                onClick={() => void onEnable()}
                disabled={busy || permission === 'denied'}
              >
                {busy ? (
                  <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                ) : (
                  <BellRing aria-hidden="true" />
                )}
                启用推送通知
              </Button>
              <Button type="button" variant="outline" onClick={() => void onDisable()} disabled={busy}>
                注销本机订阅
              </Button>
            </div>

            {permission === 'denied' ? (
              <p className="text-sm text-muted-foreground">
                浏览器已拒绝通知权限，只能先在浏览器设置里重新允许，再回到这里启用。
              </p>
            ) : null}

            {error ? (
              <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                {error}
              </p>
            ) : null}
            {status ? (
              <p role="status" className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">
                {status}
              </p>
            ) : null}

            <div className="space-y-2">
              <h3 className="flex items-center gap-2 text-sm font-medium">
                <Smartphone className="size-4" aria-hidden="true" />已登记的设备
              </h3>
              {subscriptions.length === 0 ? (
                <p className="text-sm text-muted-foreground">还没有设备登记推送订阅。</p>
              ) : (
                <ul className="space-y-2">
                  {subscriptions.map((item) => (
                    <li
                      key={item.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium">{item.endpoint_host}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {item.user_agent || '未知浏览器'} · 登记于 {formatTime(item.created_at)} · 最近送达{' '}
                          {formatTime(item.last_success_at)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {item.disabled ? <Badge variant="outline">已失效</Badge> : null}
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => void removeSubscription(item.id)}
                        >
                          删除
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
