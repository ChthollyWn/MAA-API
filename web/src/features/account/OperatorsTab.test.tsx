import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { KNOWN_EMPTY_ROW, UNKNOWN_SKILL_ROW, installAccountApi, renderAccount } from './test-utils'

function operatorsRequests(find: ReturnType<typeof installAccountApi>['find']) {
  return find('GET', '/api/account/operators')
}

/** 一行里某个字段单元格（`<dt>标签</dt><dd>值</dd>`）的文本。 */
function fieldValue(row: HTMLElement, label: string): string {
  const term = within(row).getByText(label)
  return term.nextElementSibling?.textContent ?? ''
}

describe('M27 干员页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('默认隐藏未拥有：请求带 owned_only=true，列表里没有未拥有的干员', async () => {
    const { find } = installAccountApi({ diffs: [] })
    renderAccount('/more/account')

    expect(await screen.findByText('阿米娅')).toBeInTheDocument()
    expect(screen.getByText('临光')).toBeInTheDocument()
    expect(screen.queryByText('陈')).not.toBeInTheDocument()
    await waitFor(() => expect(operatorsRequests(find)).toHaveLength(1))
    expect(operatorsRequests(find)[0].url.searchParams.get('owned_only')).toBe('true')
    expect(screen.getByText(/默认只显示已拥有的干员/)).toBeInTheDocument()
  })

  it('按稀有度、职业、精英化筛选并写进请求参数', async () => {
    const { find } = installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByLabelText('最低稀有度'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('职业'), { target: { value: '重装' } })
    fireEvent.change(screen.getByLabelText('最低精英化'), { target: { value: '2' } })

    await waitFor(() => {
      const last = operatorsRequests(find).at(-1)
      expect(last?.url.searchParams.get('rarity_min')).toBe('6')
      expect(last?.url.searchParams.get('profession')).toBe('重装')
      expect(last?.url.searchParams.get('elite_min')).toBe('2')
    })
  })

  it('持有筛选切到「全部」后带 owned_only=false 并显示未拥有的干员', async () => {
    const { find } = installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByLabelText('持有'), { target: { value: 'all' } })

    expect(await screen.findByText('陈')).toBeInTheDocument()
    await waitFor(() => expect(operatorsRequests(find).at(-1)?.url.searchParams.get('owned_only')).toBe('false'))
  })

  it('「未拥有」在已取回的列表里过滤', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByLabelText('持有'), { target: { value: 'unowned' } })

    expect(await screen.findByText('陈')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('阿米娅')).not.toBeInTheDocument())
  })

  it('搜索按名字片段过滤，并在输入停下后再发请求', async () => {
    const { find } = installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByPlaceholderText('搜索干员名'), { target: { value: '临' } })

    await waitFor(() => expect(operatorsRequests(find).at(-1)?.url.searchParams.get('query')).toBe('临'))
    await waitFor(() => expect(screen.queryByText('阿米娅')).not.toBeInTheDocument())
    expect(screen.getByText('临光')).toBeInTheDocument()
  })

  it('顶部显示识别时间，且没有「刷新识别」按钮', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')

    expect(await screen.findByLabelText('干员识别')).toHaveTextContent('识别于')
    expect(screen.getByText('共 3 名（精二 1）')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /刷新识别/ })).not.toBeInTheDocument()
  })

  it('未识别时概要显示「尚未识别」', async () => {
    installAccountApi({
      diffs: [],
      summary: {
        operators: { recognized: false, total: 0, elite2: 0, recognized_at: null, stale: false, stale_days: null, stale_after_days: 14 },
        depot: { recognized: false, total: 0, elite2: 0, recognized_at: null, stale: false, stale_days: null, stale_after_days: 14 },
        diffs: 0,
      },
    })
    renderAccount('/more/account')

    expect(await screen.findByLabelText('干员识别')).toHaveTextContent('尚未识别')
  })

  it('每行显示字段来源标记（识别 / 手动 / Agent）', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')

    await screen.findByText('阿米娅')
    expect(screen.getByTestId('operator-source-char_002_amiya-recognition')).toHaveTextContent('识别')
    expect(screen.getByTestId('operator-source-char_002_amiya-manual')).toHaveTextContent('手动')
    expect(screen.getByTestId('operator-source-char_002_amiya-agent')).toHaveTextContent('Agent')
    expect(screen.getByTestId('operator-source-char_123_knight-recognition')).toBeInTheDocument()
    expect(screen.queryByTestId('operator-source-char_123_knight-agent')).not.toBeInTheDocument()
  })

  it('没有符合条件的干员时给空状态', async () => {
    installAccountApi({ diffs: [], operators: [] })
    renderAccount('/more/account')

    expect(await screen.findByText('没有符合条件的干员')).toBeInTheDocument()
  })

  it('M35：技能 / 专精 / 模组未识别时写「未识别」，已知的精英化与等级照旧显示', async () => {
    installAccountApi({ diffs: [], operators: [UNKNOWN_SKILL_ROW] })
    renderAccount('/more/account')

    const row = await screen.findByTestId('operator-char_4093_frston')
    expect(fieldValue(row, '技能等级')).toBe('未识别')
    expect(fieldValue(row, '专精')).toBe('未识别')
    expect(fieldValue(row, '模组')).toBe('未识别')
    expect(fieldValue(row, '精英化')).toBe('精二')
    expect(fieldValue(row, '等级')).toBe('90')
    expect(fieldValue(row, '潜能')).toBe('1')
  })

  it('M35：已知的「无专精 / 无模组」显示「—」，不和未识别混为一谈', async () => {
    installAccountApi({ diffs: [], operators: [KNOWN_EMPTY_ROW] })
    renderAccount('/more/account')

    const row = await screen.findByTestId('operator-char_102_texas')
    expect(fieldValue(row, '专精')).toBe('—')
    expect(fieldValue(row, '模组')).toBe('—')
    expect(within(row).queryByText('未识别')).not.toBeInTheDocument()
  })
})

describe('M27 差异提示条', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('逐条「知道了」只提交这条的 id，并从提示条里移掉', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account')

    expect(await screen.findByLabelText('识别差异')).toHaveTextContent('识别结果覆盖了 2 处手动修改')
    expect(screen.getByText('精英化：手动 精一 → 识别 精二')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: '知道了' })[0])

    await waitFor(() => expect(find('DELETE', '/api/account/operator-diffs')).toHaveLength(1))
    expect(find('DELETE', '/api/account/operator-diffs')[0].body).toEqual({ ids: [11] })
    await waitFor(() => expect(screen.getByLabelText('识别差异')).toHaveTextContent('识别结果覆盖了 1 处手动修改'))
  })

  it('「全部知道了」不传 ids', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account')

    expect(await screen.findByLabelText('识别差异')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全部知道了' }))

    await waitFor(() => expect(find('DELETE', '/api/account/operator-diffs')).toHaveLength(1))
    expect(find('DELETE', '/api/account/operator-diffs')[0].body).toBeUndefined()
    await waitFor(() => expect(screen.queryByLabelText('识别差异')).not.toBeInTheDocument())
  })

  it('没有差异时不渲染提示条', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')

    await screen.findByText('阿米娅')
    expect(screen.queryByLabelText('识别差异')).not.toBeInTheDocument()
  })
})
