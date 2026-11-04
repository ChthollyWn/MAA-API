import { useState, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookX, RotateCcw, Trash2 } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Disclosure } from '@/components/ui/disclosure'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { StickyActionBar } from '@/components/ui/sticky-action-bar'
import { SwitchRow } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import { useUnsavedChangesGuard } from '@/lib/use-unsaved-changes'
import type { components } from '@/types/api'
import {
  agentSpecsQuery,
  deleteSkill,
  invalidateSkills,
  saveSkill,
  skillDetailQuery,
  type SkillWriteRequest,
} from './api'
import {
  MAX_BODY_CHARS,
  MAX_DESCRIPTION_CHARS,
  MAX_TITLE_CHARS,
  SKILLS_PATH,
  describeSkillError,
  emptySkillForm,
  skillAgentOptions,
  skillFormFromDetail,
  skillServerFieldErrors,
  skillSnapshot,
  skillWriteFromForm,
  specTitle,
  validateSkillForm,
  type SkillForm,
  type SkillFormErrors,
} from './format'

const card = 'rounded-xl border bg-card shadow-card'

const TEXT_INPUT = { autoComplete: 'off', autoCapitalize: 'none', autoCorrect: 'off', spellCheck: false } as const

/** 正文计数：规格要求显示「{n}/20000」。 */
function BodyCounter({ value }: { value: string }) {
  return <span className="text-footnote tabular-nums text-muted-foreground">{value.length}/{MAX_BODY_CHARS}</span>
}

interface SkillEditorProps {
  /** 编辑已有技能；新建时为 null。 */
  skill: components['schemas']['SkillDetail'] | null
  /** 保存、删除或恢复默认成功后离开编辑器（不经未保存拦截）。 */
  onLeave: (path: string) => void
}

