import type { ReactNode } from 'react'
import { Disclosure } from '@/components/ui/disclosure'

export interface SupersededFoldProps {
  /** 这一组连续被取代的消息条数，标题里的 n。 */
  count: number
  children: ReactNode
}

/**
 * 连续的被取代消息（M80 §4.6）：默认收起的折叠条，展开后整组降低不透明度。
 *
 * 组内不再渲染动作菜单——这些消息已经不是合法目标。
 */
export function SupersededFold({ count, children }: SupersededFoldProps) {
  return (
    <Disclosure title={`已回溯的 ${count} 条消息`} variant="plain">
      <div className="space-y-3 opacity-60">{children}</div>
    </Disclosure>
  )
}
