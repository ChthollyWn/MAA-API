import { useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

export function PwaUpdatePrompt() {
  const [waitingWorker, setWaitingWorker] = useState<ServiceWorker | null>(null)
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

  if (!waitingWorker) return null

  const update = () => {
    refreshing.current = true
    waitingWorker.postMessage({ type: 'SKIP_WAITING' })
  }

  return (
    <Card className="fixed inset-x-3 bottom-[calc(4.5rem+env(safe-area-inset-bottom,0px))] z-50 mx-auto max-w-lg border-primary/40 shadow-lg">
      <CardContent className="flex items-center justify-between gap-3 p-3">
        <p className="text-sm font-medium">MAA-API 有新版本可用</p>
        <Button size="sm" onClick={update}>
          <RefreshCw aria-hidden="true" />
          立即更新
        </Button>
      </CardContent>
    </Card>
  )
}
