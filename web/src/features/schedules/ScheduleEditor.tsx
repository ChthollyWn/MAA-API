import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarX, Play, Trash2 } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { InlineAlert, type AlertTone } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { StickyActionBar } from '@/components/ui/sticky-action-bar'
import { SwitchRow } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { draftsFromTemplate, materializeDrafts, templateFromDrafts, TaskListEditor, useTaskTypes, type TaskDraft } from '@/features/tasks'
import { taskErrorSummaryMessage } from '@/features/tasks/pipeline-format'
import { useUnsavedChangesGuard } from '@/lib/use-unsaved-changes'
import { deleteSchedule, refreshAfterRun, runSchedule, saveSchedule, SCHEDULES_PATH, updateScheduleCache } from './api'
import { describeSaveFailure, pruneTaskErrors, type SaveFailureContext, type TaskErrors } from './errors'
import { deleteConfirmation, runConfirmation } from './format'
import {
  buildSchedulePayload,
  emptyScheduleForm,
  hasSchedulePrefill,
  readSchedulePrefill,
  ScheduleTemplateError,
  scheduleFormFromView,
  scheduleSnapshot,
  validateScheduleForm,
  type ScheduleField,
  type ScheduleFieldErrors,
  type ScheduleForm,
  type SchedulePrefill,
  type ScheduleView,
  type ScheduleWrite,
} from './payload'
import { schedulesQuery } from './queries'
import { describeWeekdays } from './schedule-time'
import { ScheduleRuns } from './ScheduleRuns'
import { ScheduleTimeFields } from './ScheduleTimeFields'

const card = 'rounded-xl border bg-card shadow-card'
/** 视口底部被 Tab 栏与操作栏盖住，反馈滚进视野时要停在它们上方。 */
const FEEDBACK_SCROLL_MARGIN = 'scroll-mt-28 scroll-mb-[calc(var(--nav-offset)+var(--keyboard-inset)+var(--nav-safe)+5.5rem)]'
const TASKS_SECTION_ID = 'schedule-tasks'
const FIELD_ORDER: readonly ScheduleField[] = ['name', 'weekdays', 'time', 'cron', 'timezone', 'tasks']
/** 改动某个表单项时一并清掉的错误。 */
const FIELDS_OF: Readonly<Record<keyof ScheduleForm, readonly ScheduleField[]>> = {
  name: ['name'],
  mode: ['weekdays', 'time', 'cron'],
  weekdays: ['weekdays'],
  time: ['time'],
  cron: ['cron'],
  timezone: ['timezone'],
  enabled: [],
}

interface Notice {
  tone: AlertTone
  title?: string
  message: string
  /** 提示上的按钮：查看队列，或定位到出错的表单项。 */
  action?: 'queue' | ScheduleField
}

function withoutFields(errors: ScheduleFieldErrors, fields: readonly ScheduleField[]): ScheduleFieldErrors {
  if (!fields.some((field) => errors[field])) return errors
  return Object.fromEntries(Object.entries(errors).filter(([field]) => !fields.includes(field as ScheduleField)))
}

/** 居中而不是贴边：顶部有吸顶标题栏，底部有 Tab 栏与操作栏。 */
function focusField(field: ScheduleField) {
  const target = field === 'weekdays'
    ? document.querySelector<HTMLElement>('#schedule-weekdays button')
    : field === 'tasks'
      ? document.querySelector<HTMLElement>(`#${TASKS_SECTION_ID} button`)
      : document.getElementById(`schedule-${field}`)
  if (!target) return
  target.focus({ preventScroll: true })
  target.scrollIntoView?.({ block: 'center' })
}

function taskErrorSummary(drafts: readonly TaskDraft[], errors: TaskErrors) {
  const failing = drafts
    .map((draft, index) => ({ id: draft.id, position: index + 1, count: Object.keys(errors[draft.id] ?? {}).length }))
    .filter((entry) => entry.count > 0)
  if (failing.length === 0) return null
  const total = failing.reduce((sum, entry) => sum + entry.count, 0)
  const message = `${taskErrorSummaryMessage(failing.map((entry) => entry.position), total)}。`
  return { message, firstId: failing[0].id }
}

function whenSummary(form: ScheduleForm): string {
  if (form.mode === 'cron') return form.cron.trim() ? `按 cron ${form.cron.trim()} 运行` : '填写 cron 表达式'
  return form.weekdays.length > 0 && form.time ? `${describeWeekdays(form.weekdays)} ${form.time} 运行` : '选择星期和时间'
}

