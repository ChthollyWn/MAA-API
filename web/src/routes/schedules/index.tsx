import { Navigate, useLocation } from 'react-router'
import { Page } from '@/components/layout/Page'
import { NEW_SCHEDULE_PATH } from '@/features/schedules/api'
import { hasSchedulePrefill } from '@/features/schedules/payload'
import { ScheduleList } from '@/features/schedules/ScheduleList'
import { TaskAreaNav } from '@/features/tasks/TaskAreaNav'

/** 任务 › 定时。带着预填 state 进来（例如旧地址 /more/schedules 的重定向）时转给新建页。 */
export default function SchedulesPage() {
  const location = useLocation()
  if (hasSchedulePrefill(location.state)) return <Navigate to={NEW_SCHEDULE_PATH} replace state={location.state} />
  return (
    <Page title="任务" toolbar={<TaskAreaNav value="schedules" />}>
      <ScheduleList />
    </Page>
  )
}
