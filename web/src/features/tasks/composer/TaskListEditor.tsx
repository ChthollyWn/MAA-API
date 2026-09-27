import { useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, Copy, ListChecks, Plus, Trash2 } from 'lucide-react'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import { useCopilotAssets } from '../editor'
import { summarizeTaskParams, type TaskDraft, type TaskTypeSchema } from '../form'
import { taskTypeIcon } from '../task-types'
import { createDraft, duplicateDraft, MAX_TASKS, moveDraft } from './drafts'
import { TaskEditorSheet } from './TaskEditorSheet'
import { TaskTypeSheet } from './TaskTypeSheet'

export interface TaskListEditorProps {
  taskTypes: readonly TaskTypeSchema[]
  drafts: readonly TaskDraft[]
  onChange: (drafts: TaskDraft[]) => void
  /** 任务 id → 字段名 → 错误消息：行上显示「N 处错误」，编辑器里显示在字段下方。 */
  errors?: Readonly<Record<string, Readonly<Record<string, string>>>>
  disabled?: boolean
  maxTasks?: number
  /** 正在编辑的任务 id；不传时由组件自己管理。 */
  editingId?: string | null
  onEditingIdChange?: (id: string | null) => void
  emptyDescription?: ReactNode
}

const NO_ERRORS: Readonly<Record<string, Readonly<Record<string, string>>>> = {}

interface TaskRowProps {
  draft: TaskDraft
  position: number
  count: number
  taskType: TaskTypeSchema | undefined
  summary: string
  errorCount: number
  disabled: boolean
  full: boolean
  onEdit: () => void
  onMove: (offset: -1 | 1) => void
  onDuplicate: () => void
  onRemove: () => void
}

function TaskRow({ draft, position, count, taskType, summary, errorCount, disabled, full, onEdit, onMove, onDuplicate, onRemove }: TaskRowProps) {
  const Icon = taskTypeIcon(draft.name)
  const label = taskType?.label ?? draft.name
  return (
    <li className="flex items-center pr-1.5">
      <button
        type="button"
        aria-haspopup="dialog"
        disabled={disabled || !taskType}
        onClick={onEdit}
        className="flex min-h-16 min-w-0 flex-1 items-center gap-3 py-3 pl-3 pr-2 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:pointer-events-none"
      >
        <span aria-hidden="true" className="w-5 shrink-0 text-right text-footnote tabular-nums text-muted-foreground">{position}</span>
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
          <Icon aria-hidden="true" className="size-[1.125rem]" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="sr-only">第 {position} 项：</span>
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[0.9375rem] font-medium">{label}</span>
            {errorCount > 0 ? <Badge variant="danger">{errorCount} 处错误</Badge> : null}
          </span>
          <span className={cn('mt-0.5 block truncate text-footnote', taskType ? 'text-muted-foreground' : 'text-danger')}>
            {taskType ? summary : '服务端未提供这种任务，无法编辑'}
          </span>
        </span>
      </button>
      <ActionMenu
        label={`第 ${position} 项的更多操作`}
        title={`第 ${position} 项 · ${label}`}
        disabled={disabled}
        actions={[
          { label: '上移', icon: ArrowUp, disabled: position === 1, onSelect: () => onMove(-1) },
          { label: '下移', icon: ArrowDown, disabled: position === count, onSelect: () => onMove(1) },
          { label: '复制', icon: Copy, disabled: full || !taskType, onSelect: onDuplicate },
          { label: '删除', icon: Trash2, tone: 'danger', onSelect: onRemove },
        ]}
      />
    </li>
  )
}

/**
 * 可编排的任务列表：每行显示类型、参数摘要与错误数，点行打开参数编辑器；行内菜单可上移、下移、复制、删除。
 * 编排器与定时任务编辑器共用；草稿结构见 `TaskDraft`（参数只含用户改过的字段）。
 */
