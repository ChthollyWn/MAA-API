import type { ReactNode } from 'react'
import { Disclosure } from '@/components/ui/disclosure'
import type { ToolView } from '@/features/agent/transcript'
import { ToolCallBar } from '@/features/agent/ToolCallBar'

export interface DelegateToolBarProps {
  tool: ToolView
  /** 子代理的中文名；没有定义时组件显示「子代理」。 */
  agentTitle: string | null
  messageCount: number
  children: ReactNode
}

/**
 * 委派工具条：`ToolCallBar` 之下一个默认折叠的「子代理过程」区块，
 * 里面是该子运行的条目（历史用 `subRuns`，实时用子 `LiveRun` 的段）。
 */
export function DelegateToolBar({ tool, agentTitle, messageCount, children }: DelegateToolBarProps) {
  return (
    <div className="space-y-2">
      <ToolCallBar tool={tool} />
      <Disclosure
        variant="plain"
        title={`子代理过程（${agentTitle ?? '子代理'}，${messageCount} 条消息）`}
      >
        <div className="space-y-3 border-l-2 border-border pl-3">{children}</div>
      </Disclosure>
    </div>
  )
}
