import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { KNOWN_EMPTY_ROW, UNKNOWN_SKILL_ROW, installAccountApi, operatorRow, renderAccount } from './test-utils'

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
    const { find } = installAccountApi()
    renderAccount('/more/account')

    expect(await screen.findByText('阿米娅')).toBeInTheDocument()
    expect(screen.getByText('临光')).toBeInTheDocument()
    expect(screen.queryByText('陈')).not.toBeInTheDocument()
    await waitFor(() => expect(operatorsRequests(find)).toHaveLength(1))
    expect(operatorsRequests(find)[0].url.searchParams.get('owned_only')).toBe('true')
    expect(screen.getByText(/默认只显示已拥有的干员/)).toBeInTheDocument()
  })

  it('按稀有度、职业、精英化筛选并写进请求参数', async () => {
    const { find } = installAccountApi()
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

    // 生效条件显示可单独移除的芯片，点击可逐项撤销筛选
    fireEvent.click(screen.getByRole('button', { name: '移除筛选：职业：重装' }))
    await waitFor(() => {
      const last = operatorsRequests(find).at(-1)
      expect(last?.url.searchParams.get('profession')).toBeNull()
      expect(last?.url.searchParams.get('rarity_min')).toBe('6')
    })
  })

  it('持有筛选切到「全部」后带 owned_only=false 并显示未拥有的干员', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByLabelText('持有'), { target: { value: 'all' } })

    expect(await screen.findByText('陈')).toBeInTheDocument()
    await waitFor(() => expect(operatorsRequests(find).at(-1)?.url.searchParams.get('owned_only')).toBe('false'))
  })

  it('「未拥有」在已取回的列表里过滤', async () => {
    installAccountApi()
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByLabelText('持有'), { target: { value: 'unowned' } })

    expect(await screen.findByText('陈')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('阿米娅')).not.toBeInTheDocument())
  })

  it('搜索按名字片段过滤，并在输入停下后再发请求', async () => {
    const { find } = installAccountApi()
    renderAccount('/more/account')
    await screen.findByText('阿米娅')

    fireEvent.change(screen.getByPlaceholderText('搜索干员名'), { target: { value: '临' } })

    await waitFor(() => expect(operatorsRequests(find).at(-1)?.url.searchParams.get('query')).toBe('临'))
    await waitFor(() => expect(screen.queryByText('阿米娅')).not.toBeInTheDocument())
    expect(screen.getByText('临光')).toBeInTheDocument()
  })

  it('顶部显示同步时间，且没有「刷新识别」按钮', async () => {
    installAccountApi()
    renderAccount('/more/account')

    // M47 起状态与绑定/同步动作在同一张卡里（不再另开一张「干员识别」状态卡）；
    // 卡片先出现、概要后到，所以等的是那行「同步于 …」。
    expect(await screen.findByText(/^同步于 \d{4}-\d{2}-\d{2}/)).toBeInTheDocument()
    expect(screen.getByLabelText('森空岛练度')).toHaveTextContent('共 3 名（精二 1）')
    expect(screen.queryByRole('button', { name: /刷新识别/ })).not.toBeInTheDocument()
  })

  it('未识别时概要显示「尚未识别」', async () => {
    installAccountApi({
      summary: {
        operators: { recognized: false, total: 0, elite2: 0, recognized_at: null, stale: false, stale_days: null, stale_after_days: 14 },
        depot: { recognized: false, total: 0, elite2: 0, recognized_at: null, stale: false, stale_days: null, stale_after_days: 14 },
      },
    })
    renderAccount('/more/account')

    // 没绑定时卡片自己说「还没有同步过练度」，标题行的徽章标「未识别」
    expect(await screen.findByText('还没有同步过练度')).toBeInTheDocument()
    expect(screen.getByLabelText('森空岛练度')).toHaveTextContent('未识别')
  })

  it('每张卡片只标「森空岛」这一个来源', async () => {
    installAccountApi({
      operators: [
        operatorRow({
          char_id: 'char_002_amiya',
          name: '阿米娅',
          field_sources: {
            own: 'skland',
            elite: 'skland',
            level: 'skland',
            potential: 'skland',
            skill_level: 'skland',
            masteries: 'skland',
            modules: 'skland',
          },
        }),
      ],
    })
    renderAccount('/more/account')

    await screen.findByText('阿米娅')
    expect(screen.getByTestId('operator-source-char_002_amiya-skland')).toHaveTextContent('森空岛')
    expect(screen.queryByTestId('operator-source-char_002_amiya-recognition')).not.toBeInTheDocument()
    expect(screen.queryByTestId('operator-source-char_002_amiya-manual')).not.toBeInTheDocument()
    expect(screen.queryByTestId('operator-source-char_002_amiya-agent')).not.toBeInTheDocument()
  })

  it('没有符合条件的干员时给空状态', async () => {
    installAccountApi({ operators: [] })
    renderAccount('/more/account')

    expect(await screen.findByText('没有符合条件的干员')).toBeInTheDocument()
    expect(screen.getByText('试试放宽筛选；还没有数据的话，先绑定森空岛账号并同步练度')).toBeInTheDocument()
  })

  it('M35：技能 / 专精 / 模组未识别时写「未识别」，已知的精英化与等级照旧显示', async () => {
    installAccountApi({ operators: [UNKNOWN_SKILL_ROW] })
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
    installAccountApi({ operators: [KNOWN_EMPTY_ROW] })
    renderAccount('/more/account')

    const row = await screen.findByTestId('operator-char_102_texas')
    expect(fieldValue(row, '专精')).toBe('—')
    expect(fieldValue(row, '模组')).toBe('—')
    expect(within(row).queryByText('未识别')).not.toBeInTheDocument()
  })
})
