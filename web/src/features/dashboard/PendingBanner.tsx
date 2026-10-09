import { ChevronRight, ShieldAlert } from 'lucide-react'
import { useConfirmationUi } from '@/stores/confirmations-ui'

/** 首页顶部的待确认提示；数量来自外壳里的 ConfirmationDock。 */
export function PendingBanner() {
  const pendingCount = useConfirmationUi((state) => state.pendingCount)
  const setOpen = useConfirmationUi((state) => state.setOpen)
  if (pendingCount <= 0) return null
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className="flex w-full items-center gap-3 rounded-xl bg-warning-soft px-4 py-3.5 text-left transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ShieldAlert aria-hidden="true" className="size-5 shrink-0 text-warning" />
      <span className="min-w-0 flex-1">
        <span className="block text-[0.9375rem] font-semibold">{pendingCount} 项操作等待你确认</span>
        <span className="block text-footnote text-muted-foreground">Agent 请求的资源消耗和破坏性操作需要你确认</span>
      </span>
      <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
    </button>
  )
}
