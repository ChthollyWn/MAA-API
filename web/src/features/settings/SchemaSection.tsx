import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Inbox, RotateCcw } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup, ListItem } from '@/components/ui/list'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { StickyActionBar } from '@/components/ui/sticky-action-bar'
import { toast } from '@/components/ui/toaster'
import { errorText } from './format'
import { asRecord, fieldDomId, initialInputValue, isFieldChanged, parseFieldValue, type SettingState } from './schema'
import { SETTINGS_INDEX_PATH, fieldsForSection, groupFields, sectionPath, type SettingsSection } from './sections'
import { SettingFieldRow } from './SettingFieldRow'
import { settingsQueryOptions } from './use-settings'
import { taskTypesKey } from '@/features/tasks'
import { useUnsavedChangesGuard } from '@/lib/use-unsaved-changes'

type Action = 'save' | 'reset'

type Notice =
  | { kind: 'apply-failed'; action: Action; message: string }
  | { kind: 'error'; action: Action; message: string }

const NO_VALUES: Record<string, SettingState> = {}

function isApplyFailure(error: unknown): boolean {
  const failure = toApiError(error)
  return failure.code === 'SETTING_APPLY_FAILED' && asRecord(failure.details).applied === true
}

function SectionNotice({ notice, sectionId }: { notice: Notice; sectionId: SettingsSection['id'] }) {
  if (notice.kind === 'apply-failed') {
    // 只有设备相关的设置会触发热生效；已经在设备分区时引导去首页看设备状态与重连。
    const guide = sectionId === 'device' ? { to: '/', label: '查看设备状态' } : { to: sectionPath('device'), label: '前往设备设置' }
    return (
      <InlineAlert
        tone="warning"
        title={notice.action === 'reset' ? '已恢复默认但生效失败' : '已保存但生效失败'}
        action={<Button asChild variant="outline" size="sm"><Link to={guide.to}>{guide.label}</Link></Button>}
      >
        配置已保留，请检查设备连接或相关进程。
        {notice.message ? <span className="mt-0.5 block text-footnote">{notice.message}</span> : null}
      </InlineAlert>
    )
  }
  return (
    <InlineAlert tone="danger" title={notice.action === 'reset' ? '恢复默认失败' : '保存失败'}>
      {notice.message}
    </InlineAlert>
  )
}

export interface SchemaSectionProps {
  section: SettingsSection
  /** 分区顶部、字段之前的内容，例如 Agent 分区的「检查模型能力」。 */
  intro?: ReactNode
}

