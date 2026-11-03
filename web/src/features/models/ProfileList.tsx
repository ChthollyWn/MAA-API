import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { errorText } from '@/features/settings/format'
import { modelProfilesQueryOptions, useDeleteProfile, useProbeProfile, type ModelProfile } from './api'
import { ProfileEditorSheet } from './ProfileEditorSheet'

function hostnameOf(profile: ModelProfile): string {
  if (!profile.base_url) return '未设置'
  try {
    return new URL(profile.base_url).hostname
  } catch {
    return profile.base_url
  }
}

function windowLabel(value: number | null): string {
  return value === null ? '窗口未设置' : `窗口 ${value.toLocaleString('zh-CN')}`
}

function CapabilityBadge({ label, value }: { label: string; value: boolean | null }) {
  const variant: BadgeProps['variant'] = value === null ? 'warning' : value ? 'success' : 'danger'
  return <Badge variant={variant}>{label}：{value === null ? '未检测' : value ? '可用' : '不可用'}</Badge>
}

/** 「模型档案」分区下半部分：档案列表、检测、编辑与删除。 */
export function ProfileList() {
  const profiles = useQuery(modelProfilesQueryOptions)
  const probe = useProbeProfile()
  const remove = useDeleteProfile()
  const [confirm, confirmElement] = useConfirm()
  const [error, setError] = useState<string | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [editing, setEditing] = useState<ModelProfile | null>(null)

  const items = profiles.data ?? []
  const custom = items.filter((profile) => !profile.readonly)

  const openEditor = (profile: ModelProfile | null) => {
    setEditing(profile)
    setSheetOpen(true)
  }

  const closeEditor = (open: boolean) => {
    setSheetOpen(open)
    if (!open) setEditing(null)
    setError(null)
  }

  const check = (profile: ModelProfile) => {
    setError(null)
    probe.mutate(profile.name, {
      onSuccess: () => toast.success('已完成检测'),
      onError: (caught) => setError(`检测能力失败：${errorText(caught)}`),
    })
  }

  const destroy = async (profile: ModelProfile) => {
    const ok = await confirm({
      title: `删除档案「${profile.name}」？`,
      description: '删除后无法撤销。',
      confirmText: '删除档案',
      tone: 'danger',
    })
    if (!ok) return
    setError(null)
    remove.mutate(profile.name, {
      onSuccess: () => toast.success('已删除档案'),
      onError: (caught) => setError(`删除档案失败：${errorText(caught)}`),
    })
  }

  let content
  if (profiles.isPending) {
    content = <LoadingState label="正在读取模型档案…" />
  } else if (profiles.isError) {
    content = (
      <ErrorState
        title="模型档案读取失败"
        message={errorText(profiles.error)}
        onRetry={() => void profiles.refetch()}
        retrying={profiles.isFetching}
      />
    )
  } else {
    content = (
      <div className="space-y-3">
        {items.length > 0 ? (
          <ListGroup>
            {items.map((profile) => (
              <div
                key={profile.name}
                role="group"
                aria-label={`档案 ${profile.name}`}
                className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:gap-3"
              >
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-[0.9375rem] font-medium">{profile.name}</span>
                    {profile.readonly ? <Badge variant="outline">只读</Badge> : null}
                    {!profile.enabled ? <Badge variant="neutral">已停用</Badge> : null}
                    {profile.readonly ? (
                      <InfoHint label="default 档案说明">由「LLM 与 Agent」里的设置生成，要改请到那里改。</InfoHint>
                    ) : null}
                  </div>
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-muted-foreground">
                    <span>{profile.model}</span>
                    <span aria-hidden="true">·</span>
                    <span>{hostnameOf(profile)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{windowLabel(profile.context_window)}</span>
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    <CapabilityBadge label="工具调用" value={profile.supports_tools} />
                    <CapabilityBadge label="视觉" value={profile.supports_vision} />
                    <CapabilityBadge label="JSON Schema" value={profile.supports_json_schema} />
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={probe.isPending && probe.variables === profile.name}
                    disabled={remove.isPending}
                    onClick={() => check(profile)}
                  >
                    检测能力
                  </Button>
                  {profile.readonly ? null : (
                    <>
                      <Button variant="ghost" size="sm" disabled={probe.isPending || remove.isPending} onClick={() => openEditor(profile)}>
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={remove.isPending && remove.variables === profile.name}
                        disabled={probe.isPending || remove.isPending}
                        onClick={() => void destroy(profile)}
                      >
                        删除
                      </Button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </ListGroup>
        ) : null}
        {custom.length === 0 ? (
          <div className="rounded-xl border bg-card shadow-card">
            <EmptyState title="还没有自建档案" description="新建档案后可以把角色指给它" />
          </div>
        ) : null}
      </div>
    )
  }

  return (
    <>
      <Section
        title="档案"
        actions={(
          <Button variant="soft" size="sm" onClick={() => openEditor(null)}>
            <Plus aria-hidden="true" />
            新建档案
          </Button>
        )}
      >
        {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
        {content}
      </Section>
      {confirmElement}
      {sheetOpen ? (
        <ProfileEditorSheet open={sheetOpen} profile={editing} onOpenChange={closeEditor} />
      ) : null}
    </>
  )
}
