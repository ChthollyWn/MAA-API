import { useParams } from 'react-router'
import { Page } from '@/components/layout/Page'
import { PipelineDetail } from '@/features/tasks/history/PipelineDetail'

/** 流水线详情是历史的子页面：深链直接打开，不要求它在历史列表的当前页。 */
export default function PipelineDetailPage() {
  const { pipelineId = '' } = useParams()
  return (
    <Page title="流水线详情" back="/tasks/history">
      <PipelineDetail key={pipelineId} pipelineId={pipelineId} />
    </Page>
  )
}