function SkillEditor({ skill, onLeave }: SkillEditorProps) {
  const queryClient = useQueryClient()
  const [initial] = useState(() => (skill ? skillFormFromDetail(skill) : emptySkillForm()))
  const [baseline, setBaseline] = useState(() => skillSnapshot(initial))
  const [form, setForm] = useState(initial)
  const [fieldErrors, setFieldErrors] = useState<SkillFormErrors>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [confirm, confirmElement] = useConfirm()
  const specs = useQuery(agentSpecsQuery)

  const isNew = skill === null
  const builtin = skill?.source === 'builtin'
  const agentOptions = skillAgentOptions(specs.data ?? [])
  const errors = validateSkillForm(form, { isNew })
  const dirty = skillSnapshot(form) !== baseline
  const guard = useUnsavedChangesGuard(
    dirty,
    isNew ? '新的技能还没有创建，离开后填写的内容会丢失。' : `「${skill.title}」有未保存的更改，离开后这些更改会丢失。`,
  )

  const change = (patch: Partial<SkillForm>) => {
    setForm((current) => ({ ...current, ...patch }))
    setFieldErrors((current) => {
      const next = { ...current }
      for (const key of Object.keys(patch) as (keyof SkillForm)[]) {
        if (key === 'toolsText') delete next.tools
        else if (key === 'agents') delete next.agents
        else if (key === 'id' || key === 'title' || key === 'description' || key === 'body') delete next[key]
      }
      return next
    })
    setNotice(null)
  }

  const save = useMutation({
    mutationFn: (body: SkillWriteRequest) => saveSkill(isNew ? form.id.trim() : skill.id, body),
    onSuccess: async (saved) => {
      setBaseline(skillSnapshot(form))
      await invalidateSkills(queryClient, saved.id)
      toast.success('已保存技能')
      onLeave(SKILLS_PATH)
    },
    onError: (error) => {
      const serverErrors = skillServerFieldErrors(error)
      setFieldErrors(serverErrors)
      setNotice(Object.keys(serverErrors).length > 0 ? '技能内容不合法，请按字段提示修改。' : describeSkillError(error))
    },
  })

  const remove = useMutation({
    mutationFn: () => deleteSkill(skill!.id),
    onSuccess: async () => {
      await invalidateSkills(queryClient, skill?.id)
      // 离开前清掉未保存标记，避免删除 / 恢复默认成功后被离开确认框拦住。
      setBaseline(skillSnapshot(form))
      toast.success(builtin ? '已恢复默认' : '已删除技能')
      onLeave(SKILLS_PATH)
    },
    onError: (error) => setNotice(describeSkillError(error)),
  })

  const busy = save.isPending || remove.isPending

  const submit = () => {
    if (busy) return
    if (Object.keys(errors).length > 0) {
      setFieldErrors({})
      setNotice('还有字段没填好或超出字数，请按字段提示修改。')
      return
    }
    setFieldErrors({})
    setNotice(null)
    save.mutate(skillWriteFromForm(form))
  }

  const restore = async () => {
    if (!skill) return
    const ok = await confirm({
      title: '恢复默认？',
      description: '你的修改会被丢弃，换回内置内容。',
      confirmText: '恢复默认',
    })
    if (ok) remove.mutate()
  }

  const destroy = async () => {
    if (!skill) return
    const ok = await confirm({
      title: '删除技能？',
      description: '删除后 Agent 不能再加载它，无法撤销。',
      confirmText: '删除技能',
      tone: 'danger',
    })
    if (ok) remove.mutate()
  }

  const toggleAgent = (name: string) => {
    change({
      agents: form.agents.includes(name)
        ? form.agents.filter((agent) => agent !== name)
        : [...form.agents, name],
    })
  }

  return (
    <Page title={isNew ? '新建技能' : '编辑技能'} back={SKILLS_PATH} width="narrow">
      <div className="space-y-5 pb-2">
        {notice ? <InlineAlert tone="danger" title="没有保存">{notice}</InlineAlert> : null}

        <Field
          label="id"
          htmlFor="skill-id"
          description="小写字母、数字与连字符，2–48 个字符"
          error={fieldErrors.id ?? errors.id}
          errorId="skill-id-error"
        >
          <Input
            id="skill-id"
            className="font-mono"
            value={form.id}
            readOnly={!isNew}
            disabled={busy}
            placeholder="例如：playbook-recruit"
            aria-invalid={fieldErrors.id ?? errors.id ? true : undefined}
            aria-describedby={fieldErrors.id ?? errors.id ? 'skill-id-error' : undefined}
            onChange={(event) => change({ id: event.target.value })}
            {...TEXT_INPUT}
          />
        </Field>

        <Field
          label="标题"
          htmlFor="skill-title"
          error={fieldErrors.title ?? errors.title}
          errorId="skill-title-error"
        >
          <Input
            id="skill-title"
            value={form.title}
            maxLength={MAX_TITLE_CHARS}
            disabled={busy}
            placeholder="例如：公开招募实操"
            aria-invalid={fieldErrors.title ?? errors.title ? true : undefined}
            aria-describedby={fieldErrors.title ?? errors.title ? 'skill-title-error' : undefined}
            onChange={(event) => change({ title: event.target.value })}
            {...TEXT_INPUT}
          />
        </Field>

        <Field
          label="什么时候用"
          htmlFor="skill-description"
          description="用户提到什么时该加载这个技能"
          error={fieldErrors.description ?? errors.description}
          errorId="skill-description-error"
        >
          <Input
            id="skill-description"
            value={form.description}
            maxLength={MAX_DESCRIPTION_CHARS}
            disabled={busy}
            placeholder="例如：用户要你自动公开招募时"
            aria-invalid={fieldErrors.description ?? errors.description ? true : undefined}
            aria-describedby={fieldErrors.description ?? errors.description ? 'skill-description-error' : undefined}
            onChange={(event) => change({ description: event.target.value })}
            {...TEXT_INPUT}
          />
        </Field>

        <Field
          label="可用代理"
          description="全不选表示所有代理"
          error={fieldErrors.agents}
          errorId="skill-agents-error"
        >
          <div role="group" aria-label="可用代理" className="space-y-1.5">
            {specs.isPending ? <p className="text-footnote text-muted-foreground">正在读取代理定义…</p> : null}
            {agentOptions.map((spec) => (
              <label
                key={spec.name}
                className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border bg-surface px-3 py-2 transition-colors hover:bg-surface-2"
              >
                <input
                  type="checkbox"
                  className="size-4 shrink-0 accent-primary"
                  checked={form.agents.includes(spec.name)}
                  disabled={busy}
                  onChange={() => toggleAgent(spec.name)}
                />
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{specTitle(spec)}</span>
                <code className="shrink-0 font-mono text-footnote text-muted-foreground">{spec.name}</code>
              </label>
            ))}
          </div>
        </Field>

        <Field
          label="依赖的工具"
          htmlFor="skill-tools"
          description="每行一个工具名"
          error={fieldErrors.tools}
          errorId="skill-tools-error"
        >
          <Textarea
            id="skill-tools"
            className="min-h-24 font-mono"
            value={form.toolsText}
            disabled={busy}
            placeholder={'例如：\nsubmit_pipeline\nwait_pipeline'}
            aria-invalid={fieldErrors.tools ? true : undefined}
            aria-describedby={fieldErrors.tools ? 'skill-tools-error' : undefined}
            onChange={(event) => change({ toolsText: event.target.value })}
          />
        </Field>

        <Field
          label="正文"
          htmlFor="skill-body"
          labelAside={<BodyCounter value={form.body} />}
          description="Markdown"
          error={fieldErrors.body ?? errors.body}
          errorId="skill-body-error"
        >
          <Textarea
            id="skill-body"
            className="min-h-60 font-mono"
            value={form.body}
            maxLength={MAX_BODY_CHARS}
            disabled={busy}
            aria-invalid={fieldErrors.body ?? errors.body ? true : undefined}
            aria-describedby={fieldErrors.body ?? errors.body ? 'skill-body-error' : undefined}
            onChange={(event) => change({ body: event.target.value })}
          />
        </Field>

        {skill?.builtin ? (
          <Disclosure title="内置原文" description="随服务发布的原始正文，用来对照你的修改。">
            <pre className="max-h-[60dvh] overflow-auto overscroll-contain whitespace-pre-wrap break-words rounded-lg bg-surface-2 px-3 py-2.5 font-mono text-footnote leading-6">
              {skill.builtin.body}
            </pre>
          </Disclosure>
        ) : null}

        <div className={`${card} px-4 py-1`}>
          <SwitchRow
            id="skill-enabled"
            label="启用"
            description="停用后 Agent 看不到这个技能"
            checked={form.enabled}
            disabled={busy}
            onCheckedChange={(enabled) => change({ enabled })}
          />
        </div>
      </div>

      <StickyActionBar summary={dirty ? '有未保存的更改' : undefined}>
        {skill && builtin && skill.modified ? (
          <Button variant="outline" disabled={busy} onClick={() => void restore()}>
            <RotateCcw aria-hidden="true" />恢复默认
          </Button>
        ) : null}
        {skill && !builtin ? (
          <Button variant="destructive-soft" disabled={busy} onClick={() => void destroy()}>
            <Trash2 aria-hidden="true" />删除技能
          </Button>
        ) : null}
        <Button loading={save.isPending} disabled={busy} onClick={submit}>保存</Button>
      </StickyActionBar>
      {confirmElement}
      {guard}
    </Page>
  )
}

