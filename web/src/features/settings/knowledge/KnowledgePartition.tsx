import { useSearchParams } from 'react-router'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { KnowledgeSection } from './KnowledgeSection'
import { RetrievalSearch } from './RetrievalSearch'
import { RetrievalSources } from './RetrievalSources'

const VIEW_OPTIONS = [
  { value: 'entries', label: '知识条目' },
  { value: 'sources', label: '检索来源' },
] as const

type KnowledgeView = (typeof VIEW_OPTIONS)[number]['value']

/** 「设置 › Agent 知识库」的两个视图；`entries` 是默认值，只有 `sources` 进 URL。 */
export function KnowledgePartition() {
  const [searchParams, setSearchParams] = useSearchParams()
  const view: KnowledgeView = searchParams.get('tab') === 'sources' ? 'sources' : 'entries'

  const changeView = (next: KnowledgeView) => {
    const params = new URLSearchParams(searchParams)
    if (next === 'entries') params.delete('tab')
    else params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="space-y-4">
      <SegmentedControl
        ariaLabel="知识库视图"
        value={view}
        onValueChange={changeView}
        options={VIEW_OPTIONS}
        fullWidth
      />
      {view === 'entries' ? (
        <KnowledgeSection />
      ) : (
        <div className="space-y-6">
          <RetrievalSources />
          <RetrievalSearch />
        </div>
      )}
    </div>
  )
}
