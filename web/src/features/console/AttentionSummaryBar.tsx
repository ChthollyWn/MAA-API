import { Button } from '@/components/ui/button'

export interface AttentionSummaryBarProps {
  count: number
  onOpen: () => void
}

/** 手机工具条上的汇总行：有需要处理的事时给出条数和入口（M72 §4.6.4）。 */
export function AttentionSummaryBar({ count, onOpen }: AttentionSummaryBarProps) {
  return (
    <Button type="button" variant="secondary" size="sm" className="shrink-0 rounded-full" onClick={onOpen}>
      {count} 件事等你处理
    </Button>
  )
}
