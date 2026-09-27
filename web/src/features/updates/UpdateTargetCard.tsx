import { useId, type ReactNode } from 'react'
import { Cpu, Gamepad2, Package, type LucideIcon } from 'lucide-react'
import { Badge, StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { VersionChange } from '@/features/updates/VersionChange'
import type { GameChannel, UpdateChannelState, UpdateTarget, UpdateTargetState } from '@/features/updates/types'
import {
  actionLabel, asText, availabilityStatus, canTrigger, formatBytes, formatDate, initialSelection,
  selectionAvailability, selectionError, targetLabels,
} from '@/features/updates/types'

const copy: Record<UpdateTarget, { description: string; icon: LucideIcon }> = {
  core: { description: '只使用 stable 通道，更新时会重启内核。', icon: Cpu },
  resource: { description: 'OTA 与资源仓库两条通道，可以分别更新。', icon: Package },
  game: { description: '下载对应渠道的安装包，通过 ADB 覆盖安装。', icon: Gamepad2 },
}

const gameChannelOptions = [
  { value: 'Official', label: '官服' },
  { value: 'Bilibili', label: 'B 服' },
] as const satisfies readonly { value: GameChannel; label: string }[]

export interface UpdateTargetCardProps {
  target: UpdateTarget
  state?: UpdateTargetState
  gameChannel: GameChannel
  onGameChannelChange: (channel: GameChannel) => void
  /** 这个目标正在更新。 */
  updating: boolean
  /** 更新队列被占用：三个目标共用一条队列，有更新在进行时都不能再开始。 */
  locked: boolean
  onAction: () => void
}

function Meta({ items }: { items: (string | null | undefined)[] }) {
  const shown = items.filter((item): item is string => Boolean(item))
  return shown.length ? <p className="text-footnote text-muted-foreground tabular-nums">{shown.join(' · ')}</p> : null
}

function ChannelRow({ name, channel, children }: { name: string; channel?: UpdateChannelState; children?: ReactNode }) {
  const status = availabilityStatus(channel?.available, channel?.error)
  return (
    <div className="space-y-1 py-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{name}</h3>
        <Badge variant={status.tone}>{status.label}</Badge>
      </div>
      {children}
      {channel?.error ? <p className="break-words text-footnote text-danger">{name}检查失败：{channel.error}</p> : null}
    </div>
  )
}

function CoreBody({ state }: { state?: UpdateTargetState }) {
  return (
    <div className="space-y-1 py-3">
      <VersionChange
        current={asText(state?.current)}
        latest={asText(state?.latest)}
        unknownLabel={state ? '未知（内核未就绪）' : '未知'}
        className="text-[0.9375rem]"
      />
      {state?.error ? <p className="break-words text-footnote text-danger">内核检查失败：{state.error}</p> : null}
    </div>
  )
}

function ResourceBody({ state }: { state?: UpdateTargetState }) {
  const repo = state?.channels?.repo
  const ota = state?.channels?.ota
  return (
    <div className="divide-y">
      <ChannelRow name="资源仓库" channel={repo}>
        <VersionChange current={asText(repo?.current)} latest={asText(repo?.latest)} />
        <Meta items={[repo?.last_checked_at ? `上次检查 ${formatDate(repo.last_checked_at)}` : null]} />
      </ChannelRow>
      <ChannelRow name="OTA 资源" channel={ota}>
        <Meta
          items={[
            ota?.last_synced_at ? `上次同步 ${formatDate(ota.last_synced_at)}` : '尚未同步',
            ota?.last_checked_at ? `上次检查 ${formatDate(ota.last_checked_at)}` : null,
          ]}
        />
        <p className="text-footnote text-muted-foreground">按远端校验值判断是否有更新，不显示版本号。</p>
      </ChannelRow>
      {state ? (
        <p className="flex items-center gap-2 py-3 text-sm">
          <StatusDot tone={state.reload_pending ? 'warning' : 'success'} />
          {state.reload_pending ? '资源已下载，等待重载后生效' : '资源已生效'}
        </p>
      ) : null}
      {state?.error && !state.channels ? <p className="break-words py-3 text-footnote text-danger">资源检查失败：{state.error}</p> : null}
    </div>
  )
}

function GameBody({ state, channel, onChannelChange }: { state?: UpdateTargetState; channel: GameChannel; onChannelChange: (channel: GameChannel) => void }) {
  const selected = state?.channels?.[channel]
  return (
    <div className="space-y-2.5 py-3">
      <SegmentedControl ariaLabel="游戏渠道" size="sm" fullWidth value={channel} onValueChange={onChannelChange} options={gameChannelOptions} />
      <div className="space-y-1">
        <VersionChange current={asText(selected?.current)} latest={channel === 'Bilibili' ? asText(selected?.latest) : null} />
        <Meta
          items={[
            selected?.remote_size != null ? `安装包 ${formatBytes(selected.remote_size)}` : null,
            selected?.last_checked_at ? `上次检查 ${formatDate(selected.last_checked_at)}` : null,
          ]}
        />
        {channel === 'Official' ? <p className="text-footnote text-muted-foreground">官服未提供版本号接口，按远端安装包信息判断。</p> : null}
        {selected?.error ? <p className="break-words text-footnote text-danger">该渠道检查失败：{selected.error}</p> : null}
        {!selected && state?.error ? <p className="break-words text-footnote text-danger">{state.error}</p> : null}
      </div>
    </div>
  )
}

/** 一个更新目标：名称、版本、状态与唯一的主操作；选项在点主操作后的面板里选。 */
export function UpdateTargetCard({ target, state, gameChannel, onGameChannelChange, updating, locked, onAction }: UpdateTargetCardProps) {
  const headingId = useId()
  const { description, icon: Icon } = copy[target]
  const selection = initialSelection(target, state, gameChannel)
  const available = target === 'game' ? selectionAvailability(target, state, selection) : state?.available
  const status = availabilityStatus(available, target === 'game' ? selectionError(target, state, selection) : state?.error)
  const enabled = !updating && !locked && canTrigger(target, state, selection)

  return (
    <article aria-labelledby={headingId} className="flex min-w-0 flex-col rounded-xl border bg-card shadow-card">
      <header className="flex items-start gap-3 px-4 pt-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary-text">
          <Icon aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={headingId} className="text-headline font-semibold leading-6">{targetLabels[target]}</h2>
          <p className="mt-0.5 text-footnote text-muted-foreground">{description}</p>
        </div>
        <Badge variant={updating ? 'primary' : status.tone}>{updating ? '更新中' : status.label}</Badge>
      </header>
      <div className="px-4 pt-1">
        {target === 'core' ? <CoreBody state={state} /> : null}
        {target === 'resource' ? <ResourceBody state={state} /> : null}
        {target === 'game' ? <GameBody state={state} channel={gameChannel} onChannelChange={onGameChannelChange} /> : null}
      </div>
      <div className="mt-auto border-t px-4 py-3">
        <Button className="w-full" variant={!enabled ? 'secondary' : available === false ? 'outline' : 'default'} disabled={!enabled} onClick={onAction}>
          {updating ? '更新进行中' : actionLabel(target, state, selection)}
        </Button>
      </div>
    </article>
  )
}
