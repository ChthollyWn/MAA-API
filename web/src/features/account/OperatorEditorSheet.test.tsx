import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { UNKNOWN_ALL_ROW, UNKNOWN_SKILL_ROW, installAccountApi, operatorRow, renderAccount } from './test-utils'

const THREE_STAR = operatorRow({
  char_id: 'char_3_star',
  name: '三星干员',
  rarity: 3,
  elite: 1,
  level: 55,
  potential: 3,
  skill_level: 7,
  profession: '先锋',
})

async function openEditor(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: `编辑 ${name}` }))
  return await screen.findByRole('dialog')
}

describe('M27 干员编辑抽屉', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('精英化选项按稀有度收窄：3★ 只能到精一', async () => {
    installAccountApi({ diffs: [], operators: [THREE_STAR] })
    renderAccount('/more/account')
    const dialog = await openEditor('三星干员')

    const elite = within(dialog).getByLabelText('精英化')
    expect(within(elite).getAllByRole('option').map((option) => option.textContent)).toEqual(['精零', '精一'])
    expect(within(dialog).getByText('3★ 干员的精英化上限是精一')).toBeInTheDocument()
  })

  it('专精只在精二且技能 7 级时可填，模组只在精二时可填', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    const dialog = await openEditor('临光')

    expect(within(dialog).getByLabelText('S1 专精')).toBeDisabled()
    expect(within(dialog).getByLabelText('S2 专精')).toBeDisabled()
    expect(within(dialog).getByLabelText('X 模组等级')).toBeDisabled()
    expect(within(dialog).getByText('专精只在精二且技能等级 7 时可填。')).toBeInTheDocument()
    expect(within(dialog).getByText('模组只在精二时可填。')).toBeInTheDocument()
  })

  it('精二且技能 7 级后专精与模组可填，技能数按稀有度给默认值', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    const dialog = await openEditor('阿米娅')

    expect(within(dialog).getByLabelText('S1 专精')).toBeEnabled()
    expect(within(dialog).getByLabelText('S2 专精')).toBeEnabled()
    expect(within(dialog).getByLabelText('X 模组等级')).toBeEnabled()
    expect(within(dialog).queryByLabelText('S3 专精')).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText('技能数')).toHaveValue('2')
  })

  it('保存只提交改动过的字段，成功后刷新列表与概要并关掉抽屉', async () => {
    const { find } = installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    const dialog = await openEditor('临光')
    await waitFor(() => expect(find('GET', '/api/account/operators')).toHaveLength(1))

    fireEvent.change(within(dialog).getByLabelText('等级'), { target: { value: '42' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存 1 项改动' }))

    await waitFor(() => expect(find('PATCH', '/api/account/operators/char_123_knight')).toHaveLength(1))
    expect(find('PATCH', '/api/account/operators/char_123_knight')[0].body).toEqual({ level: 42 })
    await waitFor(() => expect(find('GET', '/api/account/operators')).toHaveLength(2))
    await waitFor(() => expect(find('GET', '/api/account/summary')).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('等级越界时报错并禁用保存', async () => {
    installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    const dialog = await openEditor('临光')

    fireEvent.change(within(dialog).getByLabelText('等级'), { target: { value: '0' } })

    expect(within(dialog).getByText('等级需在 1–90 之间')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  it('把精英化降到精二以下会显式清空专精与模组', async () => {
    const { find } = installAccountApi({ diffs: [] })
    renderAccount('/more/account')
    const dialog = await openEditor('阿米娅')

    fireEvent.change(within(dialog).getByLabelText('精英化'), { target: { value: '1' } })
    fireEvent.change(within(dialog).getByLabelText('技能等级'), { target: { value: '4' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存 4 项改动' }))

    await waitFor(() => expect(find('PATCH', '/api/account/operators/char_002_amiya')).toHaveLength(1))
    expect(find('PATCH', '/api/account/operators/char_002_amiya')[0].body).toEqual({
      elite: 1,
      skill_level: 4,
      masteries: { '1': 0, '2': 0, '3': 0 },
      modules: {},
    })
  })

  it('保存失败时在抽屉里就地提示，不关闭', async () => {
    installAccountApi({ diffs: [], patchStatus: 422 })
    renderAccount('/more/account')
    const dialog = await openEditor('临光')

    fireEvent.change(within(dialog).getByLabelText('潜能'), { target: { value: '5' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存 1 项改动' }))

    expect(await within(dialog).findByText(/保存失败：/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('M35 未识别练度的编辑', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('未识别的列留空且当前项是「未识别」，专精 / 模组控件不被禁用', async () => {
    installAccountApi({ diffs: [], operators: [UNKNOWN_SKILL_ROW] })
    renderAccount('/more/account')
    const dialog = await openEditor('Friston-3')

    const skill = within(dialog).getByLabelText('技能等级')
    expect(skill).toHaveValue('')
    expect(within(skill).getByRole('option', { selected: true })).toHaveTextContent('未识别')
    // 技能等级未知不拦补录（后端也只在已知时判），模组同样留空。
    expect(within(dialog).getByLabelText('S1 专精')).toBeEnabled()
    expect(within(dialog).getByLabelText('S2 专精')).toHaveValue('')
    expect(within(dialog).getByLabelText('X 模组等级')).toHaveValue('')
    // 识别值照旧填在表单里，不许被写成未识别。
    expect(within(dialog).getByLabelText('精英化')).toHaveValue('2')
    expect(within(dialog).getByLabelText('等级')).toHaveValue(90)
  })

  it('精英化也未知时同样不拦专精 / 模组补录', async () => {
    installAccountApi({ diffs: [], operators: [UNKNOWN_ALL_ROW] })
    renderAccount('/more/account')
    const dialog = await openEditor('未识别练度')

    expect(within(dialog).getByLabelText('S1 专精')).toBeEnabled()
    expect(within(dialog).getByLabelText('X 模组等级')).toBeEnabled()
  })

  it('只改已知字段时，未识别的技能 / 专精 / 模组不写进 PATCH', async () => {
    const { find } = installAccountApi({ diffs: [], operators: [UNKNOWN_SKILL_ROW] })
    renderAccount('/more/account')
    const dialog = await openEditor('Friston-3')

    fireEvent.change(within(dialog).getByLabelText('等级'), { target: { value: '85' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存 1 项改动' }))

    await waitFor(() => expect(find('PATCH', '/api/account/operators/char_4093_frston')).toHaveLength(1))
    const body = find('PATCH', '/api/account/operators/char_4093_frston')[0].body
    expect(body).toEqual({ level: 85 })
    expect(body).not.toHaveProperty('skill_level')
    expect(body).not.toHaveProperty('masteries')
    expect(body).not.toHaveProperty('modules')
  })

  it('补录专精：只填 S2 时 PATCH 带上具体映射，S2=3', async () => {
    const { find } = installAccountApi({ diffs: [], operators: [UNKNOWN_SKILL_ROW] })
    renderAccount('/more/account')
    const dialog = await openEditor('Friston-3')

    fireEvent.change(within(dialog).getByLabelText('S2 专精'), { target: { value: '3' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存 1 项改动' }))

    await waitFor(() => expect(find('PATCH', '/api/account/operators/char_4093_frston')).toHaveLength(1))
    expect(find('PATCH', '/api/account/operators/char_4093_frston')[0].body).toEqual({
      masteries: { '1': 0, '2': 3, '3': 0 },
    })
  })
})