function prefillMessage(prefill: SchedulePrefill): string {
  return prefill.source === 'composer'
    ? `已载入 ${prefill.template.length} 个任务。设好星期和时间就能保存。`
    : '已载入 API 调试台收藏的流水线模板。设好星期和时间就能保存。'
}

interface ScheduleEditorProps {
  /** 编辑已有定时任务；新建时为 null。 */
  schedule: ScheduleView | null
  prefill: SchedulePrefill | null
  /** 保存或删除成功后离开编辑器（不经未保存拦截）。 */
  onLeave: (path: string) => void
}

function ScheduleEditor({ schedule, prefill, onLeave }: ScheduleEditorProps) {
  const queryClient = useQueryClient()
  const types = useTaskTypes()
  const taskTypes = types.data ?? []
  const [initial] = useState(() => (schedule
    ? { form: scheduleFormFromView(schedule), drafts: draftsFromTemplate(schedule.template, taskTypes) }
    : { form: emptyScheduleForm(prefill?.name), drafts: draftsFromTemplate(prefill?.template ?? [], taskTypes) }))
  const [baseline, setBaseline] = useState(() => scheduleSnapshot(initial.form, templateFromDrafts(initial.drafts)))
  const [form, setForm] = useState(initial.form)
  const [drafts, setDrafts] = useState(initial.drafts)
  const [fieldErrors, setFieldErrors] = useState<ScheduleFieldErrors>({})
  const [taskErrors, setTaskErrors] = useState<TaskErrors>({})
  const [notice, setNotice] = useState<Notice | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [focusRequest, setFocusRequest] = useState<{ field: ScheduleField } | null>(null)
  const [feedbackVersion, setFeedbackVersion] = useState(0)
  const feedbackRef = useRef<HTMLDivElement>(null)
  const [confirm, confirmElement] = useConfirm()

  const template = templateFromDrafts(drafts)
  const dirty = scheduleSnapshot(form, template) !== baseline
  const guard = useUnsavedChangesGuard(
    dirty,
    schedule ? `「${schedule.name}」有未保存的更改，离开后这些更改会丢失。` : '新的定时任务还没有创建，离开后填写的内容会丢失。',
  )

  // 反馈出现在列表下方，保存按钮在屏幕底部，把它滚进视野。
  useEffect(() => {
    if (feedbackVersion > 0) feedbackRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [feedbackVersion])
  useEffect(() => {
    if (focusRequest) focusField(focusRequest.field)
  }, [focusRequest])

  // 任务类型目录可能晚于首次渲染才到达（例如从 API 调试台收藏新建）；到了再补一次物化，
  // 基线一起更新，否则物化本身会被当成「有未保存的更改」。
  const materialized = useRef(taskTypes.length > 0)
  useEffect(() => {
    if (materialized.current || taskTypes.length === 0) return
    materialized.current = true
    const next = materializeDrafts(drafts, taskTypes)
    if (next.every((draft, index) => draft === drafts[index])) return
    setDrafts(next)
    setBaseline(scheduleSnapshot(initial.form, templateFromDrafts(next)))
  }, [taskTypes, drafts, initial.form])

  const showFeedback = (next: Notice | null) => {
    setNotice(next)
    setFeedbackVersion((version) => version + 1)
  }
  const locate = (field: ScheduleField) => {
    if (field === 'timezone') setAdvancedOpen(true)
    setFocusRequest({ field })
  }

  const save = useMutation({
    mutationFn: ({ payload }: { payload: ScheduleWrite; context: SaveFailureContext }) => saveSchedule(schedule?.id ?? null, payload),
    onSuccess: (saved) => {
      updateScheduleCache(queryClient, { saved })
      toast.success(schedule ? '定时任务已保存' : '已新建定时任务')
      onLeave(SCHEDULES_PATH)
    },
    onError: (error, { context }) => {
      const failure = describeSaveFailure(error, context)
      setFieldErrors(failure.fields)
      setTaskErrors(failure.tasks)
      if (failure.fields.timezone) setAdvancedOpen(true)
      const field = FIELD_ORDER.find((candidate) => failure.fields[candidate])
      showFeedback(failure.notice ? { tone: 'danger', ...failure.notice, action: field } : null)
    },
  })
  const run = useMutation({
    mutationFn: (target: ScheduleView) => runSchedule(target.id),
    onMutate: () => setNotice(null),
    onSuccess: async (_data, target) => {
      toast.success(`「${target.name}」已加入队列`)
      showFeedback({ tone: 'success', message: `「${target.name}」已加入队列。`, action: 'queue' })
      await refreshAfterRun(queryClient)
    },
    onError: (error) => showFeedback({ tone: 'danger', message: `运行失败：${toApiError(error).message}` }),
  })
  const remove = useMutation({
    mutationFn: (target: ScheduleView) => deleteSchedule(target.id),
    onMutate: () => setNotice(null),
    onSuccess: (_data, target) => {
      toast.success(`已删除「${target.name}」`)
      updateScheduleCache(queryClient, { removedId: target.id })
      onLeave(SCHEDULES_PATH)
    },
    onError: (error) => showFeedback({ tone: 'danger', message: `删除失败：${toApiError(error).message}` }),
  })
  const busy = save.isPending || run.isPending || remove.isPending

  const changeForm = (patch: Partial<ScheduleForm>) => {
    setForm((current) => ({ ...current, ...patch }))
    const cleared = (Object.keys(patch) as (keyof ScheduleForm)[]).flatMap((key) => FIELDS_OF[key])
    setFieldErrors((current) => withoutFields(current, cleared))
    setNotice(null)
  }
  const changeDrafts = (next: TaskDraft[]) => {
    setTaskErrors((current) => pruneTaskErrors(current, drafts, next))
    if (next.length > 0) setFieldErrors((current) => withoutFields(current, ['tasks']))
    setDrafts(next)
    setNotice(null)
  }

  const submit = () => {
    if (busy) return
    const errors = validateScheduleForm(form, drafts.length)
    const firstInvalid = FIELD_ORDER.find((field) => errors[field])
    if (firstInvalid) {
      setFieldErrors(errors)
      setNotice(null)
      locate(firstInvalid)
      return
    }
    let payload: ScheduleWrite
    try {
      payload = buildSchedulePayload(form, template)
    } catch (error) {
      if (!(error instanceof ScheduleTemplateError)) throw error
      const draft = drafts[error.index]
      if (draft && error.field) {
        setTaskErrors({ [draft.id]: { [error.field]: error.message } })
        showFeedback(null)
      } else {
        showFeedback({ tone: 'danger', title: '无法保存', message: error.message })
      }
      return
    }
    setFieldErrors({})
    setTaskErrors({})
    setNotice(null)
    save.mutate({ payload, context: { name: form.name, mode: form.mode, timezone: form.timezone, drafts, taskTypes } })
  }

  const runNow = async (target: ScheduleView) => {
    if (await confirm(runConfirmation(target, dirty))) run.mutate(target)
  }
  const removeSchedule = async (target: ScheduleView) => {
    if (await confirm(deleteConfirmation(target, dirty))) remove.mutate(target)
  }

  const taskSummary = taskErrorSummary(drafts, taskErrors)
  let noticeAction: ReactNode = null
  if (notice?.action === 'queue') {
    noticeAction = <Button asChild variant="outline" size="sm"><Link to="/tasks/queue">查看队列</Link></Button>
  } else if (notice?.action) {
    const field = notice.action
    noticeAction = <Button variant="outline" size="sm" onClick={() => locate(field)}>修改</Button>
  }

  let tasks: ReactNode
  if (types.isPending) {
    tasks = <div className={card}><LoadingState label="正在读取任务类型…" /></div>
  } else if (types.error) {
    tasks = (
      <div className={card}>
        <ErrorState message={`无法读取任务类型：${toApiError(types.error).message}`} onRetry={() => void types.refetch()} retrying={types.isFetching} />
      </div>
    )
  } else {
    tasks = (
      <TaskListEditor
        taskTypes={taskTypes}
        drafts={drafts}
        onChange={changeDrafts}
        errors={taskErrors}
        disabled={busy}
        editingId={editingId}
        onEditingIdChange={setEditingId}
        emptyDescription="到点后按顺序执行这些任务，最多 32 个。"
      />
    )
  }

  return (
    <Page
      title={schedule ? '编辑定时任务' : '新建定时任务'}
      back={SCHEDULES_PATH}
      actions={schedule ? (
        <ActionMenu
          label="定时任务操作"
          title={schedule.name}
          disabled={busy}
          actions={[
            { label: '立即运行', description: '按已保存的设置马上执行一次', icon: Play, onSelect: () => void runNow(schedule) },
            { label: '删除', icon: Trash2, tone: 'danger', onSelect: () => void removeSchedule(schedule) },
          ]}
        />
      ) : undefined}
    >
      <div className="space-y-6 pb-2">
        {prefill ? <InlineAlert tone="info">{prefillMessage(prefill)}</InlineAlert> : null}

        <Field label="名称" htmlFor="schedule-name" error={fieldErrors.name} errorId="schedule-name-error">
          <Input
            id="schedule-name"
            maxLength={64}
            placeholder="例如：每日日常、周末剿灭"
            autoComplete="off"
            value={form.name}
            disabled={busy}
            aria-invalid={fieldErrors.name ? true : undefined}
            aria-describedby={fieldErrors.name ? 'schedule-name-error' : undefined}
            onChange={(event) => changeForm({ name: event.target.value })}
          />
        </Field>

        <Section id="schedule-when" title="执行时间" description={whenSummary(form)}>
          <ScheduleTimeFields form={form} errors={fieldErrors} disabled={busy} advancedOpen={advancedOpen} onChange={changeForm} />
        </Section>

        <Section id={TASKS_SECTION_ID} title="执行顺序" hint="到点后从上到下依次执行，只保存改过的参数。">
          {tasks}
          {fieldErrors.tasks ? <p className="px-1 text-footnote font-medium text-danger">{fieldErrors.tasks}</p> : null}
        </Section>

        <div className="space-y-1.5">
          <div className={`${card} px-4`}>
            <SwitchRow
              id="schedule-enabled"
              label="启用"
              description="关闭后到点不会运行，仍可随时手动运行。"
              checked={form.enabled}
              disabled={busy}
              onCheckedChange={(enabled) => changeForm({ enabled })}
            />
          </div>
          <p className="px-1 text-footnote text-muted-foreground">定时任务以最低优先级排队。上一次还没执行完时，这次到点会跳过。</p>
        </div>

        {schedule ? <ScheduleRuns runs={schedule.recent_runs} /> : null}

        <div ref={feedbackRef} className={`space-y-3 empty:hidden ${FEEDBACK_SCROLL_MARGIN}`}>
          {taskSummary ? (
            <InlineAlert
              tone="danger"
              title="校验未通过"
              action={<Button variant="outline" size="sm" onClick={() => setEditingId(taskSummary.firstId)}>修改</Button>}
            >
              {taskSummary.message}
            </InlineAlert>
          ) : null}
          {notice ? <InlineAlert tone={notice.tone} title={notice.title} action={noticeAction}>{notice.message}</InlineAlert> : null}
        </div>
      </div>

      <StickyActionBar summary={schedule && dirty ? '有未保存的更改' : undefined}>
        <Button loading={save.isPending} disabled={busy || (schedule !== null && !dirty)} onClick={submit}>
          {save.isPending ? (schedule ? '正在保存…' : '正在新建…') : schedule ? '保存' : '新建定时任务'}
        </Button>
      </StickyActionBar>
      {confirmElement}
      {guard}
    </Page>
  )
}

/**
 * 新建（`scheduleId` 为 null）或编辑定时任务的整页编辑器。新建时读取一次路由 state 里的预填，然后清掉它，
 * 刷新或前进后退回来不会重复载入。保存、删除成功后回到列表。
 */
export function ScheduleEditorScreen({ scheduleId }: { scheduleId: string | null }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [leaving, setLeaving] = useState<string | null>(null)
  const [prefill] = useState(() => (scheduleId === null ? readSchedulePrefill(location.state) : null))
  const schedules = useQuery({ ...schedulesQuery, enabled: scheduleId !== null })
  const handoff = hasSchedulePrefill(location.state)
  const { pathname, search, hash } = location

  useEffect(() => {
    if (handoff) navigate({ pathname, search, hash }, { replace: true, state: null })
  }, [handoff, pathname, search, hash, navigate])

  // 先卸载编辑器（连同未保存拦截）再跳转，保存或删除后离开不会被拦下。
  if (leaving) return <Navigate to={leaving} replace />
  if (scheduleId === null) return <ScheduleEditor schedule={null} prefill={prefill} onLeave={setLeaving} />

  const schedule = schedules.data?.find((candidate) => candidate.id === scheduleId)
  if (schedule) return <ScheduleEditor key={schedule.id} schedule={schedule} prefill={null} onLeave={setLeaving} />

  let body: ReactNode
  if (schedules.isPending) {
    body = <div className={card}><LoadingState label="正在读取定时任务…" /></div>
  } else if (schedules.error) {
    body = (
      <div className={card}>
        <ErrorState message={`无法读取定时任务：${toApiError(schedules.error).message}`} onRetry={() => void schedules.refetch()} retrying={schedules.isFetching} />
      </div>
    )
  } else {
    body = (
      <div className={card}>
        <EmptyState
          icon={CalendarX}
          title="找不到这个定时任务"
          description="它可能已经被删除。"
          action={<Button asChild variant="outline"><Link to={SCHEDULES_PATH}>返回定时任务</Link></Button>}
        />
      </div>
    )
  }
  return <Page title="编辑定时任务" back={SCHEDULES_PATH}>{body}</Page>
}
