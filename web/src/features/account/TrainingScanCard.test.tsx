import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import {
  IDLE_TRAINING_SCAN,
  RUNNING_FULL_TRAINING_SCAN,
  RUNNING_TRAINING_SCAN,
  TRAINING_SCAN_SUBMIT_FULL,
  installAccountApi,
  jsonResponse,
  renderAccount,
} from './test-utils'
import {
  fetchTrainingScan,
  normalizeTrainingScanState,
  normalizeTrainingScanSubmit,
  startTrainingScan,
} from './api'
import { scannedText, trainingScanSummary } from './TrainingScanCard'

/**
 * M36「练度扫描」卡片的用例（规格 §4.6、§4.8）。
 *
 * 卡片挂在干员页 `<RecognitionStatus>` 下方，所以渲染走 `renderAccount`（假后端
 * `installAccountApi` 也把 `/api/account/training-scan` 的三个方法一起接上了）。
 * 时间相关的用例统一开假计时器（`shouldAdvanceTime: true`，照
 * `web/src/features/tasks/queue.test.tsx:113`）：轮询周期是 5 s，不推进时间就等着
 * 也不算数，推进时间又不能让 `findBy` 卡死。
 */

/** 卡片外层 `Section` 的 `aria-labelledby` 指向标题，所以它本身就是一个有名的 region。 */
const card = () => screen.findByRole('region', { name: '练度扫描' })

const scanRequests = (find: ReturnType<typeof installAccountApi>['find'], method: string) =>
  find(method, '/api/account/training-scan')

/** 用假计时器渲染页面的公共头：等首屏（干员列表 + 扫描状态）都落地再交给用例。 */
async function renderWithFakeTimers(options?: Parameters<typeof installAccountApi>[0]) {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const api = installAccountApi(options)
  renderAccount('/more/account')
  expect(await card()).toBeInTheDocument()
  await waitFor(() => expect(scanRequests(api.find, 'GET')).toHaveLength(1))
  return api
}

