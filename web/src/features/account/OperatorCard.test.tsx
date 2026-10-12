import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { OperatorCard } from './OperatorCard'
import { operatorRow } from './test-utils'

/**
 * M43 卡片（规格 §7-4）：头像与技能图标走官方 CDN、没有 id 时降级不造假、未拥有灰显且
 * 不加载图片、模组显示 `CCR-X` 这类标签、图片失败换占位、点击与键盘都能打开编辑抽屉。
 */

afterEach(() => {
  cleanup()
})

const RICH = operatorRow({
  char_id: 'char_002_amiya',
  name: '阿米娅',
  rarity: 6,
  elite: 2,
  level: 80,
  potential: 6,
  skill_level: 7,
  masteries: { '1': 3, '2': 0, '3': 0 },
  modules: { Y: 3 },
  skill_ids: ['skchr_amiya_2', 'skchr_amiya_3'],
  module_details: [
    { type: 'Y', level: 3, equip_id: 'uniequip_002_amiya', branch: 'ccr', label: 'CCR-Y', type_icon: 'ccr-y' },
  ],
})

function renderCard(row = RICH, onSelect = vi.fn()) {
  render(<OperatorCard row={row} onSelect={onSelect} />)
  return { card: screen.getByTestId(`operator-${row.char_id}`), onSelect }
}

describe('M43 干员卡片', () => {
  it('头像与技能图标用官方 CDN 模板拼 URL', () => {
    renderCard()

    expect(screen.getByTestId('operator-avatar-char_002_amiya')).toHaveAttribute(
      'src',
      'https://web.hycdn.cn/arknights/game/assets/char/avatar/char_002_amiya.png',
    )
    expect(screen.getByTestId('operator-skill-icon-char_002_amiya-1')).toHaveAttribute(
      'src',
      'https://web.hycdn.cn/arknights/game/assets/char_skill/skchr_amiya_2.png',
    )
    expect(screen.getByTestId('operator-skill-icon-char_002_amiya-2')).toHaveAttribute(
      'src',
      'https://web.hycdn.cn/arknights/game/assets/char_skill/skchr_amiya_3.png',
    )
    // 图片懒加载：一页几十张卡，别在首屏把 CDN 打满。
    expect(screen.getByTestId('operator-avatar-char_002_amiya')).toHaveAttribute('loading', 'lazy')
  })

  it('没有 skill_ids 时只降级成槽位与专精数字，不拿别的图冒充', () => {
    renderCard(operatorRow({ char_id: 'char_123_knight', name: '临光', skill_ids: null, masteries: { '1': 2 } }))

    expect(screen.queryByTestId('operator-skill-icon-char_123_knight-1')).not.toBeInTheDocument()
    // 槽位还在，专精数字还是真值。
    expect(screen.getByTestId('operator-skill-char_123_knight-1')).toHaveTextContent('2')
    // 技能栏也整列未知（`skill_ids` 与 `masteries` 都是 null）时**不画槽位**：画三个
    // 空框等于宣称「这名干员有三个技能」，而这条事实我们并没有。改成一句话说清楚。
    cleanup()
    renderCard(operatorRow({ char_id: 'char_4093_frston', name: 'Friston-3', skill_ids: null, masteries: null }))
    expect(screen.queryByTestId('operator-skill-char_4093_frston-1')).not.toBeInTheDocument()
    expect(screen.getByText(/专精未识别/)).toBeInTheDocument()
  })

  it('未拥有：整卡灰显、不加载头像与技能图标', () => {
    const { card } = renderCard(
      operatorRow({ char_id: 'char_010_chen', name: '陈', own: false, skill_ids: ['skchr_chen_1'] }),
    )

    expect(card.className).toContain('grayscale')
    expect(screen.queryByTestId('operator-avatar-char_010_chen')).not.toBeInTheDocument()
    expect(screen.queryByTestId('operator-skill-icon-char_010_chen-1')).not.toBeInTheDocument()
    // 未拥有的标记还在，且练度原文照旧可读。
    expect(within(card).getByText('未拥有')).toBeInTheDocument()
  })

  it('模组：优先显示 module_details 的 label+等级，只有 modules 时退化成类型字母', () => {
    renderCard()

    expect(screen.getByTestId('operator-module-char_002_amiya-Y')).toHaveTextContent('CCR-Y')
    expect(screen.getByTestId('operator-module-char_002_amiya-Y')).toHaveTextContent('3')

    cleanup()
    renderCard(operatorRow({ char_id: 'char_102_texas', name: '德克萨斯', modules: { X: 2 }, module_details: null }))
    const chip = screen.getByTestId('operator-module-char_102_texas-X')
    expect(chip).toHaveTextContent('X')
    expect(chip).toHaveTextContent('2')
    expect(chip).not.toHaveTextContent('CCR')

    // 等级 0 是「有这模组但没升级」：不显示 chip（与 moduleText 的口径一致）。
    cleanup()
    renderCard(operatorRow({ char_id: 'char_103_angel', name: '能天使', modules: { X: 0 }, module_details: null }))
    expect(screen.queryByTestId('operator-module-char_103_angel-X')).not.toBeInTheDocument()
  })

  it('图片加载失败后换占位，页面上不留破图', () => {
    renderCard()
    const avatar = screen.getByTestId('operator-avatar-char_002_amiya')

    fireEvent.error(avatar)

    expect(screen.queryByTestId('operator-avatar-char_002_amiya')).not.toBeInTheDocument()
    expect(screen.getByTestId('operator-avatar-placeholder')).toBeInTheDocument()
  })

  it('点卡片或按回车都触发 onSelect（打开编辑抽屉）', () => {
    const { card, onSelect } = renderCard()

    fireEvent.click(card)
    expect(onSelect).toHaveBeenCalledWith(RICH)

    fireEvent.keyDown(card, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('「编辑」按钮是独立入口，点它不会连带触发整卡点击', () => {
    const { onSelect } = renderCard()

    fireEvent.click(screen.getByRole('button', { name: '编辑 阿米娅' }))

    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('M35：未识别在卡片上说清楚，练度原文写「未识别」而不是 0 / —', () => {
    const { card } = renderCard(
      operatorRow({
        char_id: 'char_4093_frston',
        name: 'Friston-3',
        masteries: null,
        modules: null,
        skill_level: null,
      }),
    )

    expect(within(card).getByText(/专精未识别/)).toBeInTheDocument()
    expect(within(card).getByText(/模组未识别/)).toBeInTheDocument()
    expect(within(card).getByText('技能等级').nextElementSibling).toHaveTextContent('未识别')
    expect(within(card).getByText('模组').nextElementSibling).toHaveTextContent('未识别')
  })

  it('字段来源徽章保留（M27/M35 的功能不因换 UI 消失）', () => {
    const { card } = renderCard(
      operatorRow({
        char_id: 'char_002_amiya',
        name: '阿米娅',
        field_sources: { elite: 'recognition', masteries: 'skland' },
      }),
    )

    expect(within(card).getByTestId('operator-source-char_002_amiya-recognition')).toHaveTextContent('识别')
    expect(within(card).getByTestId('operator-source-char_002_amiya-skland')).toHaveTextContent('森空岛')
  })
})
