import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookX, RotateCcw, Trash2 } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { Page } from '@/components/layout/Page'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { StickyActionBar } from '@/components/ui/sticky-action-bar'
import { SwitchRow } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import { useUnsavedChangesGuard } from '@/lib/use-unsaved-changes'
import type { components } from '@/types/api'
import { deleteKnowledgeEntry, invalidateKnowledge, knowledgeDetailQuery, saveKnowledgeEntry, type KnowledgeWrite } from './api'
import {
  KIND_LABELS,
  KNOWLEDGE_KINDS,
  KNOWLEDGE_PATH,
  MAX_BODY_CHARS,
  MAX_TITLE_CHARS,
  MAX_WHEN_TO_USE_CHARS,
  describeKnowledgeError,
  emptyKnowledgeForm,
  formatUpdatedAt,
  indexLine,
  knowledgeFormFromDetail,
  knowledgeSnapshot,
  lengthHint,
  serverFieldErrors,
  validateKnowledgeForm,
  type KnowledgeForm,
  type KnowledgeFormErrors,
} from './format'

const card = 'rounded-xl border bg-card shadow-card'

const TEXT_INPUT = { autoComplete: 'off', autoCapitalize: 'none', autoCorrect: 'off', spellCheck: false } as const

/** 字数与上限：贴着标签右侧显示，随时能看出还剩多少。 */
function Counter({ value, max }: { value: string; max: number }) {
  return <span className="text-footnote tabular-nums text-muted-foreground">{lengthHint(value, max)}</span>
}

function withoutKey(errors: KnowledgeFormErrors, key: keyof KnowledgeFormErrors): KnowledgeFormErrors {
  if (!(key in errors)) return errors
  const next = { ...errors }
  delete next[key]
  return next
}

interface KnowledgeEditorProps {
  /** 编辑已有条目；新建时为 null。 */
  entry: components['schemas']['KnowledgeDetail'] | null
  /** 保存、删除或恢复默认成功后离开编辑器（不经未保存拦截）。 */
  onLeave: (path: string) => void
}

