import { useLocation } from 'react-router'
import { Page } from '@/components/layout/Page'
import { TaskComposer } from '@/features/tasks/composer/TaskComposer'
import { HistoryView } from '@/features/tasks/history/HistoryView'
import { QueueView } from '@/features/tasks/queue/QueueView'
import { TaskAreaNav, taskAreaView } from '@/features/tasks/TaskAreaNav'

/** 任务区：新建、队列、历史共用标题栏与分段导航，视图由路径决定。 */
export default function TasksPage() {
  const location = useLocation()
  const view = taskAreaView(location.pathname)
  return (
    <Page title="任务" toolbar={<TaskAreaNav value={view} />}>
      {view === 'queue' ? <QueueView /> : view === 'history' ? <HistoryView /> : <TaskComposer />}
    </Page>
  )
}
