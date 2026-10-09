import { useEffect, useId, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Check, CircleHelp, SkipForward } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { cn } from '@/lib/utils'
import { questionRemainingLabel } from './format'
import type { AgentQuestionPrompt } from './types'

/** 卡片的终态：用户答的、跳过的，或「已经结束」（本地超时 / 后端已终结）。 */
type QuestionOutcome = 'answered' | 'skipped' | 'expired' | 'stale'

const TERMINAL_TEXT: Record<QuestionOutcome, string> = {
  answered: '已提交选择，Agent 正在继续本轮。',
  skipped: '已跳过这次提问，本轮已结束。',
  expired: '提问已超时，本轮已结束。',
  stale: '这次提问已经结束（可能已在其他页面处理），本轮不再等待。',
}

const TERMINAL_BADGE: Record<QuestionOutcome, { label: string; variant: 'success' | 'neutral' | 'danger' }> = {
  answered: { label: '已提交', variant: 'success' },
  skipped: { label: '已跳过', variant: 'neutral' },
  expired: { label: '已超时', variant: 'danger' },
  stale: { label: '已结束', variant: 'neutral' },
}

export interface QuestionCardProps {
  /** SSE 的 `question_required` 载荷或本会话待答列表里的 wire 都能直接传进来。 */
  question: AgentQuestionPrompt
  /**
   * 提问以「用户已处理」收尾时回调：调用方清掉本轮卡、让待答列表跟上。
   * 超时不回调 —— 那张卡要留在原位显示超时文案，直到本轮结束。
   */
  onResolved: (questionId: string) => void
}