describe('M36 练度扫描卡片', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('idle：显示「开始扫描」与说明文案，点击后提交 POST', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account')

    expect(await card()).toHaveTextContent('读干员详情页的技能等级、专精与模组；只读界面，不消耗资源。')
    expect(within(await card()).getByText('尚未扫描过')).toBeInTheDocument()
    expect(scanRequests(find, 'GET')).toHaveLength(1)

    fireEvent.click(within(await card()).getByRole('button', { name: '开始扫描' }))

    await waitFor(() => expect(scanRequests(find, 'POST')).toHaveLength(1))
    // 不传任何参数 ＝ 后端用默认上限（§4.3：默认 40，从精二干员里取）。
    expect(scanRequests(find, 'POST')[0].body).toEqual({})
  })

  it('running：显示进度与「已读 x / y」，点击「取消」提交 DELETE', async () => {
    const api = await renderWithFakeTimers({ trainingScan: RUNNING_TRAINING_SCAN })
    const region = await card()

    expect(within(region).getByTestId('training-scan-status')).toHaveTextContent('进行中')
    // 裸文本节点被 `<span>` 包着，`toHaveTextContent` 会跨元素拼空格，所以按整体匹配。
    expect(region).toHaveTextContent('已读 12 / 40')
    expect(within(region).getByRole('progressbar', { name: '练度扫描进度' })).toHaveAttribute('aria-valuenow', '12')
    expect(within(region).getByRole('button', { name: '开始扫描' })).toBeDisabled()

    fireEvent.click(within(region).getByRole('button', { name: '取消' }))

    await waitFor(() => expect(scanRequests(api.find, 'DELETE')).toHaveLength(1))
    // 取消后回的还是同一份状态，页面不自己编「已取消」——以服务端状态为准。
    await waitFor(() => expect(within(region).getByTestId('training-scan-status')).toHaveTextContent('进行中'))
  })

  it('submitted：受理成功后，轮询没带 total 就用受理结果里的人数显示进度', async () => {
    // 受理前是 idle（按钮可点），受理成功后 `GET` 回 running 但**不带** `total`：
    // 这时「已读 12 / 40」里的 40 只能来自 `POST` 受理结果。
    const api = await renderWithFakeTimers({
      trainingScanAfterStart: { ...RUNNING_TRAINING_SCAN, total: null },
    })

    fireEvent.click(within(await card()).getByRole('button', { name: '开始扫描' }))

    await waitFor(() => expect(scanRequests(api.find, 'POST')).toHaveLength(1))
    await waitFor(async () => expect(await card()).toHaveTextContent('已读 12 / 40'))
  })

  it('succeeded：显示写入数与跳过摘要，没有跳过的项就不提', async () => {
    await renderWithFakeTimers({
      trainingScan: {
        ...RUNNING_TRAINING_SCAN,
        status: 'succeeded',
        finished_at: '2026-10-11T01:04:00Z',
        scanned: 40,
        updated: 37,
        skipped: 2,
        unresolved: 1,
        mismatched: 0,
      },
    })

    const region = await card()
    expect(within(region).getByTestId('training-scan-status')).toHaveTextContent('已完成')
    expect(region).toHaveTextContent('写入 37 名 / 共读 40 名')
    expect(region).toHaveTextContent('跳过 2 名 · 名单未解析 1 名')
    // `mismatched` 为 0 时不提（§4.8）。
    expect(region).not.toHaveTextContent('卡名不符')
  })

  it('failed：用 InlineAlert 显示后端给的 message', async () => {
    await renderWithFakeTimers({
      trainingScan: {
        ...IDLE_TRAINING_SCAN,
        status: 'failed',
        scanned: 5,
        updated: 5,
        message: '内核断开，扫描中断',
      },
    })

    const region = await card()
    expect(within(region).getByTestId('training-scan-status')).toHaveTextContent('失败')
    // 假后端没接养成计划，页面别处也会有一条 `alert`，所以这里只认卡片里的那条。
    expect(await within(region).findByRole('alert')).toHaveTextContent('内核断开，扫描中断')
  })

  it('POST 被 409 拒绝时就地显示后端的错误文案', async () => {
    const api = await renderWithFakeTimers({ trainingScanStartStatus: 409 })

    fireEvent.click(within(await card()).getByRole('button', { name: '开始扫描' }))

    await waitFor(() => expect(scanRequests(api.find, 'POST')).toHaveLength(1))
    expect(await within(await card()).findByRole('alert')).toHaveTextContent('已有练度扫描在跑，先等它结束或取消')
  })

  it('running 时每 5 s 再取一次状态', async () => {
    const api = await renderWithFakeTimers({ trainingScan: RUNNING_TRAINING_SCAN })
    const initial = scanRequests(api.find, 'GET').length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })

    await waitFor(() => expect(scanRequests(api.find, 'GET').length).toBeGreaterThan(initial))
  })

  it('非 running 状态不轮询', async () => {
    const api = await renderWithFakeTimers({
      trainingScan: { ...RUNNING_TRAINING_SCAN, status: 'succeeded', finished_at: '2026-10-11T01:04:00Z' },
    })
    const initial = scanRequests(api.find, 'GET').length
    await card()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })

    expect(scanRequests(api.find, 'GET')).toHaveLength(initial)
  })

  it('文案不写成「刷新干员识别」：那一页的旧裁决只约束干员识别', async () => {
    installAccountApi()
    renderAccount('/more/account')

    const region = await card()
    expect(within(region).queryByText(/刷新/)).not.toBeInTheDocument()
    // 识别时间要等概要拿到才渲染，所以这里得 `find` 而不是 `get`。
    expect(await screen.findByLabelText('干员识别')).toHaveTextContent('识别于')
    expect(screen.queryByRole('button', { name: /刷新识别/ })).not.toBeInTheDocument()
  })
})

