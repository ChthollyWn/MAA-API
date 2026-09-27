import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { UpdateHistory } from '@/features/updates/UpdateHistory'
import { UpdateOptionsSheet } from '@/features/updates/UpdateOptionsSheet'
import { UpdateProgress } from '@/features/updates/UpdateProgress'
import { UpdateRecordSheet } from '@/features/updates/UpdateRecordSheet'
import { UpdateTargetCard } from '@/features/updates/UpdateTargetCard'
import type { GameChannel, UpdateOptions, UpdateTarget } from '@/features/updates/types'
import { errorMessage, formatDate, targetLabels, withLeadingSpace } from '@/features/updates/types'
import { useUpdates } from '@/features/updates/use-updates'

const TARGETS = ['core', 'resource', 'game'] as const satisfies readonly UpdateTarget[]

interface OptionsSheetState {
  target: UpdateTarget
  open: boolean
  /** 每次打开换一个 key，让面板里的选择回到默认值；关闭时保留，收起动画里内容不跳变。 */
  key: number
}

export default function UpdatesPage() {
  const {
    statusQuery, historyQuery, detailQuery, historyDetailQuery, historyDetailId, setHistoryDetailId,
    activeRecord, check, submit, cancel, retry,
  } = useUpdates()
  const [confirm, confirmElement] = useConfirm()
  const [gameChannel, setGameChannel] = useState<GameChannel>('Official')
  const [optionsSheet, setOptionsSheet] = useState<OptionsSheetState | null>(null)
  const [recordOpen, setRecordOpen] = useState(false)

  const status = statusQuery.data
  const records = historyQuery.data?.items ?? []
  const shownRecord = historyDetailId
    ? historyDetailQuery.data?.id === historyDetailId
      ? historyDetailQuery.data
      : records.find((record) => record.id === historyDetailId) ?? null
    : null

  const openOptions = (target: UpdateTarget) => {
    submit.reset()
    setOptionsSheet((current) => ({ target, open: true, key: (current?.key ?? 0) + 1 }))
  }
  const setOptionsOpen = (open: boolean) => {
    // 面板里已经看过的失败不再搬到页面上；只有关掉面板之后才返回的失败才在页面上提示。
    if (!open && !submit.isPending) submit.reset()
    setOptionsSheet((current) => current && { ...current, open })
  }
  const setRecordSheetOpen = (open: boolean) => {
    if (!open && !retry.isPending) retry.reset()
    setRecordOpen(open)
  }

  const startUpdate = (target: UpdateTarget, options: UpdateOptions) => {
    submit.mutate({ target, options }, {
      onSuccess: () => {
        setOptionsOpen(false)
        toast.success(`已开始更新${withLeadingSpace(targetLabels[target])}`)
      },
    })
  }

  const cancelUpdate = async (id: string) => {
    const ok = await confirm({
      title: '取消这次更新？',
      description: '已下载的部分会被丢弃，之后可以重新开始。',
      confirmText: '取消更新',
      cancelText: '继续更新',
      tone: 'danger',
    })
    if (ok) cancel.mutate(id, { onSuccess: () => toast.success('已取消更新') })
  }

  const openRecord = (id: string) => {
    retry.reset()
    if (id === historyDetailId) void historyDetailQuery.refetch()
    else setHistoryDetailId(id)
    setRecordOpen(true)
  }

  const retryRecord = (id: string) => {
    retry.mutate(id, {
      onSuccess: () => {
        setRecordOpen(false)
        toast.success('已重新开始更新')
      },
    })
  }

  return (
    <Page title="热更新" back="/more">
      <div className="space-y-5 pb-4">
        <div className="space-y-1.5 px-1">
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1" aria-live="polite">
              <p className="flex items-center gap-2 text-footnote text-muted-foreground">
                上次检查
                {status?.cached ? <Badge variant="neutral">缓存结果</Badge> : null}
              </p>
              <p className="mt-0.5 text-[0.9375rem] font-medium tabular-nums">
                {status?.checked_at
                  ? formatDate(status.checked_at)
                  : statusQuery.isPending ? '正在读取…' : '还没有检查过'}
              </p>
            </div>
            <Button variant="outline" size="sm" className="shrink-0" loading={check.isPending} onClick={() => check.mutate()}>
              {check.isPending ? null : <RefreshCw aria-hidden="true" />}
              {check.isPending ? '正在检查…' : '立即检查'}
            </Button>
          </div>
          <p className="text-footnote text-muted-foreground">检查只读取版本信息，下载与安装由你手动开始。</p>
        </div>

        {check.error ? (
          <InlineAlert tone="danger" title="立即检查失败">
            {errorMessage(check.error)}
            {status ? <span className="mt-0.5 block">下方仍是上次的检查结果。</span> : null}
          </InlineAlert>
        ) : null}
        {submit.error && !optionsSheet?.open ? <InlineAlert tone="danger">更新操作失败：{errorMessage(submit.error)}</InlineAlert> : null}
        {retry.error && !recordOpen ? <InlineAlert tone="danger">重试失败：{errorMessage(retry.error)}</InlineAlert> : null}
        {statusQuery.error && status ? (
          <InlineAlert
            tone="warning"
            action={<Button variant="outline" size="sm" loading={statusQuery.isFetching} onClick={() => void statusQuery.refetch()}>重试</Button>}
          >
            状态刷新失败，正在显示上次成功的状态：{errorMessage(statusQuery.error)}
          </InlineAlert>
        ) : null}

        {activeRecord ? (
          <UpdateProgress record={activeRecord} busy={cancel.isPending} onCancel={(id) => void cancelUpdate(id)}>
            <p className="text-footnote text-muted-foreground">内核、活动资源与游戏共用一条更新队列，当前更新结束后才能开始下一个。</p>
            {detailQuery.error ? <InlineAlert tone="warning">更新进度读取失败，将继续尝试恢复：{errorMessage(detailQuery.error)}</InlineAlert> : null}
            {cancel.error ? <InlineAlert tone="danger">取消失败：{errorMessage(cancel.error)}</InlineAlert> : null}
          </UpdateProgress>
        ) : null}

        {status ? (
          <section aria-label="更新目标" className="space-y-3">
            {TARGETS.map((target) => (
              <UpdateTargetCard
                key={target}
                target={target}
                state={status.updates?.[target]}
                gameChannel={gameChannel}
                onGameChannelChange={setGameChannel}
                updating={activeRecord?.target === target}
                locked={activeRecord !== null}
                onAction={() => openOptions(target)}
              />
            ))}
          </section>
        ) : statusQuery.error ? (
          <div className="rounded-xl border bg-card shadow-card">
            <ErrorState
              message={`无法读取更新状态：${errorMessage(statusQuery.error)}`}
              onRetry={() => void statusQuery.refetch()}
              retrying={statusQuery.isFetching}
            />
          </div>
        ) : (
          <div className="rounded-xl border bg-card shadow-card"><LoadingState label="正在读取更新状态…" /></div>
        )}

        <UpdateHistory
          records={records}
          total={historyQuery.data?.total}
          isLoading={historyQuery.isPending}
          isFetching={historyQuery.isFetching}
          error={historyQuery.error}
          onRefresh={() => void historyQuery.refetch()}
          onSelect={openRecord}
        />
      </div>

      {optionsSheet ? (
        <UpdateOptionsSheet
          key={optionsSheet.key}
          open={optionsSheet.open}
          onOpenChange={setOptionsOpen}
          target={optionsSheet.target}
          state={status?.updates?.[optionsSheet.target]}
          gameChannel={gameChannel}
          submitting={submit.isPending}
          error={submit.error}
          onSubmit={(options) => startUpdate(optionsSheet.target, options)}
        />
      ) : null}
      <UpdateRecordSheet
        open={recordOpen}
        onOpenChange={setRecordSheetOpen}
        record={shownRecord}
        loading={historyDetailQuery.isFetching && historyDetailQuery.data?.id !== historyDetailId}
        error={historyDetailQuery.error}
        retrying={retry.isPending}
        retryError={retry.error}
        onRetry={retryRecord}
      />
      {confirmElement}
    </Page>
  )
}
