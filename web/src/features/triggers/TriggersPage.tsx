import { useState } from 'react'
import { Page } from '@/components/layout/Page'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ObservationList } from './ObservationList'
import { RuleList } from './RuleList'
import { TriggerLog } from './TriggerLog'

type TriggerTab = 'rules' | 'observations' | 'events'

const TAB_OPTIONS = [
  { value: 'rules', label: '规则' },
  { value: 'observations', label: '待审阅' },
  { value: 'events', label: '事件记录' },
] as const

/**
 * 「更多 › 事件规则」（M81 §4.6）：单页三分页签。
 *
 * 规则页签管内置规则覆盖与用户规则；待审阅页签审阅 observe 档写下的观察记录；
 * 事件记录页签按时间列出所有进入系统的事件与去向。三个页签共用这一页的标题栏。
 */
export default function TriggersPage() {
  const [tab, setTab] = useState<TriggerTab>('rules')

  const toolbar = (
    <SegmentedControl
      ariaLabel="事件规则分页"
      value={tab}
      onValueChange={setTab}
      options={TAB_OPTIONS}
      fullWidth
    />
  )

  return (
    <Page title="事件规则" back="/more" toolbar={toolbar}>
      <div className="pb-4">
        {tab === 'rules' ? <RuleList /> : null}
        {tab === 'observations' ? <ObservationList onViewRules={() => setTab('rules')} /> : null}
        {tab === 'events' ? <TriggerLog /> : null}
      </div>
    </Page>
  )
}
