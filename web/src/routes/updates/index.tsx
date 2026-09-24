import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { api } from '@/api/client'
import { keys } from '@/api/keys'
import { useRealtimeStatus } from '@/realtime/RealtimeProvider'
import { UpdateHistory } from '@/features/updates/UpdateHistory'
import { UpdateTargetCard } from '@/features/updates/UpdateTargetCard'
import type {
  GameChannel, ResourceChannel, UpdateHistoryResponse, UpdateRecord, UpdateStatusResponse,
  UpdateTarget,
} from '@/features/updates/types'
import { errorMessage, recordIsActive } from '@/features/updates/types'

function apiError(error: unknown): Error {
  return new Error(errorMessage(error))
}

export default function UpdatesPage() {
  const queryClient = useQueryClient()
  const realtime = useRealtimeStatus()
  const [resourceChannel, setResourceChannel] = useState<ResourceChannel>('all')
  const [reloadMode, setReloadMode] = useState<'wait' | 'force' | 'defer'>('wait')
  const [gameChannel, setGameChannel] = useState<GameChannel>('Official')
  const [historyDetailId, setHistoryDetailId] = useState<string | null>(null)
  const [forceInterrupt, setForceInterrupt] = useState(false)
  const [manualCheckError, setManualCheckError] = useState<unknown>(null)
  const [isChecking, setIsChecking] = useState(false)
  const [acceptingRecord, setAcceptingRecord] = useState<UpdateRecord | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [busyTarget, setBusyTarget] = useState<UpdateTarget | null>(null)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const previousRunningId = useRef<string | null>(null)

  const statusQuery = useQuery({
    queryKey: keys.updates.status(),
    queryFn: async () => {
      const { data, error } = await api.GET('/api/updates/status')
      if (error) throw apiError(error)
      if (!data) throw new Error('更新状态接口没有返回数据。')
      return data as unknown as UpdateStatusResponse
    },
    // update_progress invalidates this query while WS is live; poll cached status
    // during connect/offline windows so updates started elsewhere are discovered.
    refetchInterval: realtime.status === 'CONNECTED' ? false : 5000,
  })

  const historyQuery = useQuery({
    queryKey: [...keys.updates.all(), { page: 1, size: 20 }],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/updates', { params: { query: { page: 1, size: 20 } } })
      if (error) throw apiError(error)
      if (!data) throw new Error('更新记录接口没有返回数据。')
      return data as unknown as UpdateHistoryResponse
    },
  })

  const status = statusQuery.data
  const candidate = status?.running ?? acceptingRecord
  const activeCandidate = recordIsActive(candidate) ? candidate : null
  const activeId = activeCandidate?.id ?? null
  const detailQuery = useQuery({
    queryKey: [...keys.updates.all(), 'detail', activeId],
    enabled: activeId !== null,
    queryFn: async () => {
      if (!activeId) return null
      const { data, error } = await api.GET('/api/updates/{update_id}', { params: { path: { update_id: activeId } } })
      if (error) throw apiError(error)
      if (!data) throw new Error('更新进度接口没有返回数据。')
      return data as unknown as UpdateRecord
    },
    refetchInterval: activeId ? 2000 : false,
  })
  const historyDetailQuery = useQuery({
    queryKey: [...keys.updates.all(), 'detail', historyDetailId],
    enabled: historyDetailId !== null,
    queryFn: async () => {
      if (!historyDetailId) return null
      const { data, error } = await api.GET('/api/updates/{update_id}', { params: { path: { update_id: historyDetailId } } })
      if (error) throw apiError(error)
      if (!data) throw new Error('更新详情接口没有返回数据。')
      return data as unknown as UpdateRecord
    },
  })

  useEffect(() => {
    const running = status?.running
    if (!running?.id) return
    queryClient.setQueryData<UpdateRecord>([...keys.updates.all(), 'detail', running.id], (previous) => ({
      ...previous,
      ...running,
    }))
  }, [queryClient, status?.running])

  useEffect(() => {
    const runningId = status?.running?.id ?? null
    if (previousRunningId.current && !runningId) {
      void queryClient.invalidateQueries({ queryKey: keys.updates.all() })
    }
    previousRunningId.current = runningId
  }, [queryClient, status?.running?.id])

  const activeRecord = useMemo(() => {
    if (!activeId) return null
    const detail = detailQuery.data
    if (detail?.id === activeId) return detail
    return activeCandidate?.id === activeId ? activeCandidate : null
  }, [activeCandidate, activeId, detailQuery.data])

  useEffect(() => {
    if (acceptingRecord && !recordIsActive(acceptingRecord)) setAcceptingRecord(null)
  }, [acceptingRecord])

  useEffect(() => {
    if (detailQuery.data && !recordIsActive(detailQuery.data)) {
      setAcceptingRecord((current) => current?.id === detailQuery.data?.id ? null : current)
    }
  }, [detailQuery.data])

  async function checkNow() {
    setManualCheckError(null)
    setIsChecking(true)
    try {
      const { data, error } = await api.GET('/api/updates/status', { params: { query: { refresh: true } } })
      if (error) throw apiError(error)
      if (!data) throw new Error('更新状态接口没有返回数据。')
      queryClient.setQueryData(keys.updates.status(), data as unknown as UpdateStatusResponse)
    } catch (error) {
      setManualCheckError(error)
    } finally {
      setIsChecking(false)
    }
  }

  async function submitUpdate(target: UpdateTarget) {
    const interrupt = forceInterrupt || (target === 'resource' && reloadMode === 'force')

    if (interrupt) {
      const confirmed = window.confirm('强制更新会取消正在执行的流水线。继续后，当前任务可能被中断。是否继续？')
      if (!confirmed) return
    }

    setActionError(null)
    setBusyTarget(target)
    try {
      const result = target === 'core'
        ? await api.POST('/api/updates/core', { body: { channel: 'stable', force: false, force_interrupt: interrupt } })
        : target === 'resource'
          ? await api.POST('/api/updates/resource', {
            body: {
              channel: resourceChannel,
              force: false,
              force_interrupt: interrupt,
              reload_mode: reloadMode,
            },
          })
          : await api.POST('/api/updates/game', {
            body: { channel: gameChannel, force: false, force_interrupt: interrupt },
          })
      if (result.error) throw apiError(result.error)
      const record = result.data as unknown as UpdateRecord | undefined
      if (!record) throw new Error('更新请求已提交，但接口没有返回记录编号。')
      setAcceptingRecord(record)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.updates.status() }),
        queryClient.invalidateQueries({ queryKey: keys.updates.all() }),
      ])
      if (!recordIsActive(record)) setAcceptingRecord(null)
    } catch (error) {
      setActionError(error)
      await queryClient.invalidateQueries({ queryKey: keys.updates.status() })
    } finally {
      setBusyTarget(null)
    }
  }

  async function cancelUpdate(id: string) {
    setActionError(null)
    setCancelBusy(true)
    try {
      const { error } = await api.DELETE('/api/updates/{update_id}', { params: { path: { update_id: id } } })
      if (error) throw apiError(error)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.updates.status() }),
        queryClient.invalidateQueries({ queryKey: [...keys.updates.all(), 'detail', id] }),
        queryClient.invalidateQueries({ queryKey: keys.updates.all() }),
      ])
    } catch (error) {
      setActionError(error)
    } finally {
      setCancelBusy(false)
    }
  }

  async function retryUpdate(id: string) {
    setActionError(null)
    setRetryingId(id)
    try {
      const { data, error } = await api.POST('/api/updates/{update_id}/retry', { params: { path: { update_id: id } } })
      if (error) throw apiError(error)
      const record = data as unknown as UpdateRecord | undefined
      if (!record) throw new Error('重试请求已提交，但接口没有返回记录编号。')
      setAcceptingRecord(record)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.updates.status() }),
        queryClient.invalidateQueries({ queryKey: keys.updates.all() }),
      ])
    } catch (error) {
      setActionError(error)
    } finally {
      setRetryingId(null)
    }
  }

  const updates = status?.updates ?? {}
  const records = historyQuery.data?.items ?? []
  const globalActive = Boolean(activeRecord)

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-2xl space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">系统维护</p>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">热更新</h1>
          <p className="text-sm leading-6 text-muted-foreground">
            检查内核、活动资源和游戏版本。检查只读取版本信息；下载与安装由你手动开始。
          </p>
        </div>
        <Button type="button" onClick={checkNow} disabled={isChecking} className="w-full sm:w-auto">
          {isChecking ? '正在检查…' : '立即检查更新'}
        </Button>
      </header>

      <div className="space-y-3">
        {manualCheckError !== null && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm" role="alert">
            立即检查失败：{errorMessage(manualCheckError)}
          </div>
        )}
        {actionError !== null && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm" role="alert">
            更新操作失败：{errorMessage(actionError)}
          </div>
        )}
        {statusQuery.error && status && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm" role="alert">
            状态刷新失败，正在显示上次成功的状态：{errorMessage(statusQuery.error)}
          </div>
        )}
        {statusQuery.error && !status && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm" role="alert">
            无法读取更新状态：{errorMessage(statusQuery.error)}
          </div>
        )}
        {statusQuery.isLoading && !status && (
          <Card className="p-4 text-sm text-muted-foreground" role="status">正在读取更新状态…</Card>
        )}
        {status?.checked_at && (
          <p className="text-xs text-muted-foreground" aria-live="polite">
            上次检查：{new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(status.checked_at))}
            {status.cached ? ' · 显示缓存结果' : ''}
          </p>
        )}
        {status?.running && (
          <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm" role="status">
            当前有一个更新任务正在执行；内核、资源与游戏更新共用同一条更新队列。
          </p>
        )}
      </div>

      <Card className="p-4 sm:p-5">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            className="mt-1 size-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            checked={forceInterrupt}
            onChange={(event) => setForceInterrupt(event.target.checked)}
            aria-describedby="force-interrupt-description"
          />
          <span>
            <span className="block text-sm font-medium">允许强制中断流水线</span>
            <span id="force-interrupt-description" className="mt-1 block text-sm text-muted-foreground">
              仅在更新请求被流水线阻塞时使用。启用后会取消正在执行的流水线，并在提交前再次确认。
            </span>
          </span>
        </label>
      </Card>

      <section aria-label="更新目标" className="grid gap-4 lg:grid-cols-3">
        {(['core', 'resource', 'game'] as const).map((target) => (
          <UpdateTargetCard
            key={target}
            target={target}
            state={updates[target]}
            activeRecord={activeRecord?.target === target ? activeRecord : null}
            resourceChannel={resourceChannel}
            reloadMode={reloadMode}
            gameChannel={gameChannel}
            onResourceChannelChange={setResourceChannel}
            onReloadModeChange={setReloadMode}
            onGameChannelChange={setGameChannel}
            forceInterrupt={forceInterrupt}
            busy={busyTarget === target || (globalActive && !activeRecord?.target) || (globalActive && activeRecord?.target !== target)}
            cancelBusy={cancelBusy}
            error={actionError && !activeRecord?.target ? actionError : null}
            onUpdate={submitUpdate}
            onCancel={cancelUpdate}
            errorText={errorMessage}
          />
        ))}
      </section>

      {detailQuery.error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm" role="alert">
          更新进度读取失败，将继续尝试恢复：{errorMessage(detailQuery.error)}
        </div>
      )}

      <UpdateHistory
        records={records}
        total={historyQuery.data?.total}
        isLoading={historyQuery.isLoading}
        error={historyQuery.error}
        retryingId={retryingId}
        detailId={historyDetailId}
        detail={historyDetailQuery.data}
        detailError={historyDetailQuery.error}
        detailLoading={historyDetailQuery.isLoading}
        onRetry={retryUpdate}
        onDetailRequested={setHistoryDetailId}
        onRefresh={() => void historyQuery.refetch()}
      />
    </main>
  )
}
