import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import {
  SKLAND_BOUND,
  SKLAND_KEY_MISSING,
  SKLAND_QR_EXPIRED,
  SKLAND_QR_FAILED,
  SKLAND_QR_SESSION,
  SKLAND_QR_SUCCEEDED,
  SKLAND_QR_WAITING,
  SKLAND_ROLE,
  SKLAND_SYNC_SUMMARY,
  installAccountApi,
  jsonResponse,
  operatorRow,
  renderAccount,
} from './test-utils'
import {
  DEFAULT_SKLAND_POLL_INTERVAL_MS,
  bindSklandManual,
  fetchSklandStatus,
  normalizeSklandQrPoll,
  normalizeSklandQrSession,
  normalizeSklandQrState,
  normalizeSklandStatus,
  normalizeSklandSyncSummary,
  pollSklandQrLogin,
  retryAfterSeconds,
  startSklandQrLogin,
  syncSkland,
  unbindSkland,
} from './api'
import { errorText } from './format'
import { qrExpired, qrRemainingText, sklandSyncSummaryLines } from './SklandBindingCard'

/**
 * M42「森空岛练度」卡片的用例（规格 §4.6、§4.8）。
 *
 * 卡片挂在干员页 `<RecognitionStatus>` 下方，所以渲染走 `renderAccount`；假后端
 * `installAccountApi` 把 `/api/skland` 六个端点一起接上了。时间相关的用例统一开假计时器
 * （`shouldAdvanceTime: true`，照 `web/src/features/tasks/queue.test.tsx:113`）：轮询与倒计时
 * 都要推进时间才动，不推进又不能让 `findBy` 卡死。
 */

/** 卡片外层 `Section` 的 `aria-labelledby` 指向标题，所以它本身就是一个有名的 region。 */
const card = () => screen.findByRole('region', { name: '森空岛练度' })

const QR_POLL_PATH = '/api/skland/login/qrcode/qr-1'

/** 用假计时器渲染页面的公共头：等首屏（干员列表 + 绑定状态）都落地再交给用例。 */
async function renderWithFakeTimers(options?: Parameters<typeof installAccountApi>[0]) {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const api = installAccountApi(options)
  renderAccount('/more/account')
  expect(await card()).toBeInTheDocument()
  await waitFor(() => expect(api.find('GET', '/api/skland/status')).toHaveLength(1))
  return api
}

