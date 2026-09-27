import { ResponsiveSheet } from '@/components/ui/sheet'
import type { TaskTypeSchema } from '../form'
import { taskTypeIcon, typeDescription } from '../task-types'

export interface TaskTypeSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  taskTypes: readonly TaskTypeSchema[]
  onSelect: (taskType: TaskTypeSchema) => void
}

/** 「添加任务」：每种任务一行，图标、中文名与一句说明，点选即加入。 */
export function TaskTypeSheet({ open, onOpenChange, taskTypes, onSelect }: TaskTypeSheetProps) {
  return (
    <ResponsiveSheet open={open} onOpenChange={onOpenChange} title="添加任务" description="选一种任务，加入后直接设置参数。">
      <ul className="-mx-2 space-y-0.5">
        {taskTypes.map((taskType) => {
          const Icon = taskTypeIcon(taskType.name)
          return (
            <li key={taskType.name}>
              <button
                type="button"
                onClick={() => onSelect(taskType)}
                className="flex min-h-14 w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
                  <Icon aria-hidden="true" className="size-[1.125rem]" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[0.9375rem] font-medium">{taskType.label}</span>
                  <span className="mt-0.5 block text-footnote text-muted-foreground">{typeDescription(taskType)}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </ResponsiveSheet>
  )
}
