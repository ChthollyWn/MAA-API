import { useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { SwitchRow } from '@/components/ui/switch'
import { VersionChange } from '@/features/updates/VersionChange'
import type {
  GameChannel, ReloadMode, ResourceChannel, UpdateOptions, UpdateSelection, UpdateTarget, UpdateTargetState,
} from '@/features/updates/types'
import {
  actionLabel, asText, canTrigger, errorMessage, formatBytes, gameChannelLabels, initialSelection,
  interruptActionLabel, interruptsPipeline, selectionAvailability, targetLabels, withLeadingSpace,
} from '@/features/updates/types'

const scopeOptions = [
  { value: 'all', label: '全部' },
  { value: 'ota', label: '仅 OTA' },
  { value: 'repo', label: '仅仓库' },
] as const satisfies readonly { value: ResourceChannel; label: string }[]

const scopeHints: Record<ResourceChannel, string> = {
  all: 'OTA 与资源仓库依次更新。',
  ota: '只同步 OTA 资源。',
  repo: '只更新资源仓库。',
}

const reloadOptions = [
  { value: 'wait', label: '等待空闲' },
  { value: 'defer', label: '延后' },
  { value: 'force', label: '立即' },
] as const satisfies readonly { value: ReloadMode; label: string }[]

const reloadHints: Record<ReloadMode, string> = {
  wait: '下载完成后，等当前任务结束再重载资源。',
  defer: '只下载，暂不重载；之后再选「立即」让它生效。',
  force: '下载完成后立即重载资源，会中断正在执行的流水线。',
}

function SummaryRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 justify-end text-right text-sm">{children}</dd>
    </div>
  )
}

function scopeProblem(state: UpdateTargetState | undefined, selection: UpdateSelection): string | null {
  if (canTrigger('resource', state, selection)) return null
  const available = selectionAvailability('resource', state, selection)
  if (available === false) return '所选范围已是最新，没有需要下载的内容。'
  if (available === null) return '所选范围的检查失败，请先重新检查。'
  return '所选范围还没有检查结果，请先检查更新。'
}

export interface UpdateOptionsSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  target: UpdateTarget
  state?: UpdateTargetState
  gameChannel: GameChannel
  submitting: boolean
  error: unknown
  onSubmit: (options: UpdateOptions) => void
}

/** 点主操作后打开：选资源范围、生效方式与是否强制中断，确认后才提交。 */
export function UpdateOptionsSheet({ open, onOpenChange, target, state, gameChannel, submitting, error, onSubmit }: UpdateOptionsSheetProps) {
  const [selection, setSelection] = useState<UpdateSelection>(() => initialSelection(target, state, gameChannel))
  const [forceInterrupt, setForceInterrupt] = useState(false)
  const options: UpdateOptions = { ...selection, forceInterrupt }
  const interrupt = interruptsPipeline(target, options)
  const reloadInterrupts = target === 'resource' && selection.reloadMode === 'force'
  const problem = target === 'resource' ? scopeProblem(state, selection) : null
  const allowed = canTrigger(target, state, selection)
  const label = interrupt ? interruptActionLabel(target, state, selection) : actionLabel(target, state, selection)
  const game = target === 'game' ? state?.channels?.[selection.gameChannel] : undefined

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title={`更新${withLeadingSpace(targetLabels[target])}`}
      description="确认后开始，进度会显示在页面顶部。"
      footer={(
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" disabled={submitting} onClick={() => onOpenChange(false)}>取消</Button>
          <Button
            variant={interrupt ? 'destructive' : 'default'}
            loading={submitting}
            disabled={!allowed}
            onClick={() => onSubmit(options)}
            className="whitespace-normal"
          >
            {submitting ? '正在提交…' : label}
          </Button>
        </div>
      )}
    >
      <div className="space-y-5 pt-1">
        {target === 'core' ? (
          <dl className="divide-y">
            <SummaryRow label="版本">
              <VersionChange current={asText(state?.current)} latest={asText(state?.latest)} unknownLabel="未知（内核未就绪）" className="justify-end" />
            </SummaryRow>
            <SummaryRow label="通道">stable</SummaryRow>
          </dl>
        ) : null}

        {target === 'game' ? (
          <dl className="divide-y">
            <SummaryRow label="渠道">{gameChannelLabels[selection.gameChannel]}</SummaryRow>
            <SummaryRow label="版本">
              <VersionChange
                current={asText(game?.current)}
                latest={selection.gameChannel === 'Bilibili' ? asText(game?.latest) : null}
                className="justify-end"
              />
            </SummaryRow>
            {game?.remote_size != null ? <SummaryRow label="安装包">{formatBytes(game.remote_size)}</SummaryRow> : null}
          </dl>
        ) : null}

        {target === 'resource' ? (
          <>
            <Field label="资源范围" description={problem ?? scopeHints[selection.resourceChannel]}>
              <SegmentedControl
                ariaLabel="资源范围"
                fullWidth
                value={selection.resourceChannel}
                onValueChange={(resourceChannel) => setSelection((current) => ({ ...current, resourceChannel }))}
                options={scopeOptions}
              />
            </Field>
            <Field label="生效方式" description={reloadHints[selection.reloadMode]}>
              <SegmentedControl
                ariaLabel="生效方式"
                fullWidth
                value={selection.reloadMode}
                onValueChange={(reloadMode) => setSelection((current) => ({ ...current, reloadMode }))}
                options={reloadOptions}
              />
            </Field>
          </>
        ) : null}

        <div className="space-y-2">
          <SwitchRow
            label="允许强制中断正在运行的流水线"
            description={reloadInterrupts ? '「立即」生效方式本身就会中断流水线。' : '只在更新被流水线阻塞时打开。'}
            checked={interrupt}
            disabled={reloadInterrupts}
            onCheckedChange={setForceInterrupt}
          />
          {interrupt ? (
            <InlineAlert tone="warning" title="会中断当前任务">
              正在执行的流水线会被立即取消，未完成的任务不会继续执行。
            </InlineAlert>
          ) : null}
        </div>

        {error ? <InlineAlert tone="danger">更新操作失败：{errorMessage(error)}</InlineAlert> : null}
      </div>
    </ResponsiveSheet>
  )
}
