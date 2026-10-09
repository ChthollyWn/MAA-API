import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BellRing, Bird, Mail, MessageSquare, MessagesSquare, Pencil, Plus, Send, Trash2, Webhook, type LucideIcon } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { ActionMenu } from '@/components/ui/action-sheet'
import { StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { Switch } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { ChannelEditor } from './ChannelEditor'
import { channelTypeLabel, normalizeChannels, type Channel, type ChannelType, type NotifyEventId } from './channel-config'
import { errorText, formatTime } from './format'
import { asRecord } from './schema'

export const CHANNELS_KEY = [...keys.all, 'notifications', 'channels'] as const

const TYPE_ICONS: Readonly<Record<string, LucideIcon>> = {
  email: Mail,
  webhook: Webhook,
  bark: BellRing,
  dingtalk: MessageSquare,
  wecom: MessagesSquare,
  feishu: Bird,
}

async function fetchChannels(): Promise<Channel[]> {
  const { data, error, response } = await api.GET('/api/notifications/channels')
  if (error) throw toApiError(error, response)
  return normalizeChannels(data)
}

/** 列表里的启停直接整条回写；配置里的 `***` 由服务端换回已保存的凭据。 */
function channelBody(channel: Channel, enabled: boolean) {
  return {
    type: channel.type as ChannelType,
    name: channel.name,
    config: channel.config,
    events: channel.events as NotifyEventId[],
    enabled,
  }
}

function LastDelivery({ channel }: { channel: Channel }) {
  if (!channel.lastStatus) return null
  const ok = channel.lastStatus === 'success'
  const time = formatTime(channel.lastSentAt)
  return (
    <span className="mt-0.5 flex items-center gap-1.5 text-footnote text-muted-foreground">
      <StatusDot tone={ok ? 'success' : 'danger'} />
      <span className="truncate">{ok ? '发送成功' : '发送失败'}{time ? ` · ${time}` : ''}</span>
    </span>
  )
}

interface EditorTarget {
  channel: Channel | null
  key: number
}

/** 通知通道：列表（名称、类型、启用、测试、编辑、删除）与按类型填写的编辑器。 */
export function NotificationChannels() {
  const queryClient = useQueryClient()
  const channels = useQuery({ queryKey: CHANNELS_KEY, queryFn: fetchChannels })
  const [editor, setEditor] = useState<EditorTarget | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, confirmElement] = useConfirm()

  const refresh = () => queryClient.invalidateQueries({ queryKey: CHANNELS_KEY })

  const openEditor = (channel: Channel | null) => {
    setError(null)
    setEditor({ channel, key: Date.now() })
    setEditorOpen(true)
  }

  const toggle = async (channel: Channel, enabled: boolean) => {
    setBusyId(channel.id)
    setError(null)
    queryClient.setQueryData<Channel[]>(CHANNELS_KEY, (list) => list?.map((item) => (item.id === channel.id ? { ...item, enabled } : item)))
    try {
      const { error: failure, response } = await api.PUT('/api/notifications/channels/{channel_id}', {
        params: { path: { channel_id: channel.id } },
        body: channelBody(channel, enabled),
      })
      if (failure) throw toApiError(failure, response)
      toast.success(enabled ? `已启用「${channel.name}」` : `已停用「${channel.name}」`)
    } catch (caught) {
      setError(`${enabled ? '启用' : '停用'}「${channel.name}」失败：${errorText(caught)}`)
    } finally {
      setBusyId(null)
      await refresh()
    }
  }

  const test = async (channel: Channel) => {
    setBusyId(channel.id)
    setError(null)
    try {
      const { error: failure, response } = await api.POST('/api/notifications/channels/{channel_id}/test', {
        params: { path: { channel_id: channel.id } },
        body: { event: 'pipeline_completed' },
      })
      if (failure) throw toApiError(failure, response)
      toast.success(`已向「${channel.name}」发送测试通知`)
    } catch (caught) {
      const upstream = asRecord(toApiError(caught).details).upstream
      setError(`「${channel.name}」发送测试通知失败：${typeof upstream === 'string' && upstream ? upstream : errorText(caught)}`)
    } finally {
      setBusyId(null)
      await refresh()
    }
  }

  const remove = async (channel: Channel) => {
    const ok = await confirm({
      title: `删除通知通道「${channel.name}」？`,
      description: '删除后不再向这个通道发送通知，且无法撤销。',
      confirmText: '删除通道',
      tone: 'danger',
    })
    if (!ok) return
    setBusyId(channel.id)
    setError(null)
    try {
      const { error: failure, response } = await api.DELETE('/api/notifications/channels/{channel_id}', {
        params: { path: { channel_id: channel.id } },
      })
      if (failure) throw toApiError(failure, response)
      toast.success(`已删除「${channel.name}」`)
    } catch (caught) {
      setError(`删除「${channel.name}」失败：${errorText(caught)}`)
    } finally {
      setBusyId(null)
      await refresh()
    }
  }

  const list = channels.data ?? []

  return (
    <Section
      title="通知通道"
      hint="流水线完成、失败或发现更新时发送消息。"
      actions={list.length ? (
        <Button type="button" variant="soft" size="sm" onClick={() => openEditor(null)}>
          <Plus aria-hidden="true" />
          添加通道
        </Button>
      ) : null}
    >
      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
      {channels.isPending ? (
        <LoadingState label="正在读取通知通道…" />
      ) : channels.isError ? (
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState title="通知通道读取失败" message={errorText(channels.error)} onRetry={() => void channels.refetch()} retrying={channels.isFetching} />
        </div>
      ) : list.length ? (
        <ul aria-label="通知通道" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
          {list.map((channel) => {
            const Icon = TYPE_ICONS[channel.type] ?? Bell
            const busy = busyId === channel.id
            return (
              <li key={channel.id} className="flex items-center gap-1 pr-1.5">
                <button
                  type="button"
                  onClick={() => openEditor(channel)}
                  className="flex min-h-16 min-w-0 flex-1 items-center gap-3 py-3 pl-4 pr-1 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-text">
                    <Icon aria-hidden="true" className="size-[1.125rem]" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.9375rem] font-medium leading-5">{channel.name}</span>
                    <span className="mt-0.5 block truncate text-footnote text-muted-foreground">
                      {channelTypeLabel(channel.type)} · {channel.events.length ? `订阅 ${channel.events.length} 个事件` : '未订阅事件'}
                    </span>
                    <LastDelivery channel={channel} />
                  </span>
                </button>
                {busy ? <Spinner className="text-muted-foreground" /> : null}
                <Switch
                  aria-label={`启用「${channel.name}」`}
                  checked={channel.enabled}
                  disabled={busyId !== null}
                  onCheckedChange={(checked) => void toggle(channel, checked)}
                  className="mx-2"
                />
                <ActionMenu
                  label={`「${channel.name}」的更多操作`}
                  title={channel.name}
                  description={channelTypeLabel(channel.type)}
                  disabled={busyId !== null}
                  actions={[
                    { label: '发送测试通知', description: '发送一条「流水线完成」示例消息', icon: Send, onSelect: () => void test(channel) },
                    { label: '编辑', icon: Pencil, onSelect: () => openEditor(channel) },
                    { label: '删除', icon: Trash2, tone: 'danger', onSelect: () => void remove(channel) },
                  ]}
                />
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="rounded-xl border bg-card shadow-card">
          <EmptyState
            icon={Bell}
            title="还没有通知通道"
            description="添加一个通道，订阅需要通知的事件"
            action={(
              <Button type="button" onClick={() => openEditor(null)}>
                <Plus aria-hidden="true" />
                添加通道
              </Button>
            )}
          />
        </div>
      )}
      {editor ? (
        <ChannelEditor
          key={editor.key}
          open={editorOpen}
          channel={editor.channel}
          onOpenChange={setEditorOpen}
          onSaved={(name, created) => {
            toast.success(created ? `已添加「${name}」` : `已保存「${name}」`)
            void refresh()
          }}
        />
      ) : null}
      {confirmElement}
    </Section>
  )
}
