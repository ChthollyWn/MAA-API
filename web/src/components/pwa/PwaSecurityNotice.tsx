import { ShieldOff } from 'lucide-react'
import { cn } from '@/lib/utils'

const SECURE_CONTEXT =
  typeof window !== 'undefined' &&
  window.isSecureContext &&
  'serviceWorker' in navigator

export function PwaSecurityNotice({ className }: { className?: string }) {
  if (SECURE_CONTEXT) return null
  return (
    <aside className={cn('flex gap-2.5 rounded-xl bg-surface-2 px-3.5 py-3 text-footnote text-muted-foreground', className)} role="status">
      <ShieldOff aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>当前通过 HTTP 访问，Service Worker 离线缓存、Android 安装提示与推送通知不可用。配置 HTTPS 后自动启用。</span>
    </aside>
  )
}
