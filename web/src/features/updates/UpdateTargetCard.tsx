import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { UpdateProgress } from '@/features/updates/UpdateProgress'
import type { GameChannel, ResourceChannel, UpdateRecord, UpdateTarget, UpdateTargetState } from '@/features/updates/types'
import { asText, formatBytes, formatDate } from '@/features/updates/types'

interface UpdateTargetCardProps {
  target: UpdateTarget
  state?: UpdateTargetState
  activeRecord?: UpdateRecord | null
  resourceChannel?: ResourceChannel
  reloadMode?: 'wait' | 'force' | 'defer'
  gameChannel?: GameChannel
  onResourceChannelChange?: (channel: ResourceChannel) => void
  onReloadModeChange?: (mode: 'wait' | 'force' | 'defer') => void
  onGameChannelChange?: (channel: GameChannel) => void
  forceInterrupt: boolean
  busy: boolean
  cancelBusy: boolean
  error?: unknown
  onUpdate: (target: UpdateTarget) => void
  onCancel: (id: string) => void
  errorText: (error: unknown) => string
}

const heading: Record<UpdateTarget, { title: string; description: string }> = {
  core: { title: 'MAA 内核', description: '只使用 stable 通道；更新会重启内核子进程。' },
  resource: { title: '活动资源', description: '选择 OTA、资源仓库或两条通道依次更新。' },
  game: { title: '游戏本体', description: '下载对应渠道安装包，并通过 ADB 覆盖安装。' },
}

function AvailabilityBadge({ available, error }: { available?: boolean | null; error?: unknown }) {
  if (error || available === null) return <Badge variant="warning">检查失败</Badge>
  if (available === true) return <Badge variant="positive">可更新</Badge>
  if (available === false) return <Badge variant="secondary">当前已是最新</Badge>
  return <Badge variant="outline">尚未检查</Badge>
}

function VersionLine({ label, value, unknownLabel = '未知' }: { label: string; value: unknown; unknownLabel?: string }) {
  const text = asText(value)
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 text-sm">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-right font-medium">{text ?? unknownLabel}</dd>
    </div>
  )
}

function ResourceChannels({
  state, selection, reloadMode, onSelectionChange, onReloadModeChange,
}: {
  state?: UpdateTargetState
  selection: ResourceChannel
  reloadMode: 'wait' | 'force' | 'defer'
  onSelectionChange: (channel: ResourceChannel) => void
  onReloadModeChange: (mode: 'wait' | 'force' | 'defer') => void
}) {
  const channels = state?.channels ?? {}
  const repo = channels.repo
  const ota = channels.ota
  return (
    <>
      <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">资源仓库</h3>
          <AvailabilityBadge available={repo?.available} error={repo?.error} />
        </div>
        <dl className="divide-y divide-border/70">
          <VersionLine label="资源版本" value={repo?.current} />
          <VersionLine label="最新资源" value={repo?.latest} />
          <VersionLine label="上次检查" value={repo?.last_checked_at ? formatDate(repo.last_checked_at) : null} />
        </dl>
        {repo?.error && <p role="alert" className="text-sm text-destructive">仓库检查失败：{repo.error}</p>}
      </div>
      <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">OTA 资源</h3>
          <AvailabilityBadge available={ota?.available} error={ota?.error} />
        </div>
        <p className="text-sm text-muted-foreground">版本以远端校验值比较，不展示校验字符串。</p>
        <dl className="divide-y divide-border/70">
          <VersionLine label="上次同步" value={ota?.last_synced_at ? formatDate(ota.last_synced_at) : null} />
          <VersionLine label="上次检查" value={ota?.last_checked_at ? formatDate(ota.last_checked_at) : null} />
        </dl>
        {ota?.error && <p role="alert" className="text-sm text-destructive">OTA 检查失败：{ota.error}</p>}
      </div>
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        更新范围
        <select
          className="min-h-11 rounded-md border border-input bg-background px-3 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={selection}
          onChange={(event) => onSelectionChange(event.target.value as ResourceChannel)}
        >
          <option value="all">两条通道（默认）</option>
          <option value="ota">仅 OTA</option>
          <option value="repo">仅资源仓库</option>
        </select>
      </label>
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        资源生效方式
        <select
          className="min-h-11 rounded-md border border-input bg-background px-3 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={reloadMode}
          onChange={(event) => onReloadModeChange(event.target.value as 'wait' | 'force' | 'defer')}
        >
          <option value="wait">等待任务空闲后生效</option>
          <option value="defer">下载后稍后生效</option>
          <option value="force">立即生效并中断流水线</option>
        </select>
      </label>
    </>
  )
}

