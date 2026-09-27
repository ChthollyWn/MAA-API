import { useCallback, useEffect, useRef, type ReactNode } from 'react'
import { useBlocker, type BlockerFunction } from 'react-router'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'

/**
 * 有未保存改动时拦截应用内导航（弹确认框）与关闭、刷新页面（浏览器自带提示）。
 * 只能用在数据路由（createBrowserRouter / createMemoryRouter）里。返回的确认框要渲染出来。
 */
export function useUnsavedChangesGuard(dirty: boolean, description: string): ReactNode {
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
    [dirty],
  )
  const blocker = useBlocker(shouldBlock)
  // 确认离开后对话框关闭时不能再 reset：后退（POP）导航在 proceed 之后才异步重放，reset 会让它再被拦一次。
  const leavingRef = useRef(false)

  useEffect(() => {
    if (blocker.state === 'blocked') leavingRef.current = false
  }, [blocker.state])

  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])

  return (
    <ConfirmDialog
      open={blocker.state === 'blocked'}
      onOpenChange={(open) => {
        if (!open && !leavingRef.current) blocker.reset?.()
      }}
      title="放弃未保存的更改？"
      description={description}
      confirmText="放弃更改"
      cancelText="继续编辑"
      tone="danger"
      onConfirm={() => {
        leavingRef.current = true
        blocker.proceed?.()
      }}
    />
  )
}
