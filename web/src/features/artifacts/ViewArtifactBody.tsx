import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { toApiError } from '@/api/errors'
import { isRecord } from '@/features/agent/format'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { toast } from '@/components/ui/toaster'
import { formatDateTime } from '@/features/tickets/format'
import { useSubmitArtifactForm } from './api'
import {
  initialValues,
  parseView,
  toSubmitPayload,
  validateValues,
  type FormValue,
  type FormValues,
  type ParsedView,
} from './view/schema'
import { ViewRenderer } from './view/ViewRenderer'
import type { ArtifactDetailView } from '@/features/tickets/workspace-api'

export interface ViewArtifactBodyProps {
  artifact: ArtifactDetailView
  ticketEnded: boolean
}

interface Submission {
  at: string
  values: Record<string, unknown>
}

function submissionsOf(meta: Record<string, unknown>): Submission[] {
  const raw = meta.submissions
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (!isRecord(item) || typeof item.at !== 'string' || !isRecord(item.values)) return []
    return [{ at: item.at, values: item.values }]
  })
}

function parseContent(content: string): ParsedView | null {
  try {
    return parseView(JSON.parse(content) as unknown)
  } catch {
    return null
  }
}

function Unavailable({ artifactId }: { artifactId: string }) {
  return (
    <InlineAlert
      tone="warning"
      title="视图无法显示"
      action={(
        <Button asChild variant="outline" size="sm">
          <Link to={`/more/artifacts/${artifactId}`}>在产物页打开</Link>
        </Button>
      )}
    >
      正文不是可渲染的视图，或版本不受支持。
    </InlineAlert>
  )
}

function ViewForm({
  view,
  artifact,
  ticketEnded,
}: {
  view: ParsedView
  artifact: ArtifactDetailView
  ticketEnded: boolean
}) {
  const submissions = submissionsOf(artifact.meta)
  const latest = submissions.at(-1)
  const [values, setValues] = useState<FormValues>(() => initialValues(view.fields, latest?.values))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [message, setMessage] = useState<string | null>(null)
  const [editing, setEditing] = useState(latest === undefined)
  const [submittedAt, setSubmittedAt] = useState<string | null>(latest?.at ?? null)
  const [endedByServer, setEndedByServer] = useState(false)
  const mutation = useSubmitArtifactForm(artifact.id, artifact.ticket_id ?? '')
  const ended = ticketEnded || endedByServer

  const change = (name: string, value: FormValue) => {
    setValues((current) => ({ ...current, [name]: value }))
    setErrors((current) => {
      if (!(name in current)) return current
      const next = { ...current }
      delete next[name]
      return next
    })
  }

  const submit = () => {
    const found = validateValues(view.fields, values)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    setMessage(null)
    const payload = toSubmitPayload(view.fields, values)
    mutation.mutate(payload, {
      onSuccess: (result) => {
        setSubmittedAt(result.submitted_at)
        setValues(initialValues(view.fields, payload))
        setEditing(false)
        setErrors({})
        toast.success('已提交表单')
      },
      onError: (error) => {
        const apiError = toApiError(error)
        if (apiError.status === 409 || apiError.code === 'TICKET_ENDED') {
          setEndedByServer(true)
          setEditing(false)
          return
        }
        const details = apiError.details
        const fields = isRecord(details) && isRecord(details.fields) ? details.fields : null
        if (apiError.status === 400 && fields) {
          const next: Record<string, string> = {}
          for (const [key, reason] of Object.entries(fields)) {
            if (typeof reason === 'string') next[key] = reason
          }
          setErrors(next)
          setMessage(apiError.message)
          return
        }
        setMessage(`提交表单失败：${apiError.message}`)
      },
    })
  }

  return (
    <div className="space-y-3">
      {ended ? <InlineAlert tone="warning">工单已结束，不能再提交</InlineAlert> : null}
      {message ? <InlineAlert tone="danger">{message}</InlineAlert> : null}
      {!editing && submittedAt ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-footnote text-muted-foreground">已提交 · {formatDateTime(submittedAt)}</p>
          {ended ? null : (
            <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
              修改后重新提交
            </Button>
          )}
        </div>
      ) : null}
      <ViewRenderer
        view={view}
        mode={editing && !ended ? 'interactive' : 'readonly'}
        values={values}
        errors={errors}
        onChange={change}
        onSubmit={submit}
        submitting={mutation.isPending}
      />
      {view.dropped > 0 ? (
        <p className="text-footnote text-muted-foreground">有 {view.dropped} 个组件无法显示</p>
      ) : null}
    </div>
  )
}

/**
 * 视图产物的正文（M90 §4.6.3）：解析、渲染与表单提交状态都在这里。
 *
 * 父组件用 `key={artifact.id}` 让它换产物时重置；正文坏了或 `meta.view_version`
 * 不是 1 时只显示提示，不渲染任何视图组件。
 */
export function ViewArtifactBody({ artifact, ticketEnded }: ViewArtifactBodyProps) {
  const view = useMemo(() => parseContent(artifact.content), [artifact.content])
  if (!view || artifact.meta.view_version !== 1) return <Unavailable artifactId={artifact.id} />
  return <ViewForm key={artifact.id} view={view} artifact={artifact} ticketEnded={ticketEnded} />
}
