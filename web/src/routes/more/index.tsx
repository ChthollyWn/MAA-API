import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { LogOut, Monitor, Moon, Sun } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { secondaryNav } from '@/components/layout/nav'
import { Page } from '@/components/layout/Page'
import { PwaSecurityNotice } from '@/components/pwa/PwaSecurityNotice'
import { StatusDot } from '@/components/ui/badge'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { healthQueryOptions } from '@/lib/health'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'
import { useAuth } from '@/stores/auth'
import { useUiStore, type ThemePreference } from '@/stores/ui'

const themeOptions = [
  { value: 'light', label: '浅色', icon: Sun },
  { value: 'dark', label: '深色', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
] as const satisfies readonly { value: ThemePreference; label: string; icon: typeof Sun }[]

const realtimeText = {
  CONNECTED: { label: '已连接', tone: 'success' },
  CONNECTING: { label: '正在连接', tone: 'warning' },
  RECONNECTING: { label: '重连中', tone: 'warning' },
  OFFLINE: { label: '已断开', tone: 'danger' },
} as const

const operations = secondaryNav.filter((item) => item.to !== '/more/settings')
const system = secondaryNav.filter((item) => item.to === '/more/settings')

export default function MorePage() {
  const navigate = useNavigate()
  const token = useAuth((state) => state.token)
  const clearToken = useAuth((state) => state.clearToken)
  const theme = useUiStore((state) => state.theme)
  const setTheme = useUiStore((state) => state.setTheme)
  const realtime = useRealtimeStatus()
  const health = useQuery(healthQueryOptions)
  const [confirm, confirmElement] = useConfirm()
  const [logoutError, setLogoutError] = useState<string | null>(null)
  const [loggingOut, setLoggingOut] = useState(false)

  const logout = async () => {
    if (!token || loggingOut) return
    const ok = await confirm({
      title: '退出登录？',
      description: '需要重新输入访问 token 才能继续使用。',
      confirmText: '退出登录',
      tone: 'danger',
    })
    if (!ok) return
    setLogoutError(null)
    setLoggingOut(true)
    const { error, response } = await api.DELETE('/api/system/auth/cookie')
    setLoggingOut(false)
    if (error) {
      const apiError = toApiError(error, response)
      setLogoutError(apiError.status === 403 ? '没有权限退出登录（403）' : apiError.message)
      return
    }
    clearToken()
    navigate('/login', { replace: true })
  }

  const realtimeState = realtimeText[realtime.status]

  return (
    <Page title="更多" width="narrow">
      <div className="space-y-6 pb-4">
        <ListGroup title="运维">
          {operations.map(({ to, label, icon, description }) => (
            <ListItem key={to} to={to} icon={icon} iconTone="primary" title={label} description={description} />
          ))}
        </ListGroup>

        <ListGroup title="系统">
          {system.map(({ to, label, icon, description }) => (
            <ListItem key={to} to={to} icon={icon} iconTone="primary" title={label} description={description} />
          ))}
        </ListGroup>

        <ListGroup title="外观">
          <div className="p-3">
            <SegmentedControl ariaLabel="外观" value={theme} onValueChange={setTheme} options={themeOptions} fullWidth />
          </div>
        </ListGroup>

        {token ? (
          <ListGroup title="账户">
            <ListItem icon={LogOut} iconTone="danger" tone="danger" title={loggingOut ? '正在退出…' : '退出登录'} chevron={false} onClick={() => void logout()} disabled={loggingOut} />
          </ListGroup>
        ) : null}
        {logoutError ? <InlineAlert tone="danger">{logoutError}</InlineAlert> : null}

        <ListGroup title="关于">
          <ListItem title="服务版本" trailing={<span className="font-mono tabular-nums">{health.data?.version ?? '—'}</span>} />
          <ListItem
            title="实时连接"
            trailing={<span className="inline-flex items-center gap-2"><StatusDot tone={realtimeState.tone} />{realtimeState.label}</span>}
          />
        </ListGroup>
        <PwaSecurityNotice />
      </div>
      {confirmElement}
    </Page>
  )
}
