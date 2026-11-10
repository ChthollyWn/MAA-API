import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/ui/chip'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { SegmentedControl, type SegmentedOption } from '@/components/ui/segmented-control'
import { LoadingState } from '@/components/ui/states'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import {
  runFeedbackQueryOptions, usePutRunFeedback, type FeedbackInput, type FeedbackView,
} from './api'
import { FEEDBACK_REASONS, type FeedbackReason } from './format'

/** 1–5 分，按分数升序摆放；值用字符串，未选时不命中任何一项。 */
const RATING_OPTIONS: readonly SegmentedOption<string>[] = [
  { value: '1', label: '很差' },
  { value: '2', label: '较差' },
  { value: '3', label: '一般' },
  { value: '4', label: '较好' },
  { value: '5', label: '很好' },
]

const REASON_VALUES = Object.keys(FEEDBACK_REASONS) as FeedbackReason[]

function queryError(value: unknown): string {
  return toApiError(value).message || '请求失败，请稍后重试。'
}

interface FeedbackFormProps {
  runId: string
  /** 已有评价；没有时为 null。 */
  feedback: FeedbackView | null
}

/** 评价表单：只在读取结束后挂载，用已有评价做初始值，之后不再被刷新覆盖。 */
function FeedbackForm({ runId, feedback }: FeedbackFormProps) {
  const [rating, setRating] = useState(() => (feedback ? String(feedback.rating) : ''))
  const [reasons, setReasons] = useState<ReadonlySet<FeedbackReason>>(() => new Set(feedback?.reasons ?? []))
  const [comment, setComment] = useState(() => feedback?.comment ?? '')
  const [error, setError] = useState<string | null>(null)
  const submit = usePutRunFeedback(runId)

  const toggleReason = (reason: FeedbackReason) => {
    setReasons((current) => {
      const next = new Set(current)
      if (next.has(reason)) next.delete(reason)
      else next.add(reason)
      return next
    })
  }

  const handleSubmit = () => {
    const value = Number(rating)
    if (!value) {
      setError('请选择评分后再提交')
      return
    }
    setError(null)
    const trimmed = comment.trim()
    const input: FeedbackInput = {
      rating: value,
      reasons: REASON_VALUES.filter((reason) => reasons.has(reason)),
      comment: trimmed ? trimmed : null,
    }
    submit.mutate(input, {
      onSuccess: () => toast.success('已记录你的评价'),
      onError: (cause) => setError(`提交评价失败：${queryError(cause)}`),
    })
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        handleSubmit()
      }}
    >
      {feedback ? <p className="text-footnote text-muted-foreground">已评价，提交会覆盖</p> : null}

      <Field label="评分">
        <SegmentedControl
          ariaLabel="评分"
          value={rating}
          onValueChange={setRating}
          options={RATING_OPTIONS}
          fullWidth
        />
      </Field>

      <Field label="原因" description="可多选">
        <div className="flex flex-wrap gap-2">
          {REASON_VALUES.map((reason) => (
            <Chip
              key={reason}
              selected={reasons.has(reason)}
              onClick={() => toggleReason(reason)}
            >
              {FEEDBACK_REASONS[reason]}
            </Chip>
          ))}
        </div>
      </Field>

      <Field label="补充说明" htmlFor="run-feedback-comment" hint="最多 500 字">
        <Textarea
          id="run-feedback-comment"
          value={comment}
          maxLength={500}
          placeholder="补充说明（可选）"
          onChange={(event) => setComment(event.target.value)}
        />
      </Field>

      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}

      <Button type="submit" disabled={submit.isPending}>提交评价</Button>
    </form>
  )
}

/** 运行详情页的评价区块：概要卡之后、运行树之前（M99 §4.6）。 */
export function FeedbackSection({ runId }: { runId: string }) {
  const feedback = useQuery(runFeedbackQueryOptions(runId))

  return (
    <Section id="run-feedback" title="这次运行怎么样">
      <div className="rounded-xl border bg-card p-4 shadow-card">
        {feedback.isPending ? (
          <LoadingState label="正在读取评价…" />
        ) : (
          <div className="space-y-4">
            {feedback.error ? (
              <InlineAlert
                tone="danger"
                action={<Button variant="outline" size="sm" onClick={() => void feedback.refetch()}>重试</Button>}
              >
                读取评价失败：{queryError(feedback.error)}
              </InlineAlert>
            ) : null}
            <FeedbackForm key={runId} runId={runId} feedback={feedback.data?.item ?? null} />
          </div>
        )}
      </div>
    </Section>
  )
}

export default FeedbackSection
