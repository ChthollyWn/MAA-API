import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState } from '@/components/ui/states'
import { ConfirmationsPanel, pendingConfirmationsQuery } from '@/features/confirmations/ConfirmationsPanel'
import { healthQueryOptions } from '@/lib/health'
import { useAuth } from '@/stores/auth'
import { useConfirmationUi } from '@/stores/confirmations-ui'

/**
 * 全局待确认面板，挂在外壳上：
 * - 读取待确认列表并把数量同步给标题栏与首页的入口；
 * - `?confirm=<id>`（任何页面）自动打开面板，关闭时清掉参数。
 */
export function ConfirmationDock() {
  const token = useAuth((state) => state.token)
  const health = useQuery(healthQueryOptions)
  // 未登录时不读，避免 401 触发全局登出流程。
  const canRead = Boolean(token) || health.data?.auth_enabled === false
  const pending = useQuery({ ...pendingConfirmationsQuery, enabled: canRead })
  const count = pending.data?.items.length ?? 0

  const open = useConfirmationUi((state) => state.open)
  const setOpen = useConfirmationUi((state) => state.setOpen)
  const setPendingCount = useConfirmationUi((state) => state.setPendingCount)
  const [searchParams, setSearchParams] = useSearchParams()
  const deepLinkId = searchParams.get('confirm')
  const hadContent = useRef(false)

  useEffect(() => {
    setPendingCount(count)
  }, [count, setPendingCount])

  useEffect(() => {
    if (deepLinkId) setOpen(true)
  }, [deepLinkId, setOpen])

  useEffect(() => {
    if (!open) {
      hadContent.current = false
      return
    }
    if (count > 0 || deepLinkId) {
      hadContent.current = true
      return
    }
    // 最后一条处理完、或深链提示已确认：自动收起。
    if (hadContent.current) setOpen(false)
  }, [count, deepLinkId, open, setOpen])

  const onOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next && deepLinkId) {
      const params = new URLSearchParams(searchParams)
      params.delete('confirm')
      setSearchParams(params, { replace: true })
    }
  }

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="需要人工确认"
      description="Agent 请求执行下面的操作，请逐条批准或拒绝。"
      size="md"
    >
      <ConfirmationsPanel showHeading={false} emptyFallback={<EmptyState icon={ShieldCheck} title="没有待确认的操作" />} />
    </ResponsiveSheet>
  )
}
