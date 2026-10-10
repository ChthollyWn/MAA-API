import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { installAccountApi, renderAccount } from './test-utils'

describe('M27 仓库页', () => {
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
    expect(screen.getByText('120000')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /编辑/ })).not.toBeInTheDocument()
  })

  it('显示识别时间与过期提示，不做刷新识别与导入导出', async () => {
    installAccountApi()
    renderAccount('/more/account/depot')

    const status = await screen.findByLabelText('仓库识别')
    expect(status).toHaveTextContent('已过期')
    expect(status).toHaveTextContent('识别于')
    expect(status).toHaveTextContent('已超过 14 天')
    expect(status).toHaveTextContent('识别只覆盖养成材料页签')
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