/** 由服务端 schema 驱动的设置分区：按原 group 分小节，独立保存与恢复默认，未保存时拦截离开。 */
export function SchemaSection({ section, intro }: SchemaSectionProps) {
  const queryClient = useQueryClient()
  const settings = useQuery(settingsQueryOptions)
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState<Action | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [confirm, confirmElement] = useConfirm()
  const noticeRef = useRef<HTMLDivElement>(null)

  const fields = useMemo(() => fieldsForSection(settings.data?.fields ?? [], section.id), [settings.data, section.id])
  const groups = useMemo(() => groupFields(fields), [fields])
  const values = settings.data?.values ?? NO_VALUES
  const changed = fields.filter((field) => isFieldChanged(field, draft[field.key], values[field.key]))
  const dirty = changed.length > 0
  const resettable = fields.filter((field) => !field.readonly)
  const overridden = resettable.filter((field) => values[field.key]?.source === 'db')
  const guard = useUnsavedChangesGuard(dirty, `「${section.title}」有 ${changed.length} 项更改还没有保存，离开后这些更改会丢失。`)

  useEffect(() => {
    if (notice) noticeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [notice])

  // 渠道默认值会 overlay 进任务类型 schema（client_type / server 的 default），保存或恢复默认后必须失效它，
  // 否则 5 分钟 staleTime 内新建的任务会预填旧渠道。
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.settings.all() }),
      queryClient.invalidateQueries({ queryKey: taskTypesKey }),
    ])
  }

  const change = (key: string, value: unknown) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setErrors((current) => {
      if (!(key in current)) return current
      const next = { ...current }
      delete next[key]
      return next
    })
    setNotice(null)
  }

  const discard = () => {
    setDraft({})
    setErrors({})
    setNotice(null)
  }

  const save = async (event?: FormEvent) => {
    event?.preventDefault()
    if (pending) return
    const items: Record<string, unknown> = {}
    const nextErrors: Record<string, string> = {}
    // 留空的敏感项不算改动（见 isFieldChanged），因此不会提交，服务端保留原值。
    for (const field of changed) {
      const parsed = parseFieldValue(field, draft[field.key])
      if (parsed.error !== undefined) nextErrors[field.key] = parsed.error
      else items[field.key] = parsed.value
    }
    setErrors(nextErrors)
    const firstInvalid = Object.keys(nextErrors)[0]
    if (firstInvalid) {
      document.getElementById(fieldDomId(firstInvalid))?.focus()
      return
    }
    const count = Object.keys(items).length
    if (!count) {
      discard()
      return
    }
    setPending('save')
    setNotice(null)
    try {
      const { error, response } = await api.PUT('/api/settings', { body: { items } })
      if (error) throw toApiError(error, response)
      await refresh()
      setDraft({})
      toast.success(count > 1 ? `已保存 ${count} 项设置` : '已保存')
    } catch (caught) {
      if (isApplyFailure(caught)) {
        await refresh()
        setDraft({})
        setNotice({ kind: 'apply-failed', action: 'save', message: toApiError(caught).message })
      } else {
        const key = asRecord(toApiError(caught).details).key
        if (typeof key === 'string' && key in items) setErrors({ [key]: toApiError(caught).message })
        setNotice({ kind: 'error', action: 'save', message: errorText(caught) })
      }
    } finally {
      setPending(null)
    }
  }

  const reset = async () => {
    const effect = overridden.length
      ? `会清除 ${overridden.length} 项在网页上保存的值，改用配置文件、环境变量或默认值。`
      : '这个分区没有在网页上改过的值，恢复默认不会改变当前设置。'
    const ok = await confirm({
      title: `恢复「${section.title}」的默认设置？`,
      description: dirty ? `${effect}还没保存的更改也会一并放弃。` : effect,
      confirmText: '恢复默认',
      tone: 'danger',
    })
    if (!ok) return
    setPending('reset')
    setNotice(null)
    try {
      const { error, response } = await api.POST('/api/settings/reset', { body: { keys: resettable.map((field) => field.key) } })
      if (error) throw toApiError(error, response)
      await refresh()
      discard()
      toast.success(`「${section.title}」已恢复默认`)
    } catch (caught) {
      if (isApplyFailure(caught)) {
        await refresh()
        setDraft({})
        setErrors({})
        setNotice({ kind: 'apply-failed', action: 'reset', message: toApiError(caught).message })
      } else {
        setNotice({ kind: 'error', action: 'reset', message: errorText(caught) })
      }
    } finally {
      setPending(null)
    }
  }

  let body: ReactNode
  if (settings.isPending) {
    body = <LoadingState label="正在读取设置…" />
  } else if (!settings.data) {
    body = (
      <div className="rounded-xl border bg-card shadow-card">
        <ErrorState title="设置读取失败" message={errorText(settings.error)} onRetry={() => void settings.refetch()} retrying={settings.isFetching} />
      </div>
    )
  } else if (!fields.length) {
    body = (
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          icon={Inbox}
          title="这个分区没有设置项"
          description="暂时没有可以修改的设置"
          action={<Button asChild variant="outline" size="sm"><Link to={SETTINGS_INDEX_PATH}>返回设置</Link></Button>}
        />
      </div>
    )
  } else {
    body = (
      <>
        {groups.map(({ group, fields: groupItems }) => (
          <ListGroup key={group} title={group}>
            {groupItems.map((field) => (
              <SettingFieldRow
                key={field.key}
                field={field}
                state={values[field.key]}
                value={draft[field.key] ?? initialInputValue(field, values[field.key])}
                changed={changed.includes(field)}
                error={errors[field.key]}
                disabled={pending !== null}
                onChange={(value) => change(field.key, value)}
              />
            ))}
          </ListGroup>
        ))}
        {resettable.length ? (
          <ListGroup footer="只清除在网页上保存的值，配置文件与环境变量不受影响">
            <ListItem
              icon={RotateCcw}
              iconTone="danger"
              tone="danger"
              title={pending === 'reset' ? '正在恢复默认…' : '恢复默认'}
              description={overridden.length ? `有 ${overridden.length} 项在网页上改过` : '没有在网页上改过的值'}
              chevron={false}
              disabled={pending !== null}
              onClick={() => void reset()}
            />
          </ListGroup>
        ) : null}
      </>
    )
  }

  return (
    <Page title={section.title} back={SETTINGS_INDEX_PATH} width="narrow">
      <form noValidate onSubmit={(event) => void save(event)} className="space-y-6 pb-4">
        {intro}
        {settings.data && settings.isError ? (
          <InlineAlert
            tone="warning"
            action={<Button type="button" variant="outline" size="sm" loading={settings.isFetching} onClick={() => void settings.refetch()}>重试</Button>}
          >
            没能刷新设置，下面显示的是上次读取的值：{errorText(settings.error)}
          </InlineAlert>
        ) : null}
        {notice ? <div ref={noticeRef}><SectionNotice notice={notice} sectionId={section.id} /></div> : null}
        {body}
        {dirty ? (
          <StickyActionBar summary={`有 ${changed.length} 项未保存的更改`}>
            <Button type="button" variant="outline" disabled={pending !== null} onClick={discard}>放弃更改</Button>
            <Button type="submit" loading={pending === 'save'}>保存</Button>
          </StickyActionBar>
        ) : null}
      </form>
      {confirmElement}
      {guard}
    </Page>
  )
}
