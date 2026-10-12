import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { Section } from '@/components/ui/section'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { Spinner } from '@/components/ui/states'
import {
  DEFAULT_SKLAND_POLL_INTERVAL_MS,
  accountKeys,
  bindSklandManual,
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
import { errorText, formatDateTime } from './format'

/**
 * 「森空岛练度」卡片（M42 规格 §4.8）：扫码绑定森空岛账号，再从这里同步官方练度数据。
 *
 * 这一块是 M36「练度扫描」（读干员详情页）的替代品：数据来自森空岛而不是读屏，所以卡片
 * 只有「绑定 → 同步」两件事，没有扫描那套状态机（同步是**同步执行**的 `POST`，秒级，
 * 等待期间按钮走 `loading`）。技能等级 / 专精 / 模组写进 `account_operator` 后，
 * 干员列表的字段来源徽章会显示「森空岛」（`FieldSource = 'skland'`）。
 *
 * 三条渲染路径：没配 `skland.credential_key`（连二维码都不给，直接说怎么配）、
 * 未绑定（二维码 + 轮询 + 手动 cred 兜底）、已绑定（账号、上次同步摘要、同步与解绑）。
 */

/**
 * 没配 `skland.credential_key` 时给用户的生成命令（§4.4）：与后端「密钥缺失时回 409」
 * 配套，在这一页就能看到要做什么，不用去翻文档。密钥只写进 `config.yaml`，不走设置 API。
 */
const KEY_COMMAND = '.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'

/** 二维码剩余有效期的倒计时：`2:57`；`expires_at` 读不出来给 `null`（不假装有倒计时）。 */
export function qrRemainingText(expiresAt: string | null | undefined, now: number): string | null {
  if (!expiresAt) return null
  const deadline = new Date(expiresAt).getTime()
  if (Number.isNaN(deadline)) return null
  const remaining = Math.max(0, Math.ceil((deadline - now) / 1000))
  return `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`
}

/** 本地倒计时是否已经走到 0（后端还没来得及把状态改成 `expired` 时也要给出口）。 */
export function qrExpired(expiresAt: string | null | undefined, now: number): boolean {
  if (!expiresAt) return false
  const deadline = new Date(expiresAt).getTime()
  return !Number.isNaN(deadline) && deadline <= now
}

/** 角色的显示名：昵称优先，缺了退回 `uid`（`uid` 一定有，兜底里保证过）。 */
export function sklandRoleText(role: SklandRole): string {
  return role.nickname ?? role.uid
}

/** 已绑定那一行：昵称 · 等级 · 角色数；读不出的项就不提。 */
export function sklandAccountLine(account: SklandRole | null, roles: SklandRole[]): string {
  const name = account ? sklandRoleText(account) : '已绑定'
  const level = typeof account?.level === 'number' ? ` · ${account.level} 级` : ''
  return `${name}${level} · 共 ${roles.length} 个角色`
}

/** 上次同步时间：没有就写「还没有同步过」，不写一个空时间。 */
export function sklandLastSyncText(lastSyncAt: string | null | undefined): string {
  const at = formatDateTime(lastSyncAt)
  return at ? `上次同步 ${at}` : '还没有同步过'
}

/**
 * 上次同步摘要：**只提非零项**（§4.8）。全零时写「没有改动」，不排一行 0；
 * `duration_ms` 单独一行（有耗时才算）。
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
    summary.diffs > 0 ? `记下差异 ${summary.diffs} 处` : null,
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

/** 二维码这一块要就地显示的问题；没有问题时返回 `null`（正常渲染二维码或等待态）。 */
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
  // 倒计时用的「现在」：只在二维码显示着的时候每秒走一格（见下面的 effect）。
  const [now, setNow] = useState(() => Date.now())

  const status = useQuery({
    queryKey: accountKeys.sklandStatus(),
    queryFn: ({ signal }) => fetchSklandStatus(signal),
    retry: false,
  })

  const data = status.data
  const bound = data?.bound === true
  const keyConfigured = data?.key_configured !== false
  const needsQr = Boolean(data) && keyConfigured && !bound

  /**
   * 二维码：未绑定时自动生成一张（`POST /api/skland/login/qrcode`）。
   *
   * 用 `useQuery` 而不是 `useMutation`：同一张二维码只该生成一次，react-query 的键去重与
   * 缓存正好是这个语义；「重新生成二维码」＝ `regenerateQr()`。`gcTime: 0` 保证离开这一页
   * 再回来时是**新**二维码，而不是上次那张已经过期的。
   */
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
    // 只在等扫码的时候轮询（`waiting` 或第一次响应还没回来），其余状态一律停掉；
    // 周期用响应给的 `poll_interval_ms`，不自己定一个。
    refetchInterval: (query) => {
      const state = query.state.data?.state
      const active = needsQr
        && Boolean(sessionId)
        && !query.state.error
        && (state === undefined || state === 'waiting')
      return active ? pollIntervalMs : false
    },
  })

  // 换了会话就把倒计时的基准时间对齐一次（否则先渲染的旧 `now` 会让倒计时偏长）。
  useEffect(() => {
    if (!sessionId) return
    setNow(Date.now())
  }, [sessionId])

  const qrState = poll.data?.state ?? 'waiting'
  const qrVisible = Boolean(sessionId) && needsQr && qrState !== 'succeeded'

  useEffect(() => {
    if (!qrVisible) return
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [qrVisible])

  // 扫码成功：把状态重取一次，绑定视图自己会顶上来（轮询这时已经停了）。
  const qrSucceeded = poll.data?.state === 'succeeded'
  useEffect(() => {
    if (!qrSucceeded) return
    void queryClient.invalidateQueries({ queryKey: accountKeys.sklandStatus() })
  }, [qrSucceeded, queryClient])

  const invalidateStatus = () => queryClient.invalidateQueries({ queryKey: accountKeys.sklandStatus() })

  const sync = useMutation({
    mutationFn: () => syncSkland(),
    onSuccess: async () => {
      await invalidateStatus()
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
      // 解绑后再回到未绑定：丢掉缓存里的旧二维码，让卡片重新生成一张。
      queryClient.removeQueries({ queryKey: accountKeys.sklandQr() })
      await invalidateStatus()
    },
  })

  const requestUnbind = async () => {
    const approved = await confirm({
      title: '解绑森空岛账号？',
      description:
        '会删掉保存在本机的 cred 与签名 token，已同步到账号数据的练度保留；需要时重新扫码绑定即可。',
      confirmText: '解绑',
      cancelText: '再想想',
      tone: 'danger',
    })
    if (!approved) return
    unbind.mutate()
  }

  const expired = qrExpired(session?.expires_at, now)
  const remaining = qrRemainingText(session?.expires_at, now)

  /**
   * 重新生成一张二维码：先把上一张的轮询结果丢掉。
   *
   * 后端的会话 id 每次都是新的（键自然换掉），但旧结果留着的话，万一会话 id 复用，
   * 新二维码会直接卡在上一张的 `expired` / `failed` 上——那时轮询已经停了，扫也白扫。
   */
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
    <>
      <InlineAlert tone="warning" title="还没有配凭据加密密钥">
        先在 <code>config.yaml</code> 里配 <code>skland.credential_key</code>，再回到这一页扫码绑定；
        密钥没配好之前，后端不会接受绑定请求。
      </InlineAlert>
      <p className="text-footnote text-muted-foreground">生成一个密钥（写进 <code>config.yaml</code> 的 <code>skland.credential_key</code>）：</p>
      <code data-testid="skland-key-command" className="block overflow-x-auto rounded-lg bg-surface-2 px-2.5 py-2 text-footnote">
        {KEY_COMMAND}
      </code>
    </>
  ) : bound ? (
    <>
      <p data-testid="skland-account" className="text-[0.9375rem] font-medium">
        {sklandAccountLine(data.account, data.roles)}
      </p>
      {data.roles.length > 1 ? (
        <p className="text-footnote text-muted-foreground">
          {data.roles.map((role) => (role.is_default ? `${sklandRoleText(role)}（默认）` : sklandRoleText(role))).join('、')}
        </p>
      ) : null}
      <p className="text-footnote tabular-nums text-muted-foreground">{sklandLastSyncText(data.last_sync_at)}</p>
      {data.last_sync
        ? sklandSyncSummaryLines(data.last_sync).map((line) => (
          <p key={line} className="text-footnote tabular-nums text-muted-foreground">{line}</p>
        ))
        : null}

      {data.last_error ? (
        <InlineAlert tone="warning" title="上次同步失败">{data.last_error}</InlineAlert>
      ) : null}

      {/* 同步 / 解绑的失败就地显示，不吞掉：409 忙、429 冷却（带 `Retry-After`）都走这里。 */}
      {sync.error ? (
        <InlineAlert tone="danger" title="同步失败">
          {errorText(sync.error)}{retrySeconds ? `（还要等 ${retrySeconds} 秒）` : ''}
        </InlineAlert>
      ) : null}
      {unbind.error ? (
        <InlineAlert tone="danger" title="解绑失败">{errorText(unbind.error)}</InlineAlert>
      ) : null}

      <p className="text-footnote text-muted-foreground">{syncHint(data)}</p>
    </>
  ) : (
    <>
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
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner />
          正在生成二维码…
        </p>
      ) : qrState === 'succeeded' ? (
        <p role="status" className="text-sm">已扫码，正在读取账号数据…</p>
      ) : (
        <div className="flex flex-wrap items-start gap-4">
          {/* `qr_svg` 是后端生成的完整 SVG：走 data URI，**不用** `dangerouslySetInnerHTML`。 */}
          <img
            // 白底是为了深色主题下也扫得出来（SVG 自己可能是透明底）。
            className="size-40 shrink-0 rounded-lg border bg-white p-1.5"
            src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(session.qr_svg)}`}
            alt="森空岛登录二维码"
          />
          <div className="min-w-0 space-y-1.5 text-footnote text-muted-foreground">
            <p className="text-sm text-foreground">用森空岛 App 扫码，确认后回到这一页。</p>
            {remaining ? (
              <p data-testid="skland-qr-remaining" className="tabular-nums">二维码 {remaining} 后失效</p>
            ) : null}
            <p>cred 与 token 只保存在本机；随时可以解绑。</p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t pt-2">
        <Button variant="outline" size="sm" onClick={() => setSheetOpen(true)}>手动输入 cred</Button>
        <span className="text-footnote text-muted-foreground">扫码不可用时，可以粘贴 cred 与签名 token 绑定。</span>
      </div>
    </>
  )

  return (
    <Section
      title="森空岛练度"
      id="skland-binding"
      description="用森空岛账号同步技能等级、专精、模组与信赖；不占设备、不读屏。"
      actions={bound ? (
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="soft"
            size="sm"
            className="shrink-0"
            loading={sync.isPending}
            onClick={() => sync.mutate()}
          >
            {sync.isPending ? '正在同步…' : '同步练度'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            loading={unbind.isPending}
            onClick={() => void requestUnbind()}
          >
            {unbind.isPending ? '正在解绑…' : '解绑'}
          </Button>
        </div>
      ) : undefined}
    >
      <div className="space-y-2 rounded-xl border bg-card p-3.5 shadow-card">{body}</div>

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
    </Section>
  )
}

export default SklandBindingCard