export function TaskListEditor({
  taskTypes,
  drafts,
  onChange,
  errors = NO_ERRORS,
  disabled = false,
  maxTasks = MAX_TASKS,
  editingId: controlledEditingId,
  onEditingIdChange,
  emptyDescription,
}: TaskListEditorProps) {
  const [confirm, confirmElement] = useConfirm()
  const [typeSheetOpen, setTypeSheetOpen] = useState(false)
  const [localEditingId, setLocalEditingId] = useState<string | null>(null)
  const editingId = controlledEditingId === undefined ? localEditingId : controlledEditingId
  const setEditingId = (id: string | null) => {
    if (controlledEditingId === undefined) setLocalEditingId(id)
    onEditingIdChange?.(id)
  }
  // 关闭动画期间面板仍显示刚才的任务。
  const [shownId, setShownId] = useState<string | null>(editingId)
  if (editingId !== null && editingId !== shownId) setShownId(editingId)

  const copilots = useCopilotAssets(drafts.some((draft) => draft.name === 'Copilot'))
  const typeOf = (name: string) => taskTypes.find((taskType) => taskType.name === name)
  const full = drafts.length >= maxTasks
  const shownDraft = drafts.find((draft) => draft.id === shownId) ?? null
  const editingOpen = editingId !== null && drafts.some((draft) => draft.id === editingId)

  const summarize = (draft: TaskDraft, taskType: TaskTypeSchema) => summarizeTaskParams(taskType, draft.params, {
    formatField: (field, value) => {
      if (draft.name !== 'Copilot' || field !== 'asset_id') return undefined
      return copilots.data?.find((asset) => asset.id === value)?.name
    },
  })

  const add = (taskType: TaskTypeSchema) => {
    if (full) return
    const draft = createDraft(taskType.name)
    onChange([...drafts, draft])
    setTypeSheetOpen(false)
    setEditingId(draft.id)
  }

  const duplicate = (index: number) => {
    if (full) return
    const next = [...drafts]
    next.splice(index + 1, 0, duplicateDraft(drafts[index]))
    onChange(next)
  }

  const remove = async (index: number) => {
    const draft = drafts[index]
    const ok = await confirm({
      title: `删除第 ${index + 1} 项「${typeOf(draft.name)?.label ?? draft.name}」？`,
      description: '这个任务和它改过的参数会一起删除。',
      confirmText: '删除任务',
      tone: 'danger',
    })
    if (ok) onChange(drafts.filter((candidate) => candidate.id !== draft.id))
  }

  const updateParams = (id: string, params: Record<string, unknown>) => {
    onChange(drafts.map((draft) => (draft.id === id ? { ...draft, params } : draft)))
  }

  return (
    <>
      {drafts.length === 0 ? (
        <div className="rounded-xl border bg-card shadow-card">
          <EmptyState
            icon={ListChecks}
            title="还没有任务"
            description={emptyDescription ?? `可以添加多个任务，最多 ${maxTasks} 个。`}
            action={<Button disabled={disabled} onClick={() => setTypeSheetOpen(true)}><Plus aria-hidden="true" />添加任务</Button>}
          />
        </div>
      ) : (
        <div className="space-y-3">
          <ol aria-label="任务列表" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
            {drafts.map((draft, index) => {
              const taskType = typeOf(draft.name)
              return (
                <TaskRow
                  key={draft.id}
                  draft={draft}
                  position={index + 1}
                  count={drafts.length}
                  taskType={taskType}
                  summary={taskType ? summarize(draft, taskType) : ''}
                  errorCount={Object.keys(errors[draft.id] ?? {}).length}
                  disabled={disabled}
                  full={full}
                  onEdit={() => setEditingId(draft.id)}
                  onMove={(offset) => onChange(moveDraft(drafts, index, offset))}
                  onDuplicate={() => duplicate(index)}
                  onRemove={() => void remove(index)}
                />
              )
            })}
          </ol>
          <div className="flex items-center justify-between gap-3">
            <Button variant="outline" disabled={disabled || full} onClick={() => setTypeSheetOpen(true)}>
              <Plus aria-hidden="true" />
              添加任务
            </Button>
            <span className="text-footnote tabular-nums text-muted-foreground">{full ? `最多 ${maxTasks} 个任务` : `${drafts.length} / ${maxTasks}`}</span>
          </div>
        </div>
      )}

      <TaskTypeSheet open={typeSheetOpen} onOpenChange={setTypeSheetOpen} taskTypes={taskTypes} onSelect={add} />
      <TaskEditorSheet
        open={editingOpen}
        onClose={() => setEditingId(null)}
        draft={shownDraft}
        taskType={shownDraft ? typeOf(shownDraft.name) : undefined}
        errors={shownDraft ? errors[shownDraft.id] : undefined}
        disabled={disabled}
        onChange={(params) => {
          if (shownDraft) updateParams(shownDraft.id, params)
        }}
      />
      {confirmElement}
    </>
  )
}