/** 对话里的提问卡：Agent 就若干选项征求裁决，选完本轮继续，跳过或超时本轮结束。 */
export function QuestionCard({ question, onResolved }: QuestionCardProps) {
  const titleId = useId()
  const questionId = useId()
  const [selected, setSelected] = useState<string[]>([])
  const [outcome, setOutcome] = useState<QuestionOutcome | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const deadline = Date.parse(question.expires_at)
  const hasDeadline = Number.isFinite(deadline)
  // 没有有效期（字段缺失或读不出来）就当作不会本地超时：宁可让后端来判终态，
  // 也不要凭一个坏时间戳把还能答的卡禁用掉。
  const expired = outcome === null && hasDeadline && deadline - now <= 0
  const open = outcome === null && !expired
  const terminal = outcome ?? (expired ? 'expired' : null)
  const closed = terminal !== null

  // 跟确认卡不同，提问有 10 分钟的死线：每秒重算一次剩余时间，到点自动切超时态，
  // 之后计时器自己停掉（终态不再需要走秒）。
  useEffect(() => {
    if (!open) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [open])

  const settle = (next: QuestionOutcome) => {
    setOutcome(next)
    setFailure(null)
    onResolved(question.question_id)
  }

  const fail = (error: unknown) => {
    const apiError = toApiError(error)
    // 这张卡已经过期或已被处理不是错误：切成终态，别给用户一条红色报错。
    if (apiError.code === 'QUESTION_EXPIRED') return setOutcome('expired')
    if (apiError.code === 'QUESTION_ALREADY_ANSWERED' || apiError.code === 'QUESTION_NOT_FOUND') {
      return settle('stale')
    }
    setFailure(apiError.message || '提交失败，请重试。')
  }

  const resolve = useMutation({
    mutationFn: async (input: { action: 'answer'; selected: string[] } | { action: 'skip' }) => {
      if (input.action === 'skip') {
        const { error, response } = await api.POST('/api/agent/questions/{question_id}/skip', {
          params: { path: { question_id: question.question_id } },
        })
        if (error) throw toApiError(error, response)
        return
      }
      const { error, response } = await api.POST('/api/agent/questions/{question_id}', {
        params: { path: { question_id: question.question_id } },
        body: { selected: input.selected },
      })
      if (error) throw toApiError(error, response)
    },
    onSuccess: (_data, input) => settle(input.action === 'skip' ? 'skipped' : 'answered'),
    onError: fail,
  })

  const toggle = (label: string) => {
    setSelected((current) => (current.includes(label)
      ? current.filter((item) => item !== label)
      : question.multiple ? [...current, label] : [label]))
  }

  const busy = resolve.isPending
  const submitting = busy && resolve.variables?.action === 'answer'
  const skipping = busy && resolve.variables?.action === 'skip'
  // 必须选中才能提交；跳过不需要选。整卡在终态一律禁用。
  const canSubmit = !closed && !busy && selected.length > 0
  const remainingLabel = questionRemainingLabel(question.expires_at, now)

  return (
    <article aria-labelledby={titleId} className="overflow-hidden rounded-xl border bg-card shadow-card">
      <div className="space-y-3 p-4">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-primary-soft text-primary-text">
            <CircleHelp aria-hidden="true" className="size-[1.125rem]" />
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 id={titleId} className="text-[0.9375rem] font-semibold leading-6">需要你选择</h3>
              {terminal ? (
                <Badge variant={TERMINAL_BADGE[terminal].variant}>{TERMINAL_BADGE[terminal].label}</Badge>
              ) : (
                <Badge variant="warning" className="tabular-nums">{remainingLabel ?? '等待回答'}</Badge>
              )}
            </div>
            <p id={questionId} className="break-words text-body text-foreground">{question.question}</p>
          </div>
        </div>

        <div
          role={question.multiple ? 'group' : 'radiogroup'}
          aria-labelledby={titleId}
          aria-describedby={questionId}
          className="space-y-2"
        >
          {question.options.map((option, index) => {
            const checked = selected.includes(option.label)
            return (
              <button
                key={`${index}-${option.label}`}
                type="button"
                role={question.multiple ? 'checkbox' : 'radio'}
                aria-checked={checked}
                disabled={closed || busy}
                onClick={() => toggle(option.label)}
                className={cn(
                  'flex min-h-11 w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors duration-150',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  'disabled:pointer-events-none disabled:opacity-45',
                  checked ? 'border-primary/60 bg-primary-soft' : 'border-border bg-surface hover:bg-surface-2',
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    'mt-0.5 grid size-5 shrink-0 place-items-center border transition-colors duration-150',
                    question.multiple ? 'rounded-[0.3rem]' : 'rounded-full',
                    checked ? 'border-primary bg-primary text-primary-foreground' : 'border-border-strong bg-surface',
                  )}
                >
                  {checked ? <Check className="size-3.5" /> : null}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-sm font-medium">{option.label}</span>
                  {option.description ? (
                    <span className="mt-0.5 block break-words text-footnote text-muted-foreground">{option.description}</span>
                  ) : null}
                </span>
              </button>
            )
          })}
        </div>

        {failure ? <InlineAlert tone="danger">提交失败：{failure}</InlineAlert> : null}
        {/* 终态文案挂在 aria-live 上：倒计时每秒都在变，整卡做 live region 会把读屏器刷爆。 */}
        <p role="status" aria-live="polite" className="text-footnote text-muted-foreground">
          {terminal
            ? TERMINAL_TEXT[terminal]
            : question.multiple
              ? '选多项，或直接跳过结束本轮。'
              : '选一项，或直接跳过结束本轮。'}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2 border-t bg-surface-2/40 p-3">
        <Button type="button" variant="outline" disabled={closed || busy} loading={skipping} onClick={() => resolve.mutate({ action: 'skip' })}>
          {skipping ? null : <SkipForward aria-hidden="true" />}
          {skipping ? '正在跳过…' : '跳过'}
        </Button>
        <Button type="button" disabled={!canSubmit} loading={submitting} onClick={() => resolve.mutate({ action: 'answer', selected })}>
          {submitting ? null : <Check aria-hidden="true" />}
          {submitting ? '正在提交…' : '提交'}
        </Button>
      </div>
    </article>
  )
}
