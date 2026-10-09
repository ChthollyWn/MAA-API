import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, Smartphone, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { PwaSecurityNotice } from '@/components/pwa/PwaSecurityNotice'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import {
  PUSH_AVAILABLE,
  currentPermission,
  disablePush,
  enablePush,
  hasActiveSubscription,
  type PushPermission,
} from '@/pwa/push'
import { describeUserAgent, errorText, formatTime } from './format'

const PUSH_SUBSCRIPTIONS_KEY = [...keys.all, 'notifications', 'push-subscriptions'] as const
const LOCAL_STATE_TIMEOUT_MS = 3000

interface PushSubscriptionItem {
  id: string
  endpoint_host: string
  user_agent?: string | null
  created_at: string
  last_success_at?: string | null
  disabled: boolean
}

const PERMISSION: Record<PushPermission, { label: string; tone: NonNullable<BadgeProps['variant']> }> = {
  unsupported: { label: '当前环境不支持', tone: 'neutral' },
  default: { label: '尚未授权', tone: 'neutral' },
  granted: { label: '已授权', tone: 'success' },
  denied: { label: '已被浏览器拒绝', tone: 'danger' },
}

async function fetchSubscriptions(): Promise<PushSubscriptionItem[]> {
  const { data, error, response } = await api.GET('/api/notifications/push-subscriptions', {
    params: { query: { include_disabled: true } },
  })
  if (error) throw toApiError(error, response)
  return (data as { items?: PushSubscriptionItem[] } | undefined)?.items ?? []
}

function deviceName(item: PushSubscriptionItem): string {
  return `${item.endpoint_host}（${describeUserAgent(item.user_agent)}）`
}

/**
 * Web 推送（docs/09 §13.1）。非安全上下文里 Service Worker 与 Push API 必然不可用，
 * 这时只给出说明、不渲染订阅控件：点不动的按钮只会让人以为坏了。
 */
export function PushNotifications() {
  return (
    <Section title="Web 推送" hint="页面没打开时，需要你确认的操作会以系统通知送到这台设备。">
      {PUSH_AVAILABLE ? <PushControls /> : <PushUnavailable />}
    </Section>
  )
}

function PushUnavailable() {
  return (
    <div className="space-y-3">
      <PwaSecurityNotice />
      <InlineAlert tone="info">
        推送需要通过 HTTPS 访问（例如 Tailscale Serve 提供的 <code className="font-mono text-[0.8125rem]">*.ts.net</code> 地址）；在 iPhone、iPad 上还需要先把应用添加到主屏幕，再从主屏幕打开。
      </InlineAlert>
    </div>
  )
}

