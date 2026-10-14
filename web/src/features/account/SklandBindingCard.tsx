import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QrCode, RefreshCw, Unlink } from 'lucide-react'
import { Badge, StatusDot } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { Spinner } from '@/components/ui/states'
import {
  DEFAULT_SKLAND_POLL_INTERVAL_MS,
  accountKeys,
  bindSklandManual,
  fetchAccountSummary,
  fetchSklandStatus,
  pollSklandQrLogin,
  retryAfterSeconds,
  startSklandQrLogin,
  syncSkland,
  unbindSkland,
  type SklandRole,
  type SklandStatus,
  type SklandSyncSummary,
} from './api'
import {
  errorText,
  recognitionText,
  recognizedCountText,
  staleBadgeText,
} from './format'

/**
 * 「森空岛练度」卡片（M42 规格 §4.8，M47 合并状态）：扫码绑定森空岛账号、同步官方练度，
 * **同时**显示这份数据的状态（同步时间、是否过期、规模）。
 *
 * M47 之前这里是「绑定/同步」一张、干员页顶部另有一张「干员识别」状态卡（内核任务写的，
 * 与森空岛是两个数据源）。内核识别删掉之后两者是同一份数据，于是并成一张：标题行给
 * 状态点与过期徽章，账号行给「共 N 名（精二 M）· 同步于 …」，动作按钮仍在右上角。
 * 仓库页的「仓库识别」状态卡与本组件无关，仍用 `RecognitionStatus`。
 */

const KEY_COMMAND = '.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'

/** 二维码剩余有效期的倒计时：`2:57`；`expires_at` 读不出来给 `null`。 */
export function qrRemainingText(expiresAt: string | null | undefined, now: number): string | null {
  if (!expiresAt) return null
  const deadline = new Date(expiresAt).getTime()
  if (Number.isNaN(deadline)) return null
  const remaining = Math.max(0, Math.ceil((deadline - now) / 1000))
  return `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`
}

/** 本地倒计时是否已经走到 0。 */
export function qrExpired(expiresAt: string | null | undefined, now: number): boolean {
  if (!expiresAt) return false
  const deadline = new Date(expiresAt).getTime()
  return !Number.isNaN(deadline) && deadline <= now
}

/** 角色的显示名：昵称优先，缺了退回 `uid`。 */
export function sklandRoleText(role: SklandRole): string {
  return role.nickname ?? role.uid
}

/** 已绑定那一行：昵称 · 等级 · 角色数；读不出的项不提。 */
export function sklandAccountLine(account: SklandRole | null, roles: SklandRole[]): string {
  const name = account ? sklandRoleText(account) : '已绑定'
  const level = typeof account?.level === 'number' ? ` · ${account.level} 级` : ''
  return `${name}${level} · 共 ${roles.length} 个角色`
}

/**
 * 上次同步摘要：只提非零项（§4.8）。全零时写「没有改动」；
 * `duration_ms` 单独一项。
 */
export function sklandSyncSummaryLines(summary: SklandSyncSummary): string[] {
  const changed = [
    summary.created > 0 ? `新增 ${summary.created}` : null,
    summary.updated > 0 ? `更新 ${summary.updated}` : null,
    summary.unchanged > 0 ? `未变 ${summary.unchanged}` : null,
  ].filter((item): item is string => item !== null)
  const lines = [`本轮 ${summary.total} 名：${changed.length ? changed.join(' · ') : '没有改动'}`]
  const extra = [
    summary.ownership_cleared > 0 ? `清掉未拥有 ${summary.ownership_cleared} 名` : null,
    summary.dropped_invalid > 0 ? `丢弃越界值 ${summary.dropped_invalid} 处` : null,
  ].filter((item): item is string => item !== null)
  if (extra.length) lines.push(extra.join(' · '))
  if (summary.duration_ms > 0) lines.push(`耗时 ${(summary.duration_ms / 1000).toFixed(1)} 秒`)
  return lines
}

/** 同步一次的护栏说明：冷却与写入范围（§4.5）。 */
function syncHint(status: SklandStatus): string {
  const cooldown = status.min_sync_interval_seconds > 0
    ? `两次同步之间最少间隔 ${status.min_sync_interval_seconds} 秒；`
    : ''
  return `${cooldown}同步会写技能等级、专精、模组与信赖，字段来源标成「森空岛」；上游数据可能比游戏内慢一步。`
}

interface QrProblem {
  tone: 'danger' | 'warning'
  title: string
  message: string
}

