import { cn } from '@/lib/utils'

const SECURE_CONTEXT =
  typeof window !== 'undefined' &&
  window.isSecureContext &&
  'serviceWorker' in navigator

export function PwaSecurityNotice({ className }: { className?: string }) {
  if (SECURE_CONTEXT) return null
  return (
    <aside className={cn('rounded-lg border bg-muted/50 px-3 py-2 text-xs text-muted-foreground', className)} role="status">
      当前通过 HTTP 访问，Service Worker 离线缓存、Android 安装提示与推送通知不可用。配置 HTTPS 后自动启用。
    </aside>
  )
}
