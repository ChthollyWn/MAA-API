import type { CSSProperties } from 'react'
import { Toaster as SonnerToaster, toast } from 'sonner'
import { resolveTheme, useUiStore } from '@/stores/ui'

export { toast }

const toasterStyle = {
  '--normal-bg': 'var(--surface-raised)',
  '--normal-border': 'var(--border)',
  '--normal-text': 'var(--foreground)',
  '--border-radius': '0.75rem',
  '--width': '22rem',
  fontFamily: 'var(--app-font-sans)',
} as CSSProperties

/** 操作结果提示，出现在顶部安全区下方，不和底部操作栏抢位置。 */
export function Toaster() {
  const theme = useUiStore((state) => state.theme)
  return (
    <SonnerToaster
      // 显式传入主题：sonner 在 system 模式下会直接调用 matchMedia。
      theme={resolveTheme(theme)}
      position="top-center"
      visibleToasts={3}
      duration={2600}
      offset={{ top: 'calc(env(safe-area-inset-top, 0px) + 12px)' }}
      mobileOffset={{ top: 'calc(env(safe-area-inset-top, 0px) + 8px)', left: '12px', right: '12px' }}
      style={toasterStyle}
      toastOptions={{ style: { boxShadow: 'var(--float-shadow)', fontSize: '0.875rem' } }}
    />
  )
}