/** 新建（`skillId` 为 null）或编辑技能的整页编辑器；换一条时整页重挂，状态不串。 */
export function SkillEditorScreen({ skillId }: { skillId: string | null }) {
  const [leaving, setLeaving] = useState<string | null>(null)
  const detail = useQuery({ ...skillDetailQuery(skillId ?? ''), enabled: skillId !== null })

  if (leaving) return <Navigate to={leaving} replace />
  if (skillId === null) return <SkillEditor skill={null} onLeave={setLeaving} />
  if (detail.data) return <SkillEditor key={detail.data.id} skill={detail.data} onLeave={setLeaving} />

  let body: ReactNode
  if (detail.isError) {
    const failure = toApiError(detail.error)
    const missing = failure.status === 404 || failure.code === 'SKILL_NOT_FOUND'
    body = (
      <div className={card}>
        {missing ? (
          <EmptyState
            icon={BookX}
            title="找不到这个技能"
            description="技能可能已经被删除"
            action={<Button asChild variant="outline"><Link to={SKILLS_PATH}>返回技能</Link></Button>}
          />
        ) : (
          <ErrorState
            title="技能读取失败"
            message={describeSkillError(detail.error)}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        )}
      </div>
    )
  } else {
    body = <div className={card}><LoadingState label="正在读取技能…" /></div>
  }
  return <Page title="编辑技能" back={SKILLS_PATH} width="narrow">{body}</Page>
}