describe('M42 森空岛绑定卡片', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('未绑定：加载时读状态、自动生成二维码，src 是 data:image/svg+xml 的 data URI', async () => {
    const api = installAccountApi()
    renderAccount('/more/account')

    const region = await card()
    const img = await within(region).findByAltText('森空岛登录二维码')
    const src = img.getAttribute('src') ?? ''
    expect(src.startsWith('data:image/svg+xml')).toBe(true)
    // SVG 走 data URI（`encodeURIComponent`），不是 `dangerouslySetInnerHTML`。
    expect(decodeURIComponent(src.split(',').slice(1).join(','))).toContain('<svg')
    expect(within(region).getByText('用森空岛 App 扫码，确认后回到这一页。')).toBeInTheDocument()
    expect(within(region).getByTestId('skland-qr-remaining')).toHaveTextContent(/二维码 \d+:\d{2} 后失效/)
    expect(within(region).getByRole('button', { name: '手动输入 cred' })).toBeInTheDocument()

    await waitFor(() => expect(api.find('POST', '/api/skland/login/qrcode')).toHaveLength(1))
    expect(api.find('GET', '/api/skland/status')).toHaveLength(1)
    expect(api.find('GET', QR_POLL_PATH)).toHaveLength(1)
  })

  it('waiting：按响应里的 poll_interval_ms 继续轮询，成功之前不刷新状态', async () => {
    const api = await renderWithFakeTimers({ sklandQr: { poll_interval_ms: 1_000 } })
    await waitFor(() => expect(api.find('GET', QR_POLL_PATH)).toHaveLength(1))
    const before = api.find('GET', QR_POLL_PATH).length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_200)
    })

    await waitFor(() => expect(api.find('GET', QR_POLL_PATH).length).toBeGreaterThan(before))
    // 还是 `waiting`：不假装已绑定。
    expect(api.find('GET', '/api/skland/status')).toHaveLength(1)
  })

  it('waiting 且带上游文案（已扫码待确认）时把它显示出来，且仍继续轮询', async () => {
    // 上游在用户扫了码、还没在 App 里确认时回 `status=101`：后端归一成 waiting + message。
    const api = await renderWithFakeTimers({
      sklandQr: { poll_interval_ms: 1_000 },
      sklandQrPoll: { state: 'waiting', message: '已扫码待确认' },
    })

    const region = await card()
    expect(await within(region).findByRole('status')).toHaveTextContent('已扫码待确认')

    const before = api.find('GET', QR_POLL_PATH).length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_200)
    })
    await waitFor(() => expect(api.find('GET', QR_POLL_PATH).length).toBeGreaterThan(before))
  })

  it('succeeded：刷新状态，换成已绑定视图并停止轮询', async () => {
    const api = await renderWithFakeTimers({
      sklandQrPoll: SKLAND_QR_SUCCEEDED,
      sklandAfterBind: { bound: true, account: SKLAND_ROLE, roles: [SKLAND_ROLE] },
    })

    const region = await card()
    expect(await within(region).findByTestId('skland-account')).toHaveTextContent('罗德岛博士')
    expect(within(region).queryByAltText('森空岛登录二维码')).not.toBeInTheDocument()
    await waitFor(() => expect(api.find('GET', '/api/skland/status').length).toBeGreaterThan(1))

    const polls = api.find('GET', QR_POLL_PATH).length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(api.find('GET', QR_POLL_PATH)).toHaveLength(polls)
  })

  it('expired：就地提示并给「重新生成二维码」，点了会再生成一张并继续轮询', async () => {
    // 轮询响应固定回 `expired`：换一张之后必须能重新问起来（否则新码扫了也不会被读到）。
    const api = await renderWithFakeTimers({ sklandQrPoll: SKLAND_QR_EXPIRED })

    const region = await card()
    expect(await within(region).findByText('二维码已失效')).toBeInTheDocument()
    expect(within(region).queryByAltText('森空岛登录二维码')).not.toBeInTheDocument()
    expect(api.find('GET', QR_POLL_PATH)).toHaveLength(1)

    fireEvent.click(within(region).getByRole('button', { name: '重新生成二维码' }))

    await waitFor(() => expect(api.find('POST', '/api/skland/login/qrcode')).toHaveLength(2))
    await waitFor(() => expect(api.find('GET', QR_POLL_PATH).length).toBeGreaterThan(1))
  })

  it('failed：显示后端 message 与重新生成入口，且不再轮询', async () => {
    const api = await renderWithFakeTimers({ sklandQrPoll: SKLAND_QR_FAILED })
    await waitFor(() => expect(api.find('GET', QR_POLL_PATH)).toHaveLength(1))

    const region = await card()
    expect(await within(region).findByText('扫码登录失败')).toBeInTheDocument()
    expect(within(region).getByText('森空岛拒绝了这次登录')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: '重新生成二维码' })).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(api.find('GET', QR_POLL_PATH)).toHaveLength(1)
  })

  it('本地倒计时走到 0 也给出口（后端还没来得及回 expired）', async () => {
    await renderWithFakeTimers({ sklandQr: { expires_at: new Date(Date.now() - 1_000).toISOString() } })

    const region = await card()
    expect(await within(region).findByText('二维码已失效')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: '重新生成二维码' })).toBeInTheDocument()
  })

  it('key_configured=false：不显示二维码，给「先在 config.yaml 配置」与生成命令', async () => {
    const api = installAccountApi({ skland: SKLAND_KEY_MISSING })
    renderAccount('/more/account')

    const region = await card()
    const alert = await within(region).findByRole('alert')
    expect(alert).toHaveTextContent('还没有配凭据加密密钥')
    expect(alert).toHaveTextContent('先在 config.yaml 里配 skland.credential_key')
    expect(within(region).getByTestId('skland-key-command')).toHaveTextContent('Fernet.generate_key')
    expect(within(region).queryByAltText('森空岛登录二维码')).not.toBeInTheDocument()
    // 密钥没配好之前后端不会接受绑定，手动入口也不给。
    expect(within(region).queryByRole('button', { name: '手动输入 cred' })).not.toBeInTheDocument()
    expect(api.find('POST', '/api/skland/login/qrcode')).toHaveLength(0)
  })

  it('手动输入 cred：两项都非空才能提交，成功后回到已绑定视图', async () => {
    const api = await renderWithFakeTimers({
      sklandAfterBind: { bound: true, account: SKLAND_ROLE, roles: [SKLAND_ROLE] },
    })

    fireEvent.click(within(await card()).getByRole('button', { name: '手动输入 cred' }))
    const sheet = await screen.findByRole('dialog')
    const submit = within(sheet).getByRole('button', { name: '绑定' })
    expect(submit).toBeDisabled()

    fireEvent.change(within(sheet).getByLabelText('cred'), { target: { value: '  cred-abc  ' } })
    expect(submit).toBeDisabled()
    fireEvent.change(within(sheet).getByLabelText('token'), { target: { value: 'token-xyz' } })
    expect(submit).toBeEnabled()

    fireEvent.click(submit)

    await waitFor(() => expect(api.find('POST', '/api/skland/login/manual')).toHaveLength(1))
    // 前后空白去掉再提交。
    expect(api.find('POST', '/api/skland/login/manual')[0].body).toEqual({ cred: 'cred-abc', token: 'token-xyz' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await within(await card()).findByTestId('skland-account')).toHaveTextContent('罗德岛博士')
  })

  it('手动输入 cred 失败（401）时在弹层里就地报错', async () => {
    await renderWithFakeTimers({ sklandManualStatus: 401 })

    fireEvent.click(within(await card()).getByRole('button', { name: '手动输入 cred' }))
    const sheet = await screen.findByRole('dialog')
    fireEvent.change(within(sheet).getByLabelText('cred'), { target: { value: 'bad' } })
    fireEvent.change(within(sheet).getByLabelText('token'), { target: { value: 'bad' } })
    fireEvent.click(within(sheet).getByRole('button', { name: '绑定' }))

    expect(await within(sheet).findByText('凭据校验失败，请重新获取 cred 与 token')).toBeInTheDocument()
    expect(sheet).toBeInTheDocument()
  })

  it('已绑定：显示账号、上次同步摘要（只提非零项）与两个按钮', async () => {
    await renderWithFakeTimers({ skland: SKLAND_BOUND })

    const region = await card()
    expect(await within(region).findByTestId('skland-account')).toHaveTextContent('罗德岛博士 · 114 级 · 共 2 个角色')
    expect(within(region).getByText(/^上次同步 \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)).toBeInTheDocument()
    expect(within(region).getByText('本轮 383 名：新增 12 · 更新 47 · 未变 324')).toBeInTheDocument()
    expect(within(region).getByText('清掉未拥有 3 名')).toBeInTheDocument()
    expect(within(region).getByText('耗时 0.8 秒')).toBeInTheDocument()
    // `dropped_invalid` 为 0 时不提（§4.8）。
    expect(region).not.toHaveTextContent('丢弃越界值')
    expect(within(region).getByRole('button', { name: '同步练度' })).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: '解绑' })).toBeInTheDocument()
    expect(within(region).queryByAltText('森空岛登录二维码')).not.toBeInTheDocument()
  })

  it('已绑定：点「同步练度」按钮走 loading，完成后按新摘要刷新', async () => {
    const api = await renderWithFakeTimers({
      skland: SKLAND_BOUND,
      // 同步的响应先挂住：这样「进行中」这一段是确定的，不是抢在请求回来之前断言。
      holdSklandSync: true,
      sklandSyncSummary: { created: 20, updated: 0, unchanged: 300, ownership_cleared: 0, duration_ms: 1_500 },
    })

    const region = await card()
    const button = await within(region).findByRole('button', { name: '同步练度' })
    fireEvent.click(button)

    await waitFor(() => expect(api.find('POST', '/api/skland/sync')).toHaveLength(1))
    // 请求还没回来：按钮处于 loading（`aria-busy`），文案也换成进行中。
    await waitFor(() => expect(button).toHaveAttribute('aria-busy', 'true'))
    expect(within(region).getByRole('button', { name: '正在同步…' })).toBeInTheDocument()

    api.releaseSklandSync()

    await waitFor(() => expect(button).not.toHaveAttribute('aria-busy'))
    expect(await within(region).findByText('本轮 383 名：新增 20 · 未变 300')).toBeInTheDocument()
    expect(within(region).getByText('耗时 1.5 秒')).toBeInTheDocument()
  })

  it('同步被 429 拒绝：显示后端文案与「还要等 N 秒」', async () => {
    const api = await renderWithFakeTimers({
      skland: SKLAND_BOUND,
      sklandSyncStatus: 429,
      sklandSyncRetryAfter: 42,
    })

    fireEvent.click(await within(await card()).findByRole('button', { name: '同步练度' }))

    await waitFor(() => expect(api.find('POST', '/api/skland/sync')).toHaveLength(1))
    const region = await card()
    expect(await within(region).findByRole('alert')).toHaveTextContent('同步太频繁，请稍后再试（还要等 42 秒）')
  })

  it('解绑：先确认，「再想想」不发请求，确认后回到未绑定并重新生成二维码', async () => {
    const api = await renderWithFakeTimers({ skland: SKLAND_BOUND })
    const region = await card()

    fireEvent.click(await within(region).findByRole('button', { name: '解绑' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent('会删掉保存在本机的 cred 与签名 token，已同步到账号数据的练度保留')
    expect(api.find('DELETE', '/api/skland/binding')).toHaveLength(0)

    fireEvent.click(within(dialog).getByRole('button', { name: '再想想' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(api.find('DELETE', '/api/skland/binding')).toHaveLength(0)

    fireEvent.click(within(region).getByRole('button', { name: '解绑' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '解绑' }))

    await waitFor(() => expect(api.find('DELETE', '/api/skland/binding')).toHaveLength(1))
    await waitFor(() => expect(api.find('POST', '/api/skland/login/qrcode')).toHaveLength(1))
    expect(await within(region).findByAltText('森空岛登录二维码')).toBeInTheDocument()
  })

  it('读不到绑定状态时就地报错并给重试，不渲染二维码', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => jsonResponse({ error: { code: 'INTERNAL_ERROR', message: '服务出错' } }, 500)))
    renderAccount('/more/account')

    const region = await card()
    expect(await within(region).findByText('读取绑定状态失败')).toBeInTheDocument()
    expect(within(region).getByText('服务出错')).toBeInTheDocument()
    expect(within(region).queryByAltText('森空岛登录二维码')).not.toBeInTheDocument()
  })
})

describe('M42 干员列表的森空岛来源徽章', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('field_sources 带 skland 时行内显示「森空岛」，提示写明是同步写进来的', async () => {
    installAccountApi({
      skland: SKLAND_BOUND,
      operators: [
        operatorRow({
          char_id: 'char_002_amiya',
          name: '阿米娅',
          field_sources: { own: 'recognition', elite: 'recognition', level: 'skland', skill_level: 'skland', masteries: 'skland', modules: 'skland' },
        }),
      ],
    })
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    const badge = await screen.findByTestId('operator-source-char_002_amiya-skland')
    expect(badge).toHaveTextContent('森空岛')
    expect(within(badge).getByTitle('由森空岛账号数据同步写入')).toBeInTheDocument()
  })
})

