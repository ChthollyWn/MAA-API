import { useParams } from 'react-router'
import { KnowledgeEditorScreen } from '@/features/settings/knowledge/KnowledgeEditor'

/** 新建与编辑知识条目是「设置 › Agent 知识库」的子页面；换一条时整页重挂，状态不串。 */
export default function KnowledgeEditorPage() {
  const { entryId } = useParams()
  return <KnowledgeEditorScreen key={entryId ?? 'new'} entryId={entryId ?? null} />
}
