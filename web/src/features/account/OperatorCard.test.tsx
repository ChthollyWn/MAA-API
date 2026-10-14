import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { OperatorCard } from './OperatorCard'
import { resetAssetCache } from './assets'
import { operatorRow } from './test-utils'

/**
 * M43 卡片（规格 §7-4，按用户 2026-10-12 的反馈改成宽横卡）：
 *
 * - 头像与技能图标走**本机后端代理**（官方 CDN 有防盗链，带 Referer 会 403），
 *   所以是用带 `X-Token` 的 `fetch` 取 blob 再转 object URL——`<img src>` 带不了头；
 * - 没有 `skill_ids` 时不画图标、技能栏整列未知时不画槽位（不装懂）；
 * - 未拥有整卡灰显且不画技能/模组；
 * - 模组显示 `CCR-X` 这类标签（没有明细时退化成类型字母）。
 */

const AVATAR_PATH = '/api/account/assets/avatar/char_002_amiya'
const SKILL_PATH = '/api/account/assets/skill/skchr_amiya_2'

/** 把取图接口换成可控的假实现；`fail` 里的路径会回 404。 */
function stubAssets(fail: string[] = []) {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
    calls.push(url)
    if (fail.some((path) => url.endsWith(path))) {
      return new Response('nope', { status: 404 })
    }
    return new Response(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }), {
      status: 200,
      headers: { 'Content-Type': 'image/png' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

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

function renderCard(row = RICH) {
  render(<OperatorCard row={row} />)
  return {
    row: screen.getByTestId(`operator-${row.char_id}`),
    card: screen.getByTestId(`operator-card-${row.char_id}`),
  }
}

beforeEach(() => {
  resetAssetCache()
  // jsdom 没有 createObjectURL：给一个稳定的假 URL，断言只看「用了 blob URL」。
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:asset', revokeObjectURL: () => {} }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('M43 干员卡片', () => {
  it('头像与技能图标走本机代理取图，并用 blob URL 渲染', async () => {
    const { calls } = stubAssets()
    renderCard()

    // 取图请求带上了路径，并且不是直连官方 CDN。
    await waitFor(() => expect(calls).toContain(AVATAR_PATH))
    expect(calls).toContain(SKILL_PATH)
    expect(calls.every((url) => url.startsWith('/api/account/assets/'))).toBe(true)

    const avatar = await screen.findByTestId('operator-avatar-char_002_amiya')
    expect(avatar).toHaveAttribute('src', 'blob:asset')
    expect(await screen.findByTestId('operator-skill-icon-char_002_amiya-1')).toBeInTheDocument()
  })

  it('取图失败（上游没有这张图）时退回占位，不留破图', async () => {
    stubAssets([AVATAR_PATH])
    renderCard()

    expect(await screen.findByTestId('operator-avatar-char_002_amiya-placeholder')).toBeInTheDocument()
    expect(screen.queryByTestId('operator-avatar-char_002_amiya')).not.toBeInTheDocument()
  })

  it('没有 skill_ids 时只留空技能框，不拿别的图冒充', async () => {
    const { calls } = stubAssets()
    renderCard(operatorRow({ char_id: 'char_123_knight', name: '临光', skill_ids: null, masteries: { '1': 2 } }))

    // 槽位还在，专精角标是真值。
    expect(screen.getByTestId('operator-skill-char_123_knight-1')).toBeInTheDocument()
    expect(screen.getByTestId('operator-skill-mastery-char_123_knight-1')).toHaveTextContent('2')
    expect(calls).not.toContain('/api/account/assets/skill/skchr_amiya_2')

    // 技能栏整列未知（skill_ids 与 masteries 都是 null）时不画槽位：画空框等于宣称
    // 「这名干员有几个技能」，而这条事实我们并没有。
    cleanup()
    renderCard(operatorRow({ char_id: 'char_4093_frston', name: 'Friston-3', skill_ids: null, masteries: null }))
    expect(screen.queryByTestId('operator-skill-char_4093_frston-1')).not.toBeInTheDocument()
    expect(screen.getByText(/专精未识别/)).toBeInTheDocument()
  })

  it('未拥有：整卡灰显、不加载头像与技能图标、也不画技能与模组', async () => {
    const { calls } = stubAssets()
    const { card, row } = renderCard(
      operatorRow({ char_id: 'char_010_chen', name: '陈', own: false, skill_ids: ['skchr_chen_1'], modules: { X: 2 } }),
    )

    expect(card.className).toContain('grayscale')
    expect(card.className).toContain('opacity-70')
    expect(screen.queryByTestId('operator-avatar-char_010_chen')).not.toBeInTheDocument()
    expect(screen.queryByTestId('operator-skill-char_010_chen-1')).not.toBeInTheDocument()
    expect(screen.queryByTestId('operator-module-char_010_chen-X')).not.toBeInTheDocument()
    expect(calls).toHaveLength(0)
    expect(within(row).getByText('未拥有')).toBeInTheDocument()
  })

  it('稀有度色带走的是主色：6★ 橙金，未拥有优先用灰', () => {
    stubAssets()
    const { card } = renderCard()
    expect(card.style.getPropertyValue('--card-primary')).toBe('#FF8A3D')
    expect(card.style.getPropertyValue('--card-secondary')).toBe('#FFD24A')

    cleanup()
    const { card: grey } = renderCard(operatorRow({ char_id: 'char_010_chen', name: '陈', own: false, rarity: 6 }))
    expect(grey.style.getPropertyValue('--card-primary')).toBe('#4B5563')
  })

  it('模组：优先显示 module_details 的 label+等级，只有 modules 时退化成类型字母', () => {
    stubAssets()
    renderCard()
    const chip = screen.getByTestId('operator-module-char_002_amiya-Y')
    expect(chip).toHaveTextContent('CCR-Y')
    expect(within(chip).getByTestId('operator-module-level-char_002_amiya-Y')).toHaveTextContent('3')

    cleanup()
    renderCard(operatorRow({ char_id: 'char_102_texas', name: '德克萨斯', modules: { X: 2 }, module_details: null }))
    const fallback = screen.getByTestId('operator-module-char_102_texas-X')
    expect(fallback).toHaveTextContent('X')
    expect(fallback).toHaveTextContent('2')
    expect(fallback).not.toHaveTextContent('CCR')

    // 等级 0 是「有这模组但没升级」：不显示 chip（与 moduleText 的口径一致）。
    cleanup()
    renderCard(operatorRow({ char_id: 'char_103_angel', name: '能天使', modules: { X: 0 }, module_details: null }))
    expect(screen.queryByTestId('operator-module-char_103_angel-X')).not.toBeInTheDocument()
  })

  it('专精 0 不画角标，专精 3 画「3」', () => {
    stubAssets()
    renderCard(
      operatorRow({ char_id: 'char_002_amiya', name: '阿米娅', masteries: { '1': 3, '2': 0 }, skill_ids: ['a', 'b'] }),
    )

    expect(screen.getByTestId('operator-skill-mastery-char_002_amiya-1')).toHaveTextContent('3')
    expect(screen.queryByTestId('operator-skill-mastery-char_002_amiya-2')).not.toBeInTheDocument()
  })

  it('M35：未识别在卡片上说清楚，练度原文写「未识别」而不是 0 / —', () => {
    stubAssets()
    const { row } = renderCard(
      operatorRow({
        char_id: 'char_4093_frston',
        name: 'Friston-3',
        masteries: null,
        modules: null,
        skill_level: null,
      }),
    )

    expect(within(row).getByText(/专精未识别/)).toBeInTheDocument()
    expect(within(row).getByText(/模组未识别/)).toBeInTheDocument()
    expect(within(row).getByText('技能等级').nextElementSibling).toHaveTextContent('未识别')
    expect(within(row).getByText('模组').nextElementSibling).toHaveTextContent('未识别')
  })

  it('字段来源徽章：M47 之后只按真实来源渲染「森空岛」', () => {
    stubAssets()
    const { row } = renderCard(
      operatorRow({
        char_id: 'char_002_amiya',
        name: '阿米娅',
        field_sources: { elite: 'skland', masteries: 'skland' },
      }),
    )

    const badge = within(row).getByTestId('operator-source-char_002_amiya-skland')
    expect(badge).toHaveTextContent('森空岛')
    expect(within(badge).getByTitle('由森空岛账号数据同步写入')).toBeInTheDocument()
  })
})
