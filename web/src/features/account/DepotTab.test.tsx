import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { resetAssetCache } from './assets'
import { installAccountApi, renderAccount } from './test-utils'

/**
 * M27 仓库页 + M44 §4.4 的道具图与近似值标注。
 *
 * 图标**必须走本机代理**（`/api/account/assets/item/<item_id>` 取 blob）：官方 CDN 有防盗链，
 * 浏览器直连带 `Referer` 会被 403，而且取图接口要 `X-Token`，`<img src>` 带不了头。所以这里
 * 断言的是「请求了哪个本机路径」，而不是「src 指向官方地址」。
 */
describe('M27 仓库页', () => {
  beforeEach(() => {
    resetAssetCache()
    // jsdom 没有 createObjectURL：给一个稳定的假地址，断言只看「用了 blob:」。
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL: () => 'blob:item-icon', revokeObjectURL: () => {} }),
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('按分类分组的只读列表显示数量', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    expect(await screen.findByRole('heading', { name: '材料（3）' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '货币（1）' })).toBeInTheDocument()
    expect(screen.getByText('固源岩')).toBeInTheDocument()
    expect(screen.getByTestId('depot-count-30011')).toHaveTextContent('× 42')
    expect(screen.queryByRole('button', { name: /编辑/ })).not.toBeInTheDocument()
  })

  it('每行显示图标 + 名字 + ×数量 + 分类，图标走本机代理的 blob 地址', async () => {
    const { assetRequests } = installAccountApi()
    renderAccount('/more/account/depot')

    const row = await screen.findByTestId('depot-30012')
    expect(row).toHaveTextContent('固源岩')
    expect(row).toHaveTextContent('材料')
    expect(screen.getByTestId('depot-count-30012')).toHaveTextContent('× 7')

    const icon = await screen.findByTestId('depot-icon-30012')
    expect(icon).toHaveAttribute('src', 'blob:item-icon')
    // 走的是本机代理的 item 路径，而且**不是**官方 CDN。
    expect(assetRequests).toContain('/api/account/assets/item/30012')
    expect(assetRequests.every((path) => path.startsWith('/api/account/assets/item/'))).toBe(true)
    expect(assetRequests.some((path) => path.includes('web.hycdn.cn'))).toBe(false)
  })

  it('图标取不到时退回中性占位，不留破图', async () => {
    installAccountApi({ assetFailures: ['/api/account/assets/item/99999'] })
    renderAccount('/more/account/depot')

    // 取不到图的那件（99999，索引里没有这个 id）：占位在、`img` 不在。
    expect(await screen.findByTestId('depot-icon-99999-placeholder')).toBeInTheDocument()
    expect(screen.queryByTestId('depot-icon-99999')).not.toBeInTheDocument()
    // 同一页里别的道具不受影响。
    expect(await screen.findByTestId('depot-icon-30012')).toBeInTheDocument()
  })

  it('顶部资源栏那三件（龙门币等）的数量标「约」并说明原因', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    const money = await screen.findByTestId('depot-count-4001')
    expect(money).toHaveTextContent('约 × 4797000')
    expect(money.querySelector('[title]')).toHaveAttribute('title', '上游识别只给到千位，后三位不准确')
    // 仓库里逐件识别的材料是准的，不标「约」。
    expect(screen.getByTestId('depot-count-30011')).not.toHaveTextContent('约')
  })

  it('显示识别时间与过期提示，不做刷新识别与导入导出', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    const status = await screen.findByLabelText('仓库识别')
    expect(status).toHaveTextContent('已过期')
    expect(status).toHaveTextContent('识别于')
    expect(status).toHaveTextContent('已超过 14 天')
    for (const forbidden of [/刷新识别/, /导入/, /导出/]) {
      expect(screen.queryByRole('button', { name: forbidden })).not.toBeInTheDocument()
    }
  })

  it('未识别时提示尚未识别', async () => {
    installAccountApi({
      summary: {
        operators: { recognized: false, total: 0, elite2: 0, recognized_at: null, stale: false, stale_days: null, stale_after_days: 14 },
        depot: { recognized: false, total: 0, elite2: 0, recognized_at: null, stale: false, stale_days: null, stale_after_days: 14 },
        diffs: 0,
      },
    })
    renderAccount('/more/account/depot')

    expect(await screen.findByLabelText('仓库识别')).toHaveTextContent('尚未识别')
  })

  it('按分类筛选与搜索写进请求参数', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account/depot')
    await screen.findByText('固源岩')

    fireEvent.change(screen.getByLabelText('分类'), { target: { value: '货币' } })
    await waitFor(() => expect(find('GET', '/api/account/depot').at(-1)?.url.searchParams.get('category')).toBe('货币'))
    await waitFor(() => expect(screen.queryByText('固源岩')).not.toBeInTheDocument())
    expect(screen.getByText('龙门币')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('搜索物品名或 id'), { target: { value: '源岩' } })
    await waitFor(() => expect(find('GET', '/api/account/depot').at(-1)?.url.searchParams.get('query')).toBe('源岩'))
  })

  it('标签页切到仓库再切回干员', async () => {
    installAccountApi({ diffs: [] })
    const { router } = renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.click(screen.getByRole('radio', { name: '仓库' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/more/account/depot'))
    expect(await screen.findByRole('heading', { name: '材料（3）' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: '干员' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/more/account'))
    expect(await screen.findByText('阿米娅')).toBeInTheDocument()
  })
})

/**
 * M45 W3a：仓库页要把「未识别」与「不支持/没识别到」讲清楚。
 *
 * 三件必须做到的事：
 * 1. **数量未知显示「—」，不是 0** —— 自识别只保证身份，数量要等 W3B 的 OCR；
 *    「仓库里没有这件道具」与「不知道有几件」不能混成同一个 0。
 * 2. **每行说清来源**：内核白名单 / 自识别补的（旧数据没有来源就不贴标签）；
 * 3. **整份快照的覆盖与未识别清单**：`DepotInfo` 只回 `{itemId: count}`，缺失与
 *    0 库存无法区分，所以「这一轮没识别到哪些」是一条独立的事实，不能折算成 0。
 */
describe('M45 仓库页的来源与未识别', () => {
  beforeEach(() => {
    resetAssetCache()
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL: () => 'blob:item-icon', revokeObjectURL: () => {} }),
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('数量未识别时显示「—」而不是 0', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    const unknown = await screen.findByTestId('depot-count-7003')
    expect(unknown).toHaveTextContent('—')
    expect(unknown).not.toHaveTextContent('0')
    expect(unknown.querySelector('[title]')).toHaveAttribute(
      'title',
      '识别到这件道具，但这一轮没读到数量（不是 0）',
    )
    // 读得出数量的那些照常显示数字。
    expect(screen.getByTestId('depot-count-30011')).toHaveTextContent('× 42')
  })

  it('每行标出来源，没有来源的旧数据不贴标签', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    expect(await screen.findByTestId('depot-source-30011')).toHaveTextContent('内核')
    const selfScan = screen.getByTestId('depot-source-7003')
    expect(selfScan).toHaveTextContent('自识别')
    expect(selfScan).toHaveAttribute('title', '这一件由本机自识别补上（不在内核的识别名单里）')
    // 99999 是 0021 迁移之前写下的行（没有 sources）⇒ 不猜来源、不贴标签。
    expect(screen.queryByTestId('depot-source-99999')).not.toBeInTheDocument()
  })

  it('显示这一轮的覆盖情况与没识别到的道具', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    const coverage = await screen.findByTestId('depot-coverage')
    expect(coverage).toHaveTextContent('共 6 种：内核 4、自识别 1（扫了 3 屏）')
    expect(coverage).toHaveTextContent('2 种这一轮没识别到（不等于 0）')

    const unrecognized = screen.getByTestId('depot-unrecognized')
    expect(unrecognized).toHaveTextContent('这一轮没识别到 2 种：高级凭证、家具零件')
    expect(unrecognized).toHaveTextContent('没识别到不等于仓库里没有')
  })

  it('没有未识别项时不显示那一段，后端没给覆盖项时页面照常渲染', async () => {
    installAccountApi({ depotUnrecognized: [], depotCoverage: null })
    renderAccount('/more/account/depot')

    await screen.findByTestId('depot-count-30011')
    expect(screen.queryByTestId('depot-unrecognized')).not.toBeInTheDocument()
    expect(screen.queryByTestId('depot-coverage')).not.toBeInTheDocument()
    // 筛选刷新也照常：分类下拉与列表都在。
    expect(screen.getByRole('heading', { name: '材料（3）' })).toBeInTheDocument()
  })

  it('未识别清单不随筛选缩水（它是整份快照的事实）', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account/depot')
    await screen.findByTestId('depot-coverage')

    fireEvent.change(screen.getByLabelText('分类'), { target: { value: '材料' } })

    await waitFor(() =>
      expect(find('GET', '/api/account/depot').at(-1)?.url.searchParams.get('category')).toBe('材料'),
    )
    expect(await screen.findByTestId('depot-unrecognized')).toHaveTextContent('家具零件')
  })
})