describe('M36 练度扫描的文案与响应兜底', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('摘要只在跳过项非零时提它们', () => {
    const scan = { ...IDLE_TRAINING_SCAN, scanned: 10, updated: 10 }
    expect(trainingScanSummary(scan)).toEqual(['写入 10 名 / 共读 10 名'])
    expect(trainingScanSummary({ ...scan, skipped: 1, unresolved: 2, mismatched: 3 })).toEqual([
      '写入 10 名 / 共读 10 名',
      '跳过 1 名 · 名单未解析 2 名 · 卡名不符 3 名',
    ])
    // M40：全量扫描的两个身份计数也是「为 0 不提」，单位是张（不是名）。
    expect(trainingScanSummary({ ...scan, realigned: 4, unverified: 5 })).toEqual([
      '写入 10 名 / 共读 10 名',
      '重锚 4 张 · 身份未核 5 张',
    ])
  })

  it('状态形状读不出来时落到 idle，页面还能点「开始扫描」', () => {
    expect(normalizeTrainingScanState({ status: 'unknown-status' }).status).toBe('idle')
    expect(normalizeTrainingScanState(null)).toEqual(IDLE_TRAINING_SCAN)
  })

  it('请求层按契约读三个方法的响应（独立于假后端）', async () => {
    const calls: string[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init)
      const url = new URL(request.url)
      calls.push(`${request.method} ${url.pathname}`)
      if (request.method === 'POST') {
        return jsonResponse({
          pipeline_id: 'pipe-scan-9',
          total: 40,
          targets: [{ char_id: 'char_002_amiya', name: '阿米娅' }],
          deferred: ['char_123_knight'],
          unresolved: ['某位没解析出来的干员'],
        })
      }
      const text = await request.clone().text()
      return jsonResponse({
        status: 'running',
        pipeline_id: 'pipe-scan-9',
        started_at: '2026-10-11T01:00:00Z',
        finished_at: null,
        scanned: 12,
        updated: 9,
        skipped: 3,
        unresolved: 0,
        mismatched: 1,
        realigned: 2,
        unverified: 1,
        message: null,
        total: 40,
        body_echo: text,
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const submitted = await startTrainingScan({ limit: 40, include_plan_targets: true })
    expect(calls).toEqual(['POST /api/account/training-scan'])
    expect(submitted).toMatchObject({ pipeline_id: 'pipe-scan-9', total: 40, deferred: ['char_123_knight'] })
    expect(submitted.targets[0]).toMatchObject({ char_id: 'char_002_amiya', name: '阿米娅' })

    const state = await fetchTrainingScan()
    expect(calls).toEqual(['POST /api/account/training-scan', 'GET /api/account/training-scan'])
    expect(state).toMatchObject({
      status: 'running',
      scanned: 12,
      updated: 9,
      skipped: 3,
      mismatched: 1,
      realigned: 2,
      unverified: 1,
      total: 40,
    })
  })

  it('POST 的请求体带 limit 与 include_plan_targets', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init)
      bodies.push(request.method === 'POST' ? JSON.parse(await request.clone().text()) : null)
      return jsonResponse({
        ...IDLE_TRAINING_SCAN,
        pipeline_id: 'pipe-scan-2',
        total: 5,
        targets: [],
        deferred: [],
        unresolved: [],
      })
    }))

    await startTrainingScan({ limit: 5, char_ids: ['char_002_amiya'] })

    expect(bodies).toEqual([{ limit: 5, char_ids: ['char_002_amiya'] }])
  })
})

