import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup } from '@/components/ui/list'
import { NativeSelect } from '@/components/ui/native-select'
import { Section } from '@/components/ui/section'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { errorText } from '@/features/settings/format'
import { modelProfilesQueryOptions, modelRolesQueryOptions, useUpdateRole, type ModelRole } from './api'
import { roleLabel } from './labels'

const HINT = '每个角色用哪个档案。没选的用默认档案，也就是「LLM 与 Agent」里的设置。'

function roleKind(role: string): 'chat' | 'embedding' {
  return role === 'embedding' ? 'embedding' : 'chat'
}

/** 角色当前选中的档案名；聊天角色回落到 default 时对应空串（第一项）。 */
function selectedValue(role: ModelRole): string {
  if (role.profile === null || role.profile === 'default') return ''
  return role.profile
}

/** 「模型档案」分区上半部分：每个角色用一个下拉框选择档案。 */
export function ModelRolesCard() {
  const roles = useQuery(modelRolesQueryOptions)
  const profiles = useQuery(modelProfilesQueryOptions)
  const update = useUpdateRole()
  const [error, setError] = useState<string | null>(null)
  // 保存期间保留用户的这次选择，别让服务端数据回来前下拉框又跳回旧值。
  const [selected, setSelected] = useState<Record<string, string>>({})

  const failure = roles.error ?? profiles.error
  const profileItems = profiles.data ?? []

  const choose = (role: ModelRole, profile: string) => {
    setSelected((current) => ({ ...current, [role.role]: profile }))
    setError(null)
    update.mutate({ role: role.role, profile }, {
      onSuccess: () => toast.success('已更新角色'),
      onError: (caught) => {
        setSelected((current) => {
          const next = { ...current }
          delete next[role.role]
          return next
        })
        setError(`更新角色失败：${errorText(caught)}`)
      },
    })
  }

  let content
  if (roles.isPending || profiles.isPending) {
    content = <LoadingState label="正在读取角色…" />
  } else if (failure) {
    content = (
      <ErrorState
        title="角色读取失败"
        message={errorText(failure)}
        onRetry={() => {
          void roles.refetch()
          void profiles.refetch()
        }}
        retrying={roles.isFetching || profiles.isFetching}
      />
    )
  } else {
    content = (
      <ListGroup>
        {(roles.data ?? []).map((role) => {
          const id = `model-role-${role.role}`
          const pick = selected[role.role] ?? selectedValue(role)
          const options = profileItems.filter((profile) =>
            profile.enabled && profile.name !== 'default' && profile.kind === roleKind(role.role),
          )
          return (
            <div key={role.role} className="flex items-center gap-3 px-4 py-2.5">
              <label htmlFor={id} className="w-20 shrink-0 text-[0.9375rem] font-medium">
                {roleLabel(role.role)}
              </label>
              <NativeSelect
                id={id}
                value={pick}
                disabled={update.isPending}
                onChange={(event) => choose(role, event.target.value)}
              >
                <option value="">{role.role === 'embedding' ? '不使用' : '默认（LLM 设置）'}</option>
                {options.map((profile) => (
                  <option key={profile.name} value={profile.name}>{profile.name}</option>
                ))}
              </NativeSelect>
            </div>
          )
        })}
      </ListGroup>
    )
  }

  return (
    <Section title="角色" hint={HINT}>
      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
      {content}
    </Section>
  )
}
