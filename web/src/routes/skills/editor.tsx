import { useParams } from 'react-router'
import { SkillEditorScreen } from '@/features/skills/SkillEditorPage'

/** 新建与编辑技能是「更多 › 技能」的子页面；换一条时整页重挂，状态不串。 */
export default function SkillEditorPage() {
  const { skillId } = useParams()
  return <SkillEditorScreen key={skillId ?? 'new'} skillId={skillId ?? null} />
}
