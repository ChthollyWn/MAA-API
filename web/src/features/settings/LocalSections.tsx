import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Eye, EyeOff, KeyRound, Monitor, Moon, ShieldCheck, Sun, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ListGroup, ListItem } from '@/components/ui/list'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { toast } from '@/components/ui/toaster'
import { healthQueryOptions } from '@/lib/health'
import { useAuth } from '@/stores/auth'
import { useUiStore, type ThemePreference } from '@/stores/ui'

export const THEME_OPTIONS = [
  { value: 'light', label: '浅色', icon: Sun },
  { value: 'dark', label: '深色', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
] as const satisfies readonly { value: ThemePreference; label: string; icon: typeof Sun }[]

export function themeLabel(theme: ThemePreference): string {
  return THEME_OPTIONS.find((option) => option.value === theme)?.label ?? theme
}

/** 外观：主题三选一，与「更多」页共用同一个设置。 */
export function AppearanceSection() {
  const theme = useUiStore((state) => state.theme)
  const setTheme = useUiStore((state) => state.setTheme)
  return (
    <div className="space-y-6 pb-4">
      <ListGroup title="主题" footer="跟随系统时，会随设备的浅色、深色模式自动切换。只影响这个浏览器。">
        <div className="p-3">
          <SegmentedControl ariaLabel="主题" value={theme} onValueChange={setTheme} options={THEME_OPTIONS} fullWidth />
        </div>
      </ListGroup>
    </div>
  )
}

/** 访问 token：替换或清除这个浏览器保存的 token，页面上从不显示已保存的值。 */
export function AccessSection() {
  const token = useAuth((state) => state.token)
  const setToken = useAuth((state) => state.setToken)
  const clearToken = useAuth((state) => state.clearToken)
  const health = useQuery(healthQueryOptions)
  const [value, setValue] = useState('')
  const [visible, setVisible] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirm, confirmElement] = useConfirm()
  const authEnabled = health.data?.auth_enabled

  const save = (event: FormEvent) => {
    event.preventDefault()
    const next = value.trim()
    if (!next) {
      setError('请输入新的访问 token。')
      return
    }
    setToken(next)
    setValue('')
    setVisible(false)
    setError(null)
    toast.success('已更新这台设备保存的访问 token')
  }

  const clear = async () => {
    const ok = await confirm({
      title: '清除这台设备保存的访问 token？',
      description: authEnabled
        ? '清除后需要重新输入 token 才能继续使用，页面会回到登录。'
        : '清除后，如果服务端要求 token，需要重新输入。',
      confirmText: '清除 token',
      tone: 'danger',
    })
    if (!ok) return
    clearToken()
    toast.success('已清除这台设备保存的访问 token')
  }

  return (
    <div className="space-y-6 pb-4">
      <ListGroup title="当前状态">
        <ListItem icon={KeyRound} iconTone="primary" title="这台设备的访问 token" trailing={token ? '已保存' : '未保存'} />
        {health.data ? (
          <ListItem
            icon={ShieldCheck}
            iconTone={authEnabled ? 'success' : 'neutral'}
            title="服务端 token"
            description={authEnabled ? '请求需要带上正确的 token' : '服务端没有设置 token，不需要鉴权'}
            trailing={authEnabled ? '已启用' : '未启用'}
          />
        ) : null}
      </ListGroup>

      <form noValidate onSubmit={save} className="space-y-4 rounded-xl border bg-card p-4 shadow-card">
        <Field
          label="新的访问 token"
          htmlFor="settings-access-token"
          description="只保存在这个浏览器里，页面不显示已保存的 token"
          descriptionId="settings-access-token-description"
          error={error}
          errorId="settings-access-token-error"
        >
          <div className="relative">
            <Input
              id="settings-access-token"
              type={visible ? 'text' : 'password'}
              value={value}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              className="pr-12"
              placeholder={token ? '输入新的 token 替换旧值' : '输入 token'}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? 'settings-access-token-error settings-access-token-description' : 'settings-access-token-description'}
              onChange={(event) => {
                setValue(event.target.value)
                setError(null)
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="absolute right-1 top-1/2 -translate-y-1/2 text-muted-foreground"
              aria-label={visible ? '隐藏输入的 token' : '显示输入的 token'}
              aria-pressed={visible}
              onClick={() => setVisible((current) => !current)}
            >
              {visible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
            </Button>
          </div>
        </Field>
        <Button type="submit" className="w-full sm:w-auto">保存 token</Button>
      </form>

      {token ? (
        <ListGroup footer="退出登录在「更多」里">
          <ListItem
            icon={Trash2}
            iconTone="danger"
            tone="danger"
            title="清除这台设备的 token"
            chevron={false}
            onClick={() => void clear()}
          />
        </ListGroup>
      ) : null}
      {confirmElement}
    </div>
  )
}