export function SklandBindingCard() {
  const queryClient = useQueryClient()
  const [confirm, confirmElement] = useConfirm()
  const [sheetOpen, setSheetOpen] = useState(false)
  const [cred, setCred] = useState('')
  const [token, setToken] = useState('')
  const [now, setNow] = useState(() => Date.now())

  const status = useQuery({
    queryKey: accountKeys.sklandStatus(),
    queryFn: ({ signal }) => fetchSklandStatus(signal),
    retry: false,
  })

  // 干员数据的概况（同步时间 / 是否过期 / 已拥有与精二数）：M47 起唯一来源就是下面这个
  // 同步动作，所以这张卡自己取、自己失效，不再由干员页另开一张状态卡。
  const summary = useQuery({
    queryKey: accountKeys.summary(),
    queryFn: ({ signal }) => fetchAccountSummary(signal),
    retry: false,
  })
  const entry = summary.data?.operators
  const staleBadge = entry ? staleBadgeText(entry) : null
  const statusTone = !entry || !entry.recognized ? 'neutral' : entry.stale ? 'warning' : 'success'
  const countText = entry ? recognizedCountText(entry, '名') : null
  const syncedAtText = entry ? recognitionText(entry, '同步于') : null

  const data = status.data
  const bound = data?.bound === true
  const keyConfigured = data?.key_configured !== false
  const needsQr = Boolean(data) && keyConfigured && !bound

  const qr = useQuery({
    queryKey: accountKeys.sklandQr(),
    queryFn: () => startSklandQrLogin(),
    enabled: needsQr,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
  const session = qr.data && qr.data.session_id && qr.data.qr_svg ? qr.data : null
  const sessionId = session?.session_id ?? ''
  const pollIntervalMs = session?.poll_interval_ms ?? DEFAULT_SKLAND_POLL_INTERVAL_MS

  const poll = useQuery({
    queryKey: accountKeys.sklandQrPoll(sessionId),
    queryFn: ({ signal }) => pollSklandQrLogin(sessionId, signal),
    enabled: needsQr && Boolean(sessionId),
    retry: false,
    refetchInterval: (query) => {
      const state = query.state.data?.state
      const active = needsQr
        && Boolean(sessionId)
        && !query.state.error
        && (state === undefined || state === 'waiting')
      return active ? pollIntervalMs : false
    },
  })

  useEffect(() => {
    if (!sessionId) return
    setNow(Date.now())
  }, [sessionId])

  const qrState = poll.data?.state ?? 'waiting'
  const scannedHint = qrState === 'waiting' ? poll.data?.message ?? null : null
  const qrVisible = Boolean(sessionId) && needsQr && qrState !== 'succeeded'

  useEffect(() => {
    if (!qrVisible) return
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [qrVisible])

  const qrSucceeded = poll.data?.state === 'succeeded'
  useEffect(() => {
    if (!qrSucceeded) return
    void queryClient.invalidateQueries({ queryKey: accountKeys.sklandStatus() })
  }, [qrSucceeded, queryClient])

  const invalidateStatus = () => queryClient.invalidateQueries({ queryKey: accountKeys.sklandStatus() })

  const sync = useMutation({
    mutationFn: () => syncSkland(),
    onSuccess: async () => {
      await Promise.all([
        invalidateStatus(),
        queryClient.invalidateQueries({ queryKey: accountKeys.operatorsAll() }),
        queryClient.invalidateQueries({ queryKey: accountKeys.summary() }),
      ])
    },
  })

  const bind = useMutation({
    mutationFn: () => bindSklandManual({ cred: cred.trim(), token: token.trim() }),
    onSuccess: async () => {
      setCred('')
      setToken('')
      setSheetOpen(false)
      await invalidateStatus()
    },
  })

  const unbind = useMutation({
    mutationFn: () => unbindSkland(),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: accountKeys.sklandQr() })
      await invalidateStatus()
    },
  })

  const requestUnbind = async () => {
    const approved = await confirm({
      title: '解绑森空岛账号？',
      description:
        '会删掉保存在本机的 cred 与签名 token，已同步到账号数据的练度保留；需要时重新扫码绑定。',
      confirmText: '解绑',
      cancelText: '再想想',
      tone: 'danger',
    })
    if (!approved) return
    unbind.mutate()
  }

  const expired = qrExpired(session?.expires_at, now)
  const remaining = qrRemainingText(session?.expires_at, now)

  const regenerateQr = async () => {
    if (sessionId) queryClient.removeQueries({ queryKey: accountKeys.sklandQrPoll(sessionId) })
    await qr.refetch()
  }

  const qrProblem: QrProblem | null = !needsQr
    ? null
    : qr.isError
      ? { tone: 'danger', title: '生成二维码失败', message: errorText(qr.error) }
      : poll.isError
        ? { tone: 'danger', title: '读取二维码状态失败', message: errorText(poll.error) }
        : qrState === 'failed'
          ? { tone: 'danger', title: '扫码登录失败', message: poll.data?.message ?? '森空岛没有接受这次登录，重新生成一张再试。' }
          : qrState === 'expired' || (session !== null && expired)
            ? { tone: 'warning', title: '二维码已失效', message: poll.data?.message ?? '二维码已经过期，重新生成一张再扫。' }
            : null

  const canSubmitManual = cred.trim().length > 0 && token.trim().length > 0
  const retrySeconds = retryAfterSeconds(sync.error)

  const body = !data && status.isPending ? (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Spinner />
      正在读取森空岛绑定状态…
    </p>
  ) : !data ? (
    <InlineAlert
      tone="danger"
      title="读取绑定状态失败"
      action={(
        <Button variant="outline" size="sm" loading={status.isFetching} onClick={() => void status.refetch()}>
          重试
        </Button>
      )}
    >
      {errorText(status.error)}
    </InlineAlert>
  ) : !keyConfigured ? (
    <div className="space-y-2.5">
      <InlineAlert tone="warning" title="还没有配凭据加密密钥">
        先在 <code>config.yaml</code> 里配 <code>skland.credential_key</code>，再回到这一页扫码绑定；
        密钥没配好之前，后端不会接受绑定请求。
      </InlineAlert>
      <p className="text-footnote text-muted-foreground">生成一个密钥（写进 <code>config.yaml</code> 的 <code>skland.credential_key</code>）：</p>
      <code data-testid="skland-key-command" className="block overflow-x-auto rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs">
        {KEY_COMMAND}
      </code>
    </div>
  ) : bound ? (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-footnote">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span data-testid="skland-account" className="font-medium text-foreground">
            {sklandAccountLine(data.account, data.roles)}
          </span>
          {data.roles.length > 1 ? (
            <span className="text-muted-foreground">
              （{data.roles.map((role) => (role.is_default ? `${sklandRoleText(role)}（默认）` : sklandRoleText(role))).join('、')}）
            </span>
          ) : null}
        </div>
      </div>

      {entry?.stale ? (
        <InlineAlert tone="warning" role="status">
          账号数据已超过 {entry.stale_after_days} 天没同步，需要时点右上角「同步练度」刷新。
        </InlineAlert>
      ) : null}

      {data.last_sync ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-footnote tabular-nums text-muted-foreground">
          {sklandSyncSummaryLines(data.last_sync).map((line, index) => (
            <span key={line} className="inline-flex items-center gap-2">
              {index > 0 ? <span aria-hidden="true" className="text-border-strong">·</span> : null}
              <span>{line}</span>
            </span>
          ))}
        </div>
      ) : null}

      {data.last_error ? (
        <InlineAlert tone="warning" title="上次同步失败">{data.last_error}</InlineAlert>
      ) : null}

      {sync.error ? (
        <InlineAlert tone="danger" title="同步失败">
          {errorText(sync.error)}{retrySeconds ? `（还要等 ${retrySeconds} 秒）` : ''}
        </InlineAlert>
      ) : null}
      {unbind.error ? (
        <InlineAlert tone="danger" title="解绑失败">{errorText(unbind.error)}</InlineAlert>
      ) : null}
    </div>
  ) : (
    <div className="space-y-3">
      {qrProblem ? (
        <InlineAlert
          tone={qrProblem.tone}
          title={qrProblem.title}
          action={(
            <Button variant="outline" size="sm" loading={qr.isFetching} onClick={() => void regenerateQr()}>
              {sessionId ? '重新生成二维码' : '重试'}
            </Button>
          )}
        >
          {qrProblem.message}
        </InlineAlert>
      ) : !session ? (
        <p role="status" className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
          <Spinner />
          正在生成二维码…
        </p>
      ) : qrState === 'succeeded' ? (
        <p role="status" className="py-2 text-sm font-medium">已扫码，正在读取账号数据…</p>
      ) : (
        <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
          <img
            className="size-36 shrink-0 rounded-xl border bg-white p-2 shadow-sm sm:size-40"
            src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(session.qr_svg)}`}
            alt="森空岛登录二维码"
          />
          <div className="min-w-0 flex-1 space-y-2 text-center text-footnote text-muted-foreground sm:pt-1 sm:text-left">
            <div className="flex items-center justify-center gap-1.5 sm:justify-start">
              <QrCode aria-hidden="true" className="size-4 text-primary-text" />
              {scannedHint ? (
                <p role="status" className="text-sm font-medium text-foreground">{scannedHint}</p>
              ) : (
                <p className="text-sm font-medium text-foreground">用森空岛 App 扫码，确认后回到这一页。</p>
              )}
            </div>
            {remaining ? (
              <div>
                <Badge variant="neutral" data-testid="skland-qr-remaining" className="tabular-nums">
                  二维码 {remaining} 后失效
                </Badge>
              </div>
            ) : null}
            <p>cred 与 token 只保存在本机；随时可以解绑。</p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-2.5">
        <span className="text-footnote text-muted-foreground">扫码不可用时，可以粘贴 cred 与签名 token 绑定。</span>
        <Button variant="outline" size="sm" onClick={() => setSheetOpen(true)}>手动输入 cred</Button>
      </div>
    </div>
  )

  return (
    <section
      id="skland-binding"
      aria-labelledby="skland-binding-title"
      className="space-y-2 rounded-xl border bg-card px-3.5 py-2.5 shadow-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <StatusDot tone={statusTone} />
          <h2 id="skland-binding-title" className="text-sm font-semibold text-foreground">
            森空岛练度
          </h2>
          {staleBadge ? <Badge variant={statusTone}>{staleBadge}</Badge> : null}
          <InfoHint label="森空岛练度说明">
            {bound && data ? syncHint(data) : '干员数据的唯一来源：用森空岛账号同步技能等级、专精、模组与信赖；不占设备、不读屏。'}
          </InfoHint>
        </div>
        {bound ? (
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="soft"
              size="sm"
              className="h-7 min-h-7 shrink-0 px-2.5 text-xs"
              loading={sync.isPending}
              onClick={() => sync.mutate()}
            >
              {sync.isPending ? null : <RefreshCw aria-hidden="true" className="size-3.5!" />}
              {sync.isPending ? '正在同步…' : '同步练度'}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 min-h-7 shrink-0 px-2.5 text-xs"
              loading={unbind.isPending}
              onClick={() => void requestUnbind()}
            >
              {unbind.isPending ? null : <Unlink aria-hidden="true" className="size-3.5!" />}
              {unbind.isPending ? '正在解绑…' : '解绑'}
            </Button>
          </div>
        ) : null}
      </div>

      {/* 账号数据的现状（M47 起这张卡同时承担旧「干员识别」状态卡的职责）：
          规模、同步时间、以及没数据时该做什么——绑定与否都显示 */}
      {entry ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-footnote">
          {countText ? (
            <span className="rounded-md bg-surface-2 px-2 py-0.5 font-medium tabular-nums text-foreground">
              {countText}
            </span>
          ) : null}
          <span className="tabular-nums text-muted-foreground">
            {entry.recognized ? syncedAtText : '还没有同步过练度'}
          </span>
          {!bound ? (
            <span className="text-muted-foreground">绑定森空岛账号后点「同步练度」写入干员数据。</span>
          ) : null}
        </div>
      ) : null}

      <div>{body}</div>

      <ResponsiveSheet
        open={sheetOpen}
        onOpenChange={(next) => {
          setSheetOpen(next)
          if (next) return
          setCred('')
          setToken('')
          bind.reset()
        }}
        title="手动输入 cred"
        description="扫码不可用时，粘贴森空岛客户端抓到的 cred 与签名 token；两项都不能为空。"
        footer={(
          <div className="flex gap-2 lg:justify-end">
            <Button type="button" variant="outline" className="flex-1 lg:flex-none" onClick={() => setSheetOpen(false)}>
              取消
            </Button>
            <Button
              type="button"
              className="flex-1 lg:flex-none"
              loading={bind.isPending}
              disabled={!canSubmitManual}
              onClick={() => bind.mutate()}
            >
              {bind.isPending ? '正在绑定…' : '绑定'}
            </Button>
          </div>
        )}
      >
        <div className="space-y-4 pt-1">
          <Field
            label="cred"
            htmlFor="skland-cred"
            labelAside={<span className="text-footnote text-muted-foreground">必填</span>}
            description="森空岛登录凭据，等同账号凭证；只保存在本机。"
          >
            <Input
              id="skland-cred"
              value={cred}
              autoComplete="off"
              placeholder="粘贴 cred"
              onChange={(event) => setCred(event.target.value)}
            />
          </Field>
          <Field
            label="token"
            htmlFor="skland-token"
            labelAside={<span className="text-footnote text-muted-foreground">必填</span>}
            description="随 cred 一起下发的签名密钥。"
          >
            <Input
              id="skland-token"
              value={token}
              autoComplete="off"
              placeholder="粘贴 token"
              onChange={(event) => setToken(event.target.value)}
            />
          </Field>
          {bind.error ? (
            <InlineAlert tone="danger" title="绑定失败">{errorText(bind.error)}</InlineAlert>
          ) : null}
        </div>
      </ResponsiveSheet>

      {confirmElement}
    </section>
  )
}

export default SklandBindingCard