describe('M42 文案与响应兜底', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('同步摘要只提非零项；全零时写「没有改动」', () => {
    const empty = {
      ...SKLAND_SYNC_SUMMARY,
      created: 0,
      updated: 0,
      unchanged: 0,
      ownership_cleared: 0,
      dropped_invalid: 0,
      duration_ms: 0,
    }
    expect(sklandSyncSummaryLines(empty)).toEqual(['本轮 383 名：没有改动'])
    expect(sklandSyncSummaryLines({ ...empty, created: 1, updated: 2, ownership_cleared: 3 })).toEqual([
      '本轮 383 名：新增 1 · 更新 2',
      '清掉未拥有 3 名',
    ])
  })

  it('二维码倒计时与本地到期判断：读不出来就不假装有', () => {
    const now = Date.parse('2026-10-12T03:00:00Z')
    expect(qrRemainingText('2026-10-12T03:02:57Z', now)).toBe('2:57')
    expect(qrRemainingText('2026-10-12T02:59:00Z', now)).toBe('0:00')
    expect(qrRemainingText(null, now)).toBeNull()
    expect(qrRemainingText('not-a-date', now)).toBeNull()
    expect(qrExpired('2026-10-12T02:59:59Z', now)).toBe(true)
    expect(qrExpired('2026-10-12T03:00:01Z', now)).toBe(false)
    expect(qrExpired(null, now)).toBe(false)
    expect(qrExpired('not-a-date', now)).toBe(false)
  })

  it('状态响应读不出来时落安全默认：不假装已绑定、不假装没配密钥', () => {
    expect(normalizeSklandStatus(null)).toMatchObject({
      bound: false,
      key_configured: true,
      account: null,
      roles: [],
      last_sync: null,
    })
    expect(normalizeSklandStatus({ bound: 'yes', key_configured: 0 })).toMatchObject({ bound: false, key_configured: true })
    expect(normalizeSklandStatus({ bound: true, key_configured: false }).key_configured).toBe(false)
    // 角色缺 `uid` 就当不存在；数字 `uid` 转成字符串。
    expect(normalizeSklandStatus({ roles: [{ nickname: '没有 uid' }, { uid: 1001 }] }).roles).toEqual([
      { uid: '1001', app_code: '', channel_master_id: null, nickname: null, level: null, is_default: false, synced_at: null },
    ])
  })

  it('扫码状态与摘要兜底：状态认不出落 failed，摘要不像摘要给 null', () => {
    expect(normalizeSklandQrState('waiting')).toBe('waiting')
    expect(normalizeSklandQrState('weird')).toBe('failed')
    expect(normalizeSklandQrState(undefined)).toBe('failed')
    expect(normalizeSklandQrPoll({}).state).toBe('failed')
    expect(normalizeSklandQrPoll({ state: 'waiting' })).toMatchObject({ message: null, account: null, roles: [] })

    expect(normalizeSklandQrSession({}).poll_interval_ms).toBe(DEFAULT_SKLAND_POLL_INTERVAL_MS)
    expect(normalizeSklandQrSession({}).session_id).toBe('')
    expect(normalizeSklandQrSession({ poll_interval_ms: 0 }).poll_interval_ms).toBe(DEFAULT_SKLAND_POLL_INTERVAL_MS)
    expect(normalizeSklandQrSession({ poll_interval_ms: 500 }).poll_interval_ms).toBe(500)

    expect(normalizeSklandSyncSummary({ status: 'succeeded', total: 3 })).toMatchObject({ total: 3, created: 0, duration_ms: 0 })
    expect(normalizeSklandSyncSummary({})).toBeNull()
    expect(normalizeSklandSyncSummary(null)).toBeNull()
  })

  it('请求层按契约打六个端点（独立于假后端）', async () => {
    const calls: string[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init)
      const url = new URL(request.url)
      calls.push(`${request.method} ${url.pathname}`)
      if (url.pathname === '/api/skland/status') return jsonResponse(SKLAND_BOUND)
      if (url.pathname === '/api/skland/login/qrcode') return jsonResponse(SKLAND_QR_SESSION)
      if (url.pathname === QR_POLL_PATH) return jsonResponse(SKLAND_QR_WAITING)
      if (url.pathname === '/api/skland/login/manual') return jsonResponse(SKLAND_BOUND)
      if (url.pathname === '/api/skland/sync') return jsonResponse(SKLAND_SYNC_SUMMARY)
      if (url.pathname === '/api/skland/binding') return jsonResponse({ bound: false })
      return jsonResponse({ error: { code: 'NOT_FOUND', message: `Unexpected ${url.pathname}` } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    expect(await fetchSklandStatus()).toMatchObject({ bound: true, account: { nickname: '罗德岛博士', level: 114 } })
    expect(await startSklandQrLogin()).toMatchObject({ session_id: 'qr-1', poll_interval_ms: 2_000 })
    expect(await pollSklandQrLogin('qr-1')).toMatchObject({ state: 'waiting', roles: [] })
    expect(await bindSklandManual({ cred: 'c', token: 't' })).toMatchObject({ bound: true })
    expect(await syncSkland()).toMatchObject({ status: 'succeeded', total: 383, created: 12 })
    expect(await unbindSkland()).toBe(false)

    expect(calls).toEqual([
      'GET /api/skland/status',
      'POST /api/skland/login/qrcode',
      'GET /api/skland/login/qrcode/qr-1',
      'POST /api/skland/login/manual',
      'POST /api/skland/sync',
      'DELETE /api/skland/binding',
    ])
  })

  it('同步 429 时把 Retry-After 带进错误里', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () =>
      jsonResponse({ error: { code: 'RATE_LIMITED', message: '同步太频繁' } }, 429, { 'Retry-After': '42' })))

    const failure = await syncSkland().catch((error: unknown) => error)
    expect(retryAfterSeconds(failure)).toBe(42)
    expect(errorText(failure)).toBe('同步太频繁')
    // 不是 429 的错误没有这个秒数。
    expect(retryAfterSeconds(new Error('boom'))).toBeNull()
  })
})
