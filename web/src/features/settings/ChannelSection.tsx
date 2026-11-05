import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Link2, MessagesSquare, Unlink } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import type { components } from '@/types/api'
import { errorText, formatTime } from './format'

type Channel = components['schemas']['ChannelOut']
type Binding = components['schemas']['BindingOut']
type Platform = components['schemas']['ChannelPlatform']
type BindCode = components['schemas']['BindCodeOut']
type BindCodeResult = BindCode & { platform: Platform }

//: 顺序与 spec §4.6「四个平台各一块」一致；Telegram 用 httpx，不需要 extra。
const PLATFORMS: readonly { id: Platform; label: string; hint: string }[] = [
  { id: 'telegram', label: 'Telegram', hint: 'Bot Token；长轮询接收，不需要额外 SDK。' },
  { id: 'feishu', label: '飞书', hint: 'App ID 与应用密钥；长连接接收。' },
  { id: 'dingtalk', label: '钉钉', hint: 'Client ID 与客户端密钥；Stream 长连接接收。' },
  { id: 'qq', label: 'QQ', hint: 'AppID 与机器人密钥；官方机器人网关接收。' },
]

const PLATFORM_LABELS: Record<Platform, string> = {
  telegram: 'Telegram',
  feishu: '飞书',
  dingtalk: '钉钉',
  qq: 'QQ',
}

async function fetchChannels(): Promise<Channel[]> {
  const { data, error, response } = await api.GET('/api/agent/channels')
  if (error) throw toApiError(error, response)
  return data ?? []
}

async function fetchBindings(): Promise<Binding[]> {
  const { data, error, response } = await api.GET('/api/agent/channels/bindings')
  if (error) throw toApiError(error, response)
  return data?.items ?? []
}

type Status = { label: string; tone: 'neutral' | 'success' | 'warning' }

/** 平台状态的短文案：没启用、缺凭证、未连接分开说，不把连不上的平台写成已联通。 */
function statusOf(channel: Channel | undefined): Status {
  if (!channel) return { label: '读取中…', tone: 'neutral' }
  if (!channel.enabled) return { label: '已停用', tone: 'neutral' }
  if (!channel.configured) return { label: '已启用，缺少凭证', tone: 'warning' }
  if (channel.connected === false) return { label: '已启用，未连接', tone: 'warning' }
  if (channel.connected === true) return { label: '已启用，已连接', tone: 'success' }
  return { label: '已启用', tone: 'success' }
}

function bindingName(binding: Binding): string {
  return binding.display_name?.trim() || binding.external_user_id
}