function PushControls() {
  const queryClient = useQueryClient()
  const subscriptions = useQuery({ queryKey: PUSH_SUBSCRIPTIONS_KEY, queryFn: fetchSubscriptions })
  const [permission, setPermission] = useState<PushPermission>(() => currentPermission())
  const [subscribedHere, setSubscribedHere] = useState<boolean | null>(null)
  const [busy, setBusy] = useState<'enable' | 'disable' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, confirmElement] = useConfirm()

  const refreshLocal = useCallback(async () => {
    try {
      setSubscribedHere(await hasActiveSubscription())
    } catch {
      setSubscribedHere(false)
    }
  }, [])

  useEffect(() => {
    // Service Worker 迟迟没有就绪时 hasActiveSubscription 会一直挂起；先按未订阅显示，结果回来再更新。
    const fallback = window.setTimeout(() => setSubscribedHere((current) => current ?? false), LOCAL_STATE_TIMEOUT_MS)
    void refreshLocal().finally(() => window.clearTimeout(fallback))
    return () => window.clearTimeout(fallback)
  }, [refreshLocal])

  // 权限可能在浏览器设置里被改动，回到页面时以浏览器为准重读一次。
  useEffect(() => {
    const onFocus = () => setPermission(currentPermission())
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  const reload = () => Promise.all([queryClient.invalidateQueries({ queryKey: PUSH_SUBSCRIPTIONS_KEY }), refreshLocal()])

  // 权限申请必须由这个按钮的点击触发：iOS 会忽略非手势触发的请求。
  const enable = async () => {
    setBusy('enable')
    setError(null)
    try {
      await enablePush()
      setPermission(currentPermission())
      toast.success('已启用推送')
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '启用推送失败。')
      setPermission(currentPermission())
    } finally {
      setBusy(null)
    }
  }

  const disable = async () => {
    setBusy('disable')
    setError(null)
    try {
      await disablePush()
      toast.success('已注销这台设备的推送订阅')
      await reload()
    } catch {
      setError('注销推送订阅失败，请稍后重试。')
    } finally {
      setBusy(null)
    }
  }

  const remove = async (item: PushSubscriptionItem) => {
    const ok = await confirm({
      title: '删除这台设备的推送订阅？',
      description: `${deviceName(item)}将不再收到推送通知；需要时可以在那台设备上重新启用。`,
      confirmText: '删除订阅',
      tone: 'danger',
    })
    if (!ok) return
    setError(null)
    try {
      const { error: failure, response } = await api.DELETE('/api/notifications/push-subscriptions/{subscription_id}', {
        params: { path: { subscription_id: item.id } },
      })
      if (failure) throw toApiError(failure, response)
      toast.success('已删除推送订阅')
      await reload()
    } catch (caught) {
      setError(`删除订阅失败：${errorText(caught)}`)
    }
  }

  const status = PERMISSION[permission]
  const items = subscriptions.data ?? []

  return (
    <div className="space-y-4">
      <ListGroup>
        <ListItem icon={BellRing} iconTone="primary" title="通知权限" trailing={<Badge variant={status.tone}>{status.label}</Badge>} />
        <ListItem
          icon={Smartphone}
          iconTone="primary"
          title="这台设备"
          trailing={subscribedHere === null ? <Spinner /> : <Badge variant={subscribedHere ? 'success' : 'neutral'}>{subscribedHere ? '已订阅' : '未订阅'}</Badge>}
        />
      </ListGroup>

      {permission === 'denied' ? (
        <InlineAlert tone="warning" title="通知权限已被拒绝">
          请在浏览器的网站设置里把「通知」改为允许，再回到这里启用。
        </InlineAlert>
      ) : null}

      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="button" loading={busy === 'enable'} disabled={busy !== null || permission === 'denied'} onClick={() => void enable()}>
          {busy === 'enable' ? null : <BellRing aria-hidden="true" />}
          启用推送通知
        </Button>
        <Button type="button" variant="outline" loading={busy === 'disable'} disabled={busy !== null} onClick={() => void disable()}>
          注销订阅
        </Button>
      </div>

      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}

      <ListGroup title="已登记的设备" headingLevel="h3" footer="每个启用过推送的浏览器都会出现在这里，删除后那台设备不再收到推送">
        {subscriptions.isPending ? (
          <LoadingState label="正在读取设备…" className="min-h-16" />
        ) : subscriptions.isError ? (
          <ErrorState message={`设备列表读取失败：${errorText(subscriptions.error)}`} onRetry={() => void subscriptions.refetch()} retrying={subscriptions.isFetching} />
        ) : items.length === 0 ? (
          <p className="px-4 py-3.5 text-sm text-muted-foreground">还没有登记推送订阅的设备</p>
        ) : (
          items.map((item) => {
            const registered = formatTime(item.created_at)
            const delivered = formatTime(item.last_success_at)
            return (
              <div key={item.id} className="flex min-h-14 items-center gap-3 py-2.5 pl-4 pr-2">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-foreground">
                  <Smartphone aria-hidden="true" className="size-[1.125rem]" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.9375rem] font-medium leading-5">{item.endpoint_host}</p>
                  <p className="truncate text-footnote text-muted-foreground" title={item.user_agent ?? undefined}>
                    {describeUserAgent(item.user_agent)}{registered ? ` · 登记于 ${registered}` : ''}
                  </p>
                  <p className="text-footnote text-muted-foreground">{delivered ? `最近送达 ${delivered}` : '还没有送达记录'}</p>
                </div>
                {item.disabled ? <Badge variant="warning">已失效</Badge> : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground"
                  aria-label={`删除推送订阅：${deviceName(item)}${registered ? `，登记于 ${registered}` : ''}`}
                  onClick={() => void remove(item)}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </div>
            )
          })
        )}
      </ListGroup>
      {confirmElement}
    </div>
  )
}
