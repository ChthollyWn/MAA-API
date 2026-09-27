import { useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function PwaUpdatePrompt() {
  const [waitingWorker, setWaitingWorker] = useState<ServiceWorker | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const refreshing = useRef(false)

  useEffect(() => {
    if (import.meta.env.DEV || !window.isSecureContext || !('serviceWorker' in navigator)) return

    let active = true
    let registration: ServiceWorkerRegistration | undefined
    const onControllerChange = () => {
      if (!refreshing.current) return
      refreshing.current = false
      setWaitingWorker(null)
      window.location.reload()
    }

    const onUpdateFound = () => {
      const installing = registration?.installing
      if (!installing) return
      installing.addEventListener('statechange', () => {
        if (!active || installing.state !== 'installed' || !navigator.serviceWorker.controller) return
        setWaitingWorker(registration?.waiting ?? installing)
      })
    }

    void navigator.serviceWorker.register('/sw.js')
      .then((result) => {
        if (!active) return
        registration = result
        result.addEventListener('updatefound', onUpdateFound)
        if (result.waiting && navigator.serviceWorker.controller) setWaitingWorker(result.waiting)
        return result.update()
      })
      .catch(() => {
        // App and API access continue when a browser or proxy blocks service worker registration.
      })

    navigator.serviceWorker.addEventListener('controllerchange', onControllerChange)
    return () => {
      active = false
      registration?.removeEventListener('updatefound', onUpdateFound)
      navigator.serviceWorker.removeEventListener('controllerchange', onControllerChange)
    }
  }, [])

  if (!waitingWorker || dismissed) return null

  const update = () => {
    refreshing.current = true
    waitingWorker.postMessage({ type: 'SKIP_WAITING' })
  }

  // 放在顶部：底部是 Tab 栏与页面主操作栏，提示条不能挡住「开始执行」「保存」。
  return (
    <div
      role="status"
      className="fixed inset-x-3 top-[calc(env(safe-area-inset-top,0px)+0.5rem)] z-50 mx-auto flex max-w-md items-center gap-2 rounded-xl border bg-surface-raised py-2 pl-4 pr-2 shadow-float lg:left-auto lg:right-6 lg:top-4 lg:mx-0"
    >
      <p className="min-w-0 flex-1 text-sm font-medium">MAA-API 有新版本可用</p>
      <Button size="sm" variant="ghost" onClick={() => setDismissed(true)}>稍后</Button>
      <Button size="sm" onClick={update}>
        <RefreshCw aria-hidden="true" />
        立即更新
      </Button>
    </div>
  )
}
