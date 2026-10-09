import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useIsMutating } from '@tanstack/react-query'
import { CalendarPlus, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { StickyActionBar } from '@/components/ui/sticky-action-bar'
import { errorMessage, riskSummary, type TaskDraft } from '../form'
import { taskErrorSummaryMessage } from '../pipeline-format'
import { useTaskTypes } from '../task-types'
import { MAX_TASKS, templateFromDrafts } from './drafts'
import { useComposerStore } from './store'
import { TaskListEditor } from './TaskListEditor'
import { composerSubmitKey, useSubmitPipeline, type ComposerNotice } from './use-submit-pipeline'

function RiskLines({ lines }: { lines: readonly string[] }) {
  return (
    <>
      这些参数会消耗游戏内资源：
      {lines.map((line) => <span key={line} className="mt-1.5 block font-medium text-foreground">{line}</span>)}
    </>
  )
}

function fieldErrorSummary(drafts: readonly TaskDraft[], errors: Readonly<Record<string, Readonly<Record<string, string>>>>) {
  const failing = drafts
    .map((draft, index) => ({ id: draft.id, position: index + 1, count: Object.keys(errors[draft.id] ?? {}).length }))
    .filter((entry) => entry.count > 0)
  if (failing.length === 0) return null
  const total = failing.reduce((sum, entry) => sum + entry.count, 0)
  const message = `${taskErrorSummaryMessage(failing.map((entry) => entry.position), total)}，点开修改。`
  return { message, firstId: failing[0].id }
}

/** 任务 › 新建：可选名称、任务列表、底部「开始执行」与「存为定时任务」。草稿存在内存 store 里。 */
export function TaskComposer() {
  const navigate = useNavigate()
  const types = useTaskTypes()
  const taskTypes = types.data ?? []
  const title = useComposerStore((state) => state.title)
  const drafts = useComposerStore((state) => state.drafts)
  const errors = useComposerStore((state) => state.errors)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [notice, setNotice] = useState<ComposerNotice | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const [feedbackVersion, setFeedbackVersion] = useState(0)
  const feedbackRef = useRef<HTMLDivElement>(null)
  const [confirm, confirmElement] = useConfirm()
  const submitting = useIsMutating({ mutationKey: composerSubmitKey }) > 0
  const submit = useSubmitPipeline(taskTypes, {
    onNotice: (next) => {
      setNotice(next)
      setFeedbackVersion((version) => version + 1)
    },
    onSubmitted: () => {
      setNotice(null)
      setSubmitted(true)
      setFeedbackVersion((version) => version + 1)
    },
    onFieldErrors: () => setFeedbackVersion((version) => version + 1),
  })

  // 结果出现在列表下方；提交按钮在屏幕底部，把提示滚进视野。
  useEffect(() => {
    if (feedbackVersion > 0) feedbackRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [feedbackVersion])

  const edited = () => {
    setNotice(null)
    setSubmitted(false)
  }
  const changeDrafts = (next: TaskDraft[]) => {
    useComposerStore.getState().setDrafts(next)
    edited()
  }
  const changeTitle = (next: string) => {
    useComposerStore.getState().setTitle(next)
    edited()
  }

  const start = async () => {
    if (drafts.length === 0 || submitting) return
    const risks = riskSummary(drafts, taskTypes)
    if (risks.length > 0) {
      const ok = await confirm({ title: '确认消耗资源？', description: <RiskLines lines={risks} />, confirmText: '开始执行' })
      if (!ok) return
    }
    setNotice(null)
    setSubmitted(false)
    const store = useComposerStore.getState()
    store.setErrors({})
    submit.mutate({ idempotencyKey: store.takeIdempotencyKey(), drafts: store.drafts, title: store.title.trim() })
  }

  const saveAsSchedule = () => {
    navigate('/tasks/schedules/new', { state: { composerPrefill: { name: title.trim(), template: templateFromDrafts(drafts) } } })
  }

  const fieldErrors = fieldErrorSummary(drafts, errors)
  const count = drafts.length

  return (
    <>
      <div className="space-y-6 pb-2">
        <Field label="流水线名称（可选）" htmlFor="pipeline-title">
          <Input
            id="pipeline-title"
            maxLength={64}
            placeholder="例如：每日基建与刷图"
            autoComplete="off"
            value={title}
            disabled={submitting}
            onChange={(event) => changeTitle(event.target.value)}
          />
        </Field>

        <Section title="执行顺序" hint="从上到下依次执行，只提交你改过的参数。">
          {types.isPending ? (
            <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取任务类型…" /></div>
          ) : types.error ? (
            <div className="rounded-xl border bg-card shadow-card">
              <ErrorState message={`读取任务类型失败：${errorMessage(types.error)}`} onRetry={() => void types.refetch()} retrying={types.isFetching} />
            </div>
          ) : taskTypes.length === 0 ? (
            <div className="rounded-xl border bg-card shadow-card"><EmptyState title="还没有可用的任务类型" description="刷新页面后再试" /></div>
          ) : (
            <TaskListEditor
              taskTypes={taskTypes}
              drafts={drafts}
              onChange={changeDrafts}
              errors={errors}
              disabled={submitting}
              editingId={editingId}
              onEditingIdChange={setEditingId}
            />
          )}
        </Section>

        <div ref={feedbackRef} className="scroll-mt-28 scroll-mb-[calc(var(--nav-offset)+var(--keyboard-inset)+var(--nav-safe)+5.5rem)] space-y-3 empty:hidden">
          {fieldErrors ? (
            <InlineAlert
              tone="danger"
              title="校验未通过"
              action={<Button variant="outline" size="sm" onClick={() => setEditingId(fieldErrors.firstId)}>修改</Button>}
            >
              {fieldErrors.message}
            </InlineAlert>
          ) : null}
          {notice ? <InlineAlert tone={notice.tone}>{notice.message}</InlineAlert> : null}
          {submitted ? (
            <InlineAlert
              tone="success"
              action={<Button asChild variant="outline" size="sm"><Link to="/tasks/queue">查看队列</Link></Button>}
            >
              流水线已加入队列
            </InlineAlert>
          ) : null}
        </div>
      </div>

      <StickyActionBar>
        <Button variant="outline" disabled={count === 0 || submitting} onClick={saveAsSchedule}>
          <CalendarPlus aria-hidden="true" />
          存为定时任务
        </Button>
        <Button loading={submitting} disabled={count === 0 || count > MAX_TASKS || taskTypes.length === 0} onClick={() => void start()}>
          {submitting ? null : <Play aria-hidden="true" />}
          {submitting ? '正在提交…' : '开始执行'}
        </Button>
      </StickyActionBar>
      {confirmElement}
    </>
  )
}