/** IM 通道（M85 §4.6）：平台状态、生成绑定码与已绑定列表；密钥字段由设置 schema 渲染。 */
export function ChannelSection() {
  const queryClient = useQueryClient()
  const channels = useQuery({ queryKey: keys.channels.list(), queryFn: fetchChannels })
  const bindings = useQuery({ queryKey: keys.channels.bindings(), queryFn: fetchBindings })
  const [codes, setCodes] = useState<Partial<Record<Platform, BindCodeResult>>>({})
  const [confirm, confirmElement] = useConfirm()

  const channelFor = (platform: Platform): Channel | undefined =>
    channels.data?.find((item) => item.platform === platform)

  const bindCode = useMutation({
    mutationFn: async (platform: Platform): Promise<BindCodeResult> => {
      const { data, error, response } = await api.POST('/api/agent/channels/{platform}/bind-codes', {
        params: { path: { platform } },
      })
      if (error) throw toApiError(error, response)
      if (!data) throw new Error('服务没有返回绑定码')
      return { ...data, platform }
    },
    onSuccess: (result) => {
      setCodes((current) => ({ ...current, [result.platform]: result }))
      toast.success(`已生成 ${PLATFORM_LABELS[result.platform]} 绑定码`)
    },
  })

  const revoke = useMutation({
    mutationFn: async (binding: Binding) => {
      const { error, response } = await api.DELETE('/api/agent/channels/bindings/{binding_id}', {
        params: { path: { binding_id: binding.id } },
      })
      if (error) throw toApiError(error, response)
      return binding
    },
    onSuccess: async (binding) => {
      toast.success(`已解除 ${bindingName(binding)} 的绑定`)
      await queryClient.invalidateQueries({ queryKey: keys.channels.bindings() })
    },
  })

  const remove = async (binding: Binding) => {
    const ok = await confirm({
      title: `解除「${bindingName(binding)}」的绑定？`,
      description: '解除后，这个身份从对应平台发来的消息不再进入总控；绑定可以重新生成绑定码建立。',
      confirmText: '解除绑定',
      tone: 'danger',
    })
    if (ok) revoke.mutate(binding)
  }

  const platformList = channels.isPending ? (
    <LoadingState label="正在读取 IM 通道…" />
  ) : channels.isError ? (
    <div className="rounded-xl border bg-card shadow-card">
      <ErrorState
        title="IM 通道读取失败"
        message={errorText(channels.error)}
        onRetry={() => void channels.refetch()}
        retrying={channels.isFetching}
      />
    </div>
  ) : (
    <ul aria-label="IM 平台状态" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
      {PLATFORMS.map(({ id, label, hint }) => {
        const channel = channelFor(id)
        const status = statusOf(channel)
        const generated = codes[id]
        return (
          <li key={id} className="space-y-3 px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[0.9375rem] font-medium leading-5">{label}</span>
                  <Badge variant={status.tone === 'neutral' ? 'neutral' : status.tone === 'success' ? 'success' : 'warning'}>
                    {status.label}
                  </Badge>
                </div>
                <p className="mt-0.5 text-footnote text-muted-foreground">{hint}</p>
                {channel?.last_error ? (
                  <p className="mt-0.5 break-words text-footnote text-danger">上次错误：{channel.last_error}</p>
                ) : null}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                loading={bindCode.isPending && bindCode.variables === id}
                disabled={bindCode.isPending || revoke.isPending}
                onClick={() => bindCode.mutate(id)}
              >
                <KeyRound aria-hidden="true" />
                生成绑定码
              </Button>
            </div>
            {generated ? (
              <InlineAlert tone="success" title={`绑定码 ${generated.code}`}>
                10 分钟内有效，{formatTime(generated.expires_at) ?? '稍后'}过期；在 {label} 里发送{' '}
                <code className="font-mono">/bind {generated.code}</code>。
              </InlineAlert>
            ) : null}
          </li>
        )
      })}
    </ul>
  )

  let bindingList
  if (bindings.isPending) {
    bindingList = <LoadingState label="正在读取绑定…" />
  } else if (bindings.isError) {
    bindingList = (
      <div className="rounded-xl border bg-card shadow-card">
        <ErrorState
          title="绑定列表读取失败"
          message={errorText(bindings.error)}
          onRetry={() => void bindings.refetch()}
          retrying={bindings.isFetching}
        />
      </div>
    )
  } else if (bindings.data.length) {
    bindingList = (
      <ul aria-label="已绑定" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
        {bindings.data.map((binding) => {
          const busy = revoke.isPending && revoke.variables?.id === binding.id
          return (
            <li key={binding.id} className="flex min-h-14 items-center gap-3 px-4 py-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
                <Link2 aria-hidden="true" className="size-[1.125rem]" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.9375rem] font-medium leading-5">{bindingName(binding)}</span>
                <span className="mt-0.5 block truncate text-footnote text-muted-foreground">
                  {PLATFORM_LABELS[binding.platform]}
                  {binding.display_name ? ` · ${binding.external_user_id}` : ''}
                  {formatTime(binding.bound_at) ? ` · ${formatTime(binding.bound_at)} 绑定` : ''}
                </span>
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="text-danger"
                loading={busy}
                disabled={revoke.isPending}
                onClick={() => void remove(binding)}
              >
                <Unlink aria-hidden="true" />
                解除
              </Button>
            </li>
          )
        })}
      </ul>
    )
  } else {
    bindingList = (
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          icon={MessagesSquare}
          title="还没有绑定"
          description="生成绑定码后，在对应平台的聊天里发送 /bind <绑定码> 完成绑定"
        />
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <Section
        title="平台状态"
        hint="四个平台各自启停；凭证字段在下方的「IM 通道」设置里填写，回显为 ***，留空表示不修改。绑定码只在网页上生成，10 分钟内有效。"
      >
        {bindCode.error ? <InlineAlert tone="danger">生成绑定码失败：{errorText(bindCode.error)}</InlineAlert> : null}
        {revoke.error ? <InlineAlert tone="danger">解除绑定失败：{errorText(revoke.error)}</InlineAlert> : null}
        {platformList}
      </Section>
      <Section title="已绑定" hint="解除后这个身份的消息不再进入总控；绑定记录保留为已解除，不删除对话与消息。">
        {bindingList}
      </Section>
      {confirmElement}
    </div>
  )
}
