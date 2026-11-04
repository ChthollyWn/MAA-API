import { Section } from '@/components/ui/section'
import { AttentionList } from './AttentionList'
import type { AttentionState } from './use-attention'

export interface AttentionPanelProps {
  attention: AttentionState
  specTitles: ReadonlyMap<string, string>
}

/** 桌面右侧的「待你处理」区块（M72 §4.6.4）。 */
export function AttentionPanel({ attention, specTitles }: AttentionPanelProps) {
  return (
    <Section title="待你处理" description={`${attention.items.length} 件`}>
      <AttentionList attention={attention} specTitles={specTitles} />
    </Section>
  )
}
