import { ResponsiveSheet } from '@/components/ui/sheet'
import { AttentionList } from './AttentionList'
import type { AttentionState } from './use-attention'

export interface AttentionSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  attention: AttentionState
  specTitles: ReadonlyMap<string, string>
}

/** 手机上的「待你处理」面板：由汇总行打开，处理完最后一张自动收起（M72 §4.6.4）。 */
export function AttentionSheet({ open, onOpenChange, attention, specTitles }: AttentionSheetProps) {
  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="待你处理"
      description={`${attention.items.length} 件`}
    >
      <AttentionList attention={attention} specTitles={specTitles} />
    </ResponsiveSheet>
  )
}
