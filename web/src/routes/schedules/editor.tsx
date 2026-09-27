import { useParams } from 'react-router'
import { ScheduleEditorScreen } from '@/features/schedules/ScheduleEditor'

/** 新建与编辑定时任务是「定时」的子页面；换一个定时任务时整页重挂，状态不串。 */
export default function ScheduleEditorPage() {
  const { scheduleId } = useParams()
  return <ScheduleEditorScreen key={scheduleId ?? 'new'} scheduleId={scheduleId ?? null} />
}
