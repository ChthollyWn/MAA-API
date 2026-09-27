import { Button } from '@/components/ui/button'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { TaskEditor } from '../editor'
import type { TaskDraft, TaskTypeSchema } from '../form'
import { typeDescription } from '../task-types'

export interface TaskEditorSheetProps {
  open: boolean
  onClose: () => void
  draft: TaskDraft | null
  taskType: TaskTypeSchema | undefined
  onChange: (params: Record<string, unknown>) => void
  errors?: Readonly<Record<string, string>>
  disabled?: boolean
  runtimeEditing?: boolean
}

/** 一个任务的参数编辑器：手机全屏、桌面右侧面板，标题为任务类型，右上角「完成」。 */
export function TaskEditorSheet({ open, onClose, draft, taskType, onChange, errors, disabled = false, runtimeEditing = false }: TaskEditorSheetProps) {
  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={taskType?.label ?? draft?.name ?? '任务参数'}
      description={taskType ? typeDescription(taskType) : undefined}
      mobile="full"
      desktop="panel"
      hideClose
      headerActions={<Button variant="soft" className="-mr-1 shrink-0 px-3.5" onClick={onClose}>完成</Button>}
    >
      {draft && taskType ? (
        <TaskEditor
          key={draft.id}
          taskType={taskType}
          value={draft.params}
          onChange={onChange}
          errors={errors}
          disabled={disabled}
          runtimeEditing={runtimeEditing}
          showDescription={false}
        />
      ) : null}
    </ResponsiveSheet>
  )
}