function KnowledgeEditor({ entry, onLeave }: KnowledgeEditorProps) {
  const queryClient = useQueryClient()
  const [initial] = useState(() => (entry ? knowledgeFormFromDetail(entry) : emptyKnowledgeForm()))
  const [baseline, setBaseline] = useState(() => knowledgeSnapshot(initial))
  const [form, setForm] = useState(initial)
  const [fieldErrors, setFieldErrors] = useState<KnowledgeFormErrors>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [confirm, confirmElement] = useConfirm()
  const bodyRef = useRef<HTMLTextAreaElement>(null)

  const isNew = entry === null
  const builtin = entry?.source === 'builtin'

  // 只有字段级校验：条目数与术语表/档案正文合计都不设上限（注入多少由模型上下文决定）。
  const errors = validateKnowledgeForm(form, { isNew })
  const dirty = knowledgeSnapshot(form) !== baseline
  const guard = useUnsavedChangesGuard(dirty, isNew ? '新的知识条目还没有创建，离开后填写的内容会丢失。' : `「${entry.title}」有未保存的更改，离开后这些更改会丢失。`)

  // 正文随内容增高：先归零再量，删行后不留空白。
  useLayoutEffect(() => {
    const node = bodyRef.current
    if (!node) return
    node.style.height = 'auto'
    if (node.scrollHeight > 0) {
      node.style.height = `${node.scrollHeight + node.offsetHeight - node.clientHeight}px`
    }
  }, [form.body])

  const change = (patch: Partial<KnowledgeForm>) => {
    setForm((current) => ({ ...current, ...patch }))
    setFieldErrors((current) => (Object.keys(patch) as (keyof KnowledgeForm)[]).reduce(withoutKey, current))
    setNotice(null)
  }

  const save = useMutation({
    mutationFn: (body: KnowledgeWrite) => saveKnowledgeEntry(isNew ? form.id.trim() : entry.id, body),
    onSuccess: async (saved) => {
      setBaseline(knowledgeSnapshot(form))
      await invalidateKnowledge(queryClient, saved.id)
      toast.success(isNew ? `已创建「${saved.title}」` : `已保存「${saved.title}」`)
      onLeave(KNOWLEDGE_PATH)
    },
    onError: (error) => {
      setFieldErrors(serverFieldErrors(error))
      setNotice(describeKnowledgeError(error))
    },
  })

  const remove = useMutation({
    mutationFn: () => deleteKnowledgeEntry(entry!.id),
    onSuccess: async () => {
      await invalidateKnowledge(queryClient, entry?.id)
      toast.success(builtin ? `已恢复「${entry?.title}」的默认内容` : `已删除「${entry?.title}」`)
      onLeave(KNOWLEDGE_PATH)
    },
    onError: (error) => setNotice(describeKnowledgeError(error)),
  })

  const busy = save.isPending || remove.isPending

  const submit = () => {
    if (busy) return
    if (Object.keys(errors).length > 0) {
      setNotice('还有字段没填好或超出字数，请按字段提示修改。')
      return
    }
    setFieldErrors({})
    setNotice(null)
    save.mutate({
      kind: form.kind,
      title: form.title.trim(),
      when_to_use: form.whenToUse.trim(),
      body: form.body,
      enabled: form.enabled,
    })
  }

  const restore = async () => {
    if (!entry) return
    const ok = await confirm({
      title: `恢复「${entry.title}」的内置内容？`,
      description: '你对这一条的修改会被丢掉，换回随版本发布的内容。',
      confirmText: '恢复默认',
    })
    if (ok) remove.mutate()
  }

  const destroy = async () => {
    if (!entry) return
    const ok = await confirm({
      title: `删除自定义条目「${entry.title}」？`,
      description: '删除后索引里不再有这一条，Agent 也读不到它；这个操作无法撤销。',
      confirmText: '删除条目',
      tone: 'danger',
    })
    if (ok) remove.mutate()
  }

  const actions: ReactNode = entry && (builtin ? entry.modified : true) ? (
    <ActionMenu
      label="知识条目操作"
      title={entry.title}
      disabled={busy}
      actions={builtin
        ? [{ label: '恢复默认', description: '丢掉修改，换回内置内容', icon: RotateCcw, onSelect: () => void restore() }]
        : [{ label: '删除', icon: Trash2, tone: 'danger', onSelect: () => void destroy() }]}
    />
  ) : undefined

  const updatedAt = formatUpdatedAt(entry?.updated_at)
  const preview = indexLine({
    id: form.id.trim() || '（标识）',
    title: form.title.trim() || '（标题）',
    when_to_use: form.whenToUse.trim() || '（什么时候用）',
  })

  return (
    <Page title={isNew ? '新建知识条目' : '编辑知识条目'} back={KNOWLEDGE_PATH} width="narrow" actions={actions}>
      <div className="space-y-5 pb-2">
        {notice ? <InlineAlert tone="danger" title="没有保存">{notice}</InlineAlert> : null}

        <Field
          label="标识"
          htmlFor="knowledge-id"
          description={isNew ? '索引里的名字，创建后不可修改' : '创建后不可修改'}
          error={fieldErrors.id ?? errors.id}
          errorId="knowledge-id-error"
        >
          <Input
            id="knowledge-id"
            className="font-mono"
            value={form.id}
            readOnly={!isNew}
            disabled={busy}
            placeholder="例如：event-shop-priority"
            aria-invalid={fieldErrors.id ?? errors.id ? true : undefined}
            aria-describedby={fieldErrors.id ?? errors.id ? 'knowledge-id-error' : undefined}
            onChange={(event) => change({ id: event.target.value })}
            {...TEXT_INPUT}
          />
        </Field>

        <Field
          label="类型"
          htmlFor="knowledge-kind"
          description={builtin ? '内置条目的类型不可修改' : '术语表与用户档案会整段进模型上下文'}
          error={fieldErrors.kind ?? errors.kind}
          errorId="knowledge-kind-error"
        >
          <NativeSelect
            id="knowledge-kind"
            value={form.kind}
            disabled={builtin || busy}
            aria-invalid={fieldErrors.kind ? true : undefined}
            aria-describedby={fieldErrors.kind ? 'knowledge-kind-error' : undefined}
            onChange={(event) => change({ kind: event.target.value as KnowledgeForm['kind'] })}
          >
            {KNOWLEDGE_KINDS.map((kind) => <option key={kind} value={kind}>{KIND_LABELS[kind]}</option>)}
          </NativeSelect>
        </Field>

        <Field
          label="标题"
          htmlFor="knowledge-title"
          labelAside={<Counter value={form.title} max={MAX_TITLE_CHARS} />}
          error={fieldErrors.title ?? errors.title}
          errorId="knowledge-title-error"
        >
          <Input
            id="knowledge-title"
            value={form.title}
            maxLength={MAX_TITLE_CHARS}
            disabled={busy}
            placeholder="例如：查询账号资源"
            aria-invalid={fieldErrors.title ?? errors.title ? true : undefined}
            aria-describedby={fieldErrors.title ?? errors.title ? 'knowledge-title-error' : undefined}
            onChange={(event) => change({ title: event.target.value })}
            {...TEXT_INPUT}
          />
        </Field>

        <Field
          label="什么时候用"
          htmlFor="knowledge-when"
          labelAside={<Counter value={form.whenToUse} max={MAX_WHEN_TO_USE_CHARS} />}
          description="用户提到什么时该读这一条"
          error={fieldErrors.whenToUse ?? errors.whenToUse}
          errorId="knowledge-when-error"
        >
          <Input
            id="knowledge-when"
            value={form.whenToUse}
            maxLength={MAX_WHEN_TO_USE_CHARS}
            disabled={busy}
            placeholder="例如：用户问合成玉、源石或还能抽多少"
            aria-invalid={fieldErrors.whenToUse ?? errors.whenToUse ? true : undefined}
            aria-describedby={fieldErrors.whenToUse ?? errors.whenToUse ? 'knowledge-when-error' : undefined}
            onChange={(event) => change({ whenToUse: event.target.value })}
            {...TEXT_INPUT}
          />
        </Field>

        <Field
          label="正文"
          htmlFor="knowledge-body"
          labelAside={<Counter value={form.body} max={MAX_BODY_CHARS} />}
          description="Markdown；被读到才会进模型上下文"
          error={fieldErrors.body ?? errors.body}
          errorId="knowledge-body-error"
        >
          <Textarea
            id="knowledge-body"
            ref={bodyRef}
            className="max-h-[70dvh] min-h-40 resize-none overflow-y-auto font-mono"
            value={form.body}
            maxLength={MAX_BODY_CHARS}
            disabled={busy}
            aria-invalid={fieldErrors.body ?? errors.body ? true : undefined}
            aria-describedby={fieldErrors.body ?? errors.body ? 'knowledge-body-error' : undefined}
            onChange={(event) => change({ body: event.target.value })}
          />
        </Field>

        <div className={`${card} px-4 py-1`}>
          <SwitchRow
            id="knowledge-enabled"
            label="启用"
            description="停用后不进索引，Agent 也读不到它"
            checked={form.enabled}
            disabled={busy}
            onCheckedChange={(enabled) => change({ enabled })}
          />
          {errors.enabled ? <p className="pb-3 text-footnote font-medium text-danger">{errors.enabled}</p> : null}
        </div>

        <Section title="索引预览" hint="Agent 的系统消息里会这样列出这一条。">
          <pre className="overflow-x-auto overscroll-x-contain rounded-xl border bg-card px-3.5 py-3 font-mono text-footnote leading-6 shadow-card">{preview}</pre>
        </Section>

        {updatedAt ? <p className="px-1 text-footnote text-muted-foreground">上次更新：{updatedAt}</p> : null}
      </div>

      <StickyActionBar summary={dirty ? '有未保存的更改' : undefined}>
        <Button loading={save.isPending} disabled={busy} onClick={submit}>
          {isNew ? (save.isPending ? '正在创建…' : '创建条目') : save.isPending ? '正在保存…' : '保存'}
        </Button>
      </StickyActionBar>
      {confirmElement}
      {guard}
    </Page>
  )
}