function GameChannelView({
  state, selection, onSelectionChange,
}: {
  state?: UpdateTargetState
  selection: GameChannel
  onSelectionChange: (channel: GameChannel) => void
}) {
  const channels = state?.channels ?? {}
  const selected = channels[selection]
  return (
    <>
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        游戏渠道
        <select
          className="min-h-11 rounded-md border border-input bg-background px-3 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={selection}
          onChange={(event) => onSelectionChange(event.target.value as GameChannel)}
        >
          <option value="Official">官服</option>
          <option value="Bilibili">B 服</option>
        </select>
      </label>
      <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">{selection === 'Official' ? '明日方舟（官服）' : '明日方舟（B 服）'}</h3>
          <AvailabilityBadge available={selected?.available} error={selected?.error ?? state?.error} />
        </div>
        {selection === 'Official' && (
          <p className="text-sm text-muted-foreground">官服未提供版本号接口，以下按远端包信息展示。</p>
        )}
        <dl className="divide-y divide-border/70">
          <VersionLine label="已安装" value={selected?.current} />
          {selection === 'Bilibili' && <VersionLine label="最新版本" value={selected?.latest} />}
          {selected?.remote_size != null && <VersionLine label="远端包大小" value={formatBytes(selected.remote_size)} />}
          {selected?.last_checked_at && <VersionLine label="上次检查" value={formatDate(selected.last_checked_at)} />}
        </dl>
        {selected?.error && <p role="alert" className="text-sm text-destructive">该渠道检查失败：{selected.error}</p>}
        {!selected && state?.error && <p role="alert" className="text-sm text-destructive">{state.error}</p>}
      </div>
    </>
  )
}

export function UpdateTargetCard({
  target, state, activeRecord, resourceChannel = 'all', reloadMode = 'wait', gameChannel = 'Official',
  onResourceChannelChange, onReloadModeChange, onGameChannelChange, forceInterrupt, busy, cancelBusy, error,
  onUpdate, onCancel, errorText,
}: UpdateTargetCardProps) {
  const copy = heading[target]
  const active = activeRecord?.target === target ? activeRecord : null
  const resourceAvailable = resourceChannel === 'all'
    ? state?.available
    : state?.channels?.[resourceChannel]?.available
  const selectedGame = state?.channels?.[gameChannel]
  const available = target === 'resource'
    ? resourceAvailable
    : target === 'game'
      ? selectedGame?.available ?? state?.available
      : state?.available
  const selectedError = target === 'resource'
    ? resourceChannel === 'all' ? state?.error : state?.channels?.[resourceChannel]?.error
    : target === 'game'
      ? selectedGame?.error ?? state?.error
      : state?.error
  const canTrigger = target === 'game' ? state !== undefined : target === 'resource'
    ? resourceAvailable === true || state?.reload_pending === true
    // When the core subprocess is stopped, version inspection may return null;
    // docs/07 §7 keeps the stable install/update action available in that state.
    : state !== undefined && state.available !== false
  const buttonLabel = target === 'core'
    ? state?.latest ? `更新至 ${state.latest}` : '安装 stable 内核'
    : target === 'resource'
      ? state?.reload_pending && reloadMode === 'force' ? '立即生效' : resourceChannel === 'all' ? '更新活动资源' : `更新${resourceChannel === 'ota' ? ' OTA' : '资源仓库'}`
      : available === false ? '重新安装游戏' : '下载并安装游戏'

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>{copy.title}</CardTitle>
            <CardDescription className="mt-1">{copy.description}</CardDescription>
          </div>
          <AvailabilityBadge available={available} error={selectedError} />
        </div>
        {(forceInterrupt || (target === 'resource' && reloadMode === 'force')) && (
          <p className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-2 text-sm" role="note">
            强制更新会取消正在执行的流水线。开始前还会再次要求确认。
          </p>
        )}
      </CardHeader>

      <CardContent className="flex-1 space-y-3">
        {target === 'core' && (
          <div className="rounded-lg border bg-muted/20 px-3">
            <dl className="divide-y divide-border/70">
              <VersionLine label="当前版本" value={state?.current} unknownLabel="未知（内核未就绪）" />
              <VersionLine label="最新版本" value={state?.latest ? `${state.latest}（stable）` : null} />
            </dl>
            {state?.error && <p role="alert" className="pb-3 text-sm text-destructive">内核检查失败：{state.error}</p>}
          </div>
        )}

        {target === 'resource' && (
          <>
            <ResourceChannels
              state={state}
              selection={resourceChannel}
              reloadMode={reloadMode}
              onSelectionChange={onResourceChannelChange ?? (() => undefined)}
              onReloadModeChange={onReloadModeChange ?? (() => undefined)}
            />
            <p className="text-sm" aria-live="polite">
              生效状态：{state?.reload_pending ? '资源已下载，等待重载。' : '资源与当前运行状态一致。'}
            </p>
            {state?.error && <p role="alert" className="text-sm text-destructive">资源状态检查失败：{state.error}</p>}
          </>
        )}

        {target === 'game' && (
          <GameChannelView
            state={state}
            selection={gameChannel}
            onSelectionChange={onGameChannelChange ?? (() => undefined)}
          />
        )}

        {active && <UpdateProgress record={active} busy={cancelBusy} onCancel={onCancel} />}
        {error !== undefined && error !== null && <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">{errorText(error)}</p>}
      </CardContent>

      <CardFooter className="flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="min-h-5 text-xs text-muted-foreground">
          {available === null || selectedError ? '此目标的检查失败，其他目标状态仍可使用。' : state ? `状态基于 ${state.target} 检查结果。` : '正在等待状态数据。'}
        </p>
        <Button
          type="button"
          className="w-full sm:w-auto"
          disabled={busy || Boolean(active) || !canTrigger}
          onClick={() => onUpdate(target)}
        >
          {busy ? '正在提交…' : active ? '更新进行中' : buttonLabel}
        </Button>
      </CardFooter>
    </Card>
  )
}
