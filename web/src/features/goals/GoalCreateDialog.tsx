import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import { settingsQueryOptions } from '@/features/settings/use-settings'
import { createGoal, describeGoalError, type GoalCreate } from './api'
import {
  DEFAULT_REVIEW_TIME,
  localDateTimeToIso,
  reviewCronFromTime,
  reviewTimeFromCron,
} from './format'

export interface GoalCreateDialogProps {
  onClose: () => void
}

type FieldErrors = Partial<Record<'title' | 'target' | 'description' | 'deadline' | 'review', string>>

/**
 * 新建目标（M82 规格 §4.6）：标题、目标原文、补充说明、截止与复盘时间。
 *
 * 复盘时间在界面上是 `HH:MM`，提交时换成 cron `MM HH * * *`。设置里的
 * `goals.default_review_cron` 不是「每天某时某分」的形状时，界面只读显示原
 * cron，并让后端按设置值走（不提交 `review_cron`）。
 */
export function GoalCreateDialog({ onClose }: GoalCreateDialogProps) {
  const navigate = useNavigate()
  const settings = useQuery(settingsQueryOptions)
  const [title, setTitle] = useState('')
  const [targetText, setTargetText] = useState('')
  const [description, setDescription] = useState('')
  const [deadline, setDeadline] = useState('')
  /** null 表示没动过，提交时不带 `review_cron`，由后端取设置值。 */
  const [reviewTime, setReviewTime] = useState<string | null>(null)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [saving, setSaving] = useState(false)

  const rawCron = settings.data?.values['goals.default_review_cron']?.value
  const settingCron = typeof rawCron === 'string' ? rawCron.trim() : ''
  const parsedCron = reviewTimeFromCron(settingCron)
  /** 设置值存在且不是每天某时某分：只读显示原 cron。 */
  const fixedCron = settingCron && parsedCron === null ? settingCron : null
  const displayedReviewTime = reviewTime ?? parsedCron ?? DEFAULT_REVIEW_TIME

  const change = (patch: Partial<Record<'title' | 'target' | 'description' | 'deadline', string>>) => {
    if (patch.title !== undefined) setTitle(patch.title)
    if (patch.target !== undefined) setTargetText(patch.target)
    if (patch.description !== undefined) setDescription(patch.description)
    if (patch.deadline !== undefined) setDeadline(patch.deadline)
    setErrors({})
  }

  const validate = (): FieldErrors => {
    const found: FieldErrors = {}
    if (!title.trim()) found.title = '请填写标题'
    if (!targetText.trim()) found.target = '请填写目标'
    if (description.trim().length > 2000) found.description = '补充说明最多 2000 字'
    if (deadline && localDateTimeToIso(deadline) === null) found.deadline = '请填写有效的截止时间'
    if (!fixedCron && reviewTime !== null && reviewCronFromTime(reviewTime) === null) {
      found.review = '请填写有效的复盘时间'
    }
    return found
  }

  const save = async () => {
    const found = validate()
    setErrors(found)
    if (Object.keys(found).length > 0) return
    const body: GoalCreate = {
      title: title.trim(),
      description: description.trim() ? description.trim() : null,
      target: { text: targetText.trim() },
      deadline: deadline ? localDateTimeToIso(deadline) : null,
      review_cron: fixedCron || reviewTime === null ? null : reviewCronFromTime(reviewTime),
    }
    setSaving(true)
    try {
      const goal = await createGoal(body)
      onClose()
      toast.success('已新建目标')
      navigate(`/more/goals/${goal.id}`)
    } catch (caught) {
      toast.error(`新建目标失败：${describeGoalError(caught)}`)
    } finally {
      setSaving(false)
    }
  }

  const footer = (
    <div className="flex gap-2">
      <Button type="button" variant="outline" className="flex-1" disabled={saving} onClick={onClose}>
        取消
      </Button>
      <Button type="button" className="flex-1" loading={saving} onClick={() => void save()}>
        新建目标
      </Button>
    </div>
  )

  return (
    <ResponsiveSheet
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title="新建目标"
      footer={footer}
      desktop="dialog"
    >
      <div className="space-y-4 pb-2">
        <Field label="标题" htmlFor="goal-title" error={errors.title}>
          <Input
            id="goal-title"
            value={title}
            maxLength={64}
            aria-invalid={errors.title ? true : undefined}
            onChange={(event) => change({ title: event.target.value })}
          />
        </Field>

        <Field
          label="目标"
          htmlFor="goal-target"
          description="写清要达成什么，例如「银灰精二、三技能专三」"
          error={errors.target}
        >
          <Textarea
            id="goal-target"
            value={targetText}
            maxLength={1000}
            aria-invalid={errors.target ? true : undefined}
            onChange={(event) => change({ target: event.target.value })}
          />
        </Field>

        <Field label="补充说明" htmlFor="goal-description" description="可空" error={errors.description}>
          <Textarea
            id="goal-description"
            value={description}
            maxLength={2000}
            aria-invalid={errors.description ? true : undefined}
            onChange={(event) => change({ description: event.target.value })}
          />
        </Field>

        <Field label="截止" htmlFor="goal-deadline" description="可空" error={errors.deadline}>
          <Input
            id="goal-deadline"
            type="datetime-local"
            value={deadline}
            aria-invalid={errors.deadline ? true : undefined}
            onChange={(event) => change({ deadline: event.target.value })}
          />
        </Field>

        {fixedCron ? (
          <Field label="复盘时间" htmlFor="goal-review-time" description="设置里不是每天一次的写法，只能沿用。">
            <Input id="goal-review-time" readOnly value={fixedCron} className="font-mono" />
          </Field>
        ) : (
          <Field label="复盘时间" htmlFor="goal-review-time" error={errors.review}>
            <Input
              id="goal-review-time"
              type="time"
              value={displayedReviewTime}
              aria-invalid={errors.review ? true : undefined}
              onChange={(event) => {
                setReviewTime(event.target.value)
                setErrors({})
              }}
            />
          </Field>
        )}
      </div>
    </ResponsiveSheet>
  )
}