/** 新建（`entryId` 为 null）或编辑知识条目的整页编辑器；换一条时整页重挂，状态不串。 */
export function KnowledgeEditorScreen({ entryId }: { entryId: string | null }) {
  const [leaving, setLeaving] = useState<string | null>(null)
  const detail = useQuery({ ...knowledgeDetailQuery(entryId ?? ''), enabled: entryId !== null })

  if (leaving) return <Navigate to={leaving} replace />
  if (entryId === null) return <KnowledgeEditor entry={null} onLeave={setLeaving} />
  if (detail.data) return <KnowledgeEditor key={detail.data.id} entry={detail.data} onLeave={setLeaving} />

  let body: ReactNode
  if (detail.isError) {
    const failure = toApiError(detail.error)
    const missing = failure.status === 404 || failure.code === 'KNOWLEDGE_NOT_FOUND'
    body = (
      <div className={card}>
        {missing ? (
          <EmptyState
            icon={BookX}
            title="找不到这个知识条目"
            description="条目可能已经被删除"
            action={<Button asChild variant="outline"><Link to={KNOWLEDGE_PATH}>返回知识库</Link></Button>}
          />
        ) : (
          <ErrorState
            title="知识条目读取失败"
            message={describeKnowledgeError(detail.error)}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        )}
      </div>
    )
  } else {
    body = <div className={card}><LoadingState label="正在读取知识条目…" /></div>
  }
  return <Page title="编辑知识条目" back={KNOWLEDGE_PATH} width="narrow">{body}</Page>
}