describe('M39 全量扫描', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('全量扫描：二次确认之后才提交 mode=full', async () => {
    const api = await renderWithFakeTimers()

    fireEvent.click(within(await card()).getByRole('button', { name: '全量扫描' }))

    // 先弹确认框：这一步要占设备几十分钟，不能点一下就开跑。
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent('会把干员列表里每个人的详情页都扫一遍')
    expect(scanRequests(api.find, 'POST')).toHaveLength(0)

    fireEvent.click(within(dialog).getByRole('button', { name: '开始全量扫描' }))

    await waitFor(() => expect(scanRequests(api.find, 'POST')).toHaveLength(1))
    expect(scanRequests(api.find, 'POST')[0].body).toEqual({ mode: 'full' })
  })

  it('全量扫描：确认框里点「再想想」不提交', async () => {
    const api = await renderWithFakeTimers()

    fireEvent.click(within(await card()).getByRole('button', { name: '全量扫描' }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '再想想' }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(scanRequests(api.find, 'POST')).toHaveLength(0)
  })

  it('按名单扫的那颗按钮仍然不带 mode（老口径不变）', async () => {
    const api = await renderWithFakeTimers()

    fireEvent.click(within(await card()).getByRole('button', { name: '开始扫描' }))

    await waitFor(() => expect(scanRequests(api.find, 'POST')).toHaveLength(1))
    expect(scanRequests(api.find, 'POST')[0].body).toEqual({})
  })

  it('全量扫描跑着时：进度写成「已读 x 张 · 翻过 y 屏」，不假装有总数', async () => {
    await renderWithFakeTimers({ trainingScan: RUNNING_FULL_TRAINING_SCAN })

    const region = await card()
    expect(within(region).getByTestId('training-scan-status')).toHaveTextContent('进行中')
    expect(region).toHaveTextContent('已读 23 张 · 翻过 2 屏')
    expect(region).not.toHaveTextContent('已读 23 /')
    // `total` 是 `null`（没有名单）⇒ 进度条走不确定态，不给 `aria-valuenow`。
    expect(within(region).getByRole('progressbar', { name: '练度扫描进度' })).not.toHaveAttribute('aria-valuenow')
  })

  it('提交全量扫描后先按受理结果渲染：没有总数就不显示「x / y」', async () => {
    const api = await renderWithFakeTimers({
      trainingScanSubmit: TRAINING_SCAN_SUBMIT_FULL,
      // 受理之后第一次轮询回来：全量扫描的 `total` 恒为 `null`。
      trainingScanAfterStart: { ...RUNNING_FULL_TRAINING_SCAN, scanned: 0, screens: 0, total: null },
    })

    fireEvent.click(within(await card()).getByRole('button', { name: '全量扫描' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '开始全量扫描' }))

    await waitFor(() => expect(scanRequests(api.find, 'POST')).toHaveLength(1))
    // 显示的只能是「已读 0 张」：受理结果里没有分母，页面不许自己编一个。
    await waitFor(async () => expect(await card()).toHaveTextContent('已读 0 张'))
    expect(await card()).not.toHaveTextContent('已读 0 /')
  })
})

describe('M39 全量扫描的文案与兜底', () => {
  it('全量扫描的进度文案用张数与屏数', () => {
    const full = { ...RUNNING_FULL_TRAINING_SCAN }
    expect(scannedText(full, null)).toBe('已读 23 张 · 翻过 2 屏')
    expect(scannedText({ ...full, scanned: 0, screens: 0 }, null)).toBe('已读 0 张')
    // 点名扫的文案不变（`total` 已知就带分母）。
    expect(scannedText(RUNNING_TRAINING_SCAN, 40)).toBe('已读 12 / 40')
  })

  it('响应里缺 mode/screens 时按 single 与 0 兜底', () => {
    const state = normalizeTrainingScanState({ status: 'running', scanned: 3 })
    expect(state.mode).toBe('single')
    expect(state.screens).toBe(0)
    expect(normalizeTrainingScanState({ status: 'running', mode: 'weird' }).mode).toBe('single')
    expect(normalizeTrainingScanState({ status: 'running', mode: 'full' }).mode).toBe('full')
  })

  it('受理结果里缺 mode 时按 single 认', () => {
    expect(normalizeTrainingScanSubmit({ pipeline_id: 'p', total: 2 }).mode).toBe('single')
    expect(normalizeTrainingScanSubmit({ pipeline_id: 'p', mode: 'full' }).mode).toBe('full')
  })
})
