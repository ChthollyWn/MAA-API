import { useNavigate } from 'react-router'
import { SegmentedControl } from '@/components/ui/segmented-control'

export type TaskAreaView = 'create' | 'queue' | 'history' | 'schedules'

export const TASK_AREA_PATHS: Record<TaskAreaView, string> = {
  create: '/tasks',
  queue: '/tasks/queue',
  history: '/tasks/history',
  schedules: '/tasks/schedules',
}

const options = [
  { value: 'create', label: '新建' },
  { value: 'queue', label: '队列' },
  { value: 'history', label: '历史' },
  { value: 'schedules', label: '定时' },
] as const satisfies readonly { value: TaskAreaView; label: string }[]

export function taskAreaView(pathname: string): TaskAreaView {
  const path = pathname.replace(/\/+$/, '')
  if (path === TASK_AREA_PATHS.queue) return 'queue'
  if (path === TASK_AREA_PATHS.history) return 'history'
  if (path.startsWith(TASK_AREA_PATHS.schedules)) return 'schedules'
  return 'create'
}

/** 任务区四个子视图的分段导航；切换写入浏览器历史，可前进后退。 */
export function TaskAreaNav({ value }: { value: TaskAreaView }) {
  const navigate = useNavigate()
  return (
    <SegmentedControl
      ariaLabel="任务视图"
      value={value}
      onValueChange={(next) => navigate(TASK_AREA_PATHS[next])}
      options={options}
      fullWidth
      className="sm:max-w-md"
    />
  )
}
