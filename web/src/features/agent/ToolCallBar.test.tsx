import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ToolCallBar } from './ToolCallBar'
import type { ToolView } from './transcript'

const STAGE = {
  code: '1-7',
  name: '暴君',
  stage_id: 'main_01-07',
  width: 2,
  height: 1,
  candidates: 1,
  tiles: [['#00', '.00']],
  start: [[0, 0]],
  end: [[1, 0]],
  legend: [{ code: '#00', label: '阻挡' }],
}

const CHART = {
  type: 'chart',
  caption: '最近的掉落',
  chart: {
    chart_type: 'bar',
    title: '1-7 掉落',
    categories: ['固源岩'],
    series: [{ name: '数量', values: [12] }],
    stacked: false,
    x_label: '物品',
    y_label: '个数',
    unit: '个',
    source: 'get_drop_stats(stage=1-7)',
  },
}

/** `get_screenshot` 归档后的结果：只有截图字段，没有 `media`（M23 收尾修复 B）。 */
const SHA = 'f'.repeat(64)
const ARCHIVED_SCREENSHOT = {
  screenshot_id: 'shot-9',
  sha256: SHA,
  full_url: `/api/images/${SHA}/full`,
  thumb_url: `/api/images/${SHA}/thumb`,
  width: 1280,
  height: 720,
  format: 'jpeg',
}

function tool(overrides: Partial<ToolView> = {}): ToolView {
  return {
    name: 'send_stage_map',
    arguments: { stage: '1-7' },
    result: { result: { media: [{ type: 'stage_map', caption: '1-7 的布局', stage: STAGE }] } },
    status: 'done',
    standalone: false,
    ...overrides,
  }
}

/** 附件取图走 api 客户端的鉴权 fetch：三种白名单地址都按最小 blob 作答。 */
function stubImages() {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push(`${url.pathname}${url.search}`)
    return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } })
  }))
  return requests
}

describe('ToolCallBar', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders the media card above the collapsed details, never inside it', () => {
    const { container } = render(<ToolCallBar tool={tool()} />)
    const details = container.querySelector('details')!
    const cards = container.querySelector('[data-testid="media-cards"]')

    expect(cards).not.toBeNull()
    // 工具条默认折叠：卡片放在里面等于看不到，所以必须在外面、且在上方。
    expect(details).not.toHaveAttribute('open')
    expect(details.querySelector('[data-testid="media-cards"]')).toBeNull()
    expect(cards!.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('发送关卡示意图')).toBeInTheDocument()
    expect(screen.getByText('暴君')).toBeInTheDocument()
  })

  it('keeps every send_* media card outside the details', () => {
    // `send_chart` 之类的卡片是「发给用户看」的内容，与截图附件相反。
    const { container } = render(<ToolCallBar tool={tool({
      name: 'send_chart',
      arguments: { chart_type: 'bar' },
      result: { result: { media: [CHART] } },
    })} />)
    const cards = container.querySelector('[data-testid="media-cards"]')!
    expect(cards).not.toBeNull()
    expect(cards.closest('details')).toBeNull()
    expect(container.querySelector('[data-testid="tool-attachment"]')).toBeNull()
    expect(screen.getByText('1-7 掉落')).toBeInTheDocument()
  })

  it('shows a screenshot archive as an attachment inside the details, never as a session card', async () => {
    const requests = stubImages()
    const { container } = render(<ToolCallBar tool={tool({
      name: 'get_screenshot',
      arguments: {},
      result: { result: ARCHIVED_SCREENSHOT },
    })} />)

    // 用户裁决：`get_screenshot` 不再往会话里放大图。
    expect(container.querySelector('[data-testid="media-cards"]')).toBeNull()

    const details = container.querySelector('details')!
    const attachment = details.querySelector('[data-testid="tool-attachment"]')
    expect(attachment).not.toBeNull()
    const attachmentView = within(attachment as HTMLElement)
    expect(attachmentView.getByText('附件')).toBeInTheDocument()
    // 附件排在「参数」之前。
    const text = details.textContent ?? ''
    expect(text.indexOf('附件')).toBeGreaterThanOrEqual(0)
    expect(text.indexOf('附件')).toBeLessThan(text.indexOf('参数'))

    // 缩略图走白名单地址懒加载（jsdom 没有 IntersectionObserver，直接取）。
    const preview = await attachmentView.findByRole('img', { name: '游戏截图' })
    expect(preview).toHaveAttribute('src')
    expect(requests).toEqual([`/api/images/${SHA}/thumb`])

    // 点开进既有 ImageViewer 看原图。
    fireEvent.click(attachmentView.getByRole('button', { name: '查看大图：游戏截图' }))
    const viewer = await screen.findByRole('dialog')
    expect(within(viewer).getByRole('img', { name: '游戏截图' })).toBeInTheDocument()
    await waitFor(() => expect(requests).toContain(`/api/images/${SHA}/full`))
  })

  it('keeps the arguments and result JSON inside the details block', () => {
    const { container } = render(<ToolCallBar tool={tool()} />)
    const details = container.querySelector('details')!
    expect(details).toHaveTextContent('参数')
    expect(details).toHaveTextContent('结果')
    expect(details).toHaveTextContent('send_stage_map')
    expect(details).toHaveTextContent('main_01-07')
  })

  it('keeps the Chinese action name on the bar and folds the raw tool name and arguments away', () => {
    const { container } = render(<ToolCallBar tool={tool()} />)
    const details = container.querySelector('details')!
    const bar = details.querySelector('summary')!
    // 折叠条上只有中文动作名，英文函数名留给展开区。
    expect(bar).toHaveTextContent('发送关卡示意图')
    expect(bar).not.toHaveTextContent('send_stage_map')
    expect(within(details).getByText('send_stage_map')).toBeInTheDocument()
    // 参数只报个数，JSON 折在二级折叠区里。
    const argumentsSummary = within(details).getByText('这次调用带了 1 个参数')
    expect(argumentsSummary.closest('details')).not.toBe(details)
    expect(within(details).getByText(/"stage": "1-7"/)).toBeInTheDocument()
  })

  it('renders no media card for a tool result without media', () => {
    const { container } = render(<ToolCallBar tool={tool({ name: 'get_logs', result: { result: { items: [], total: 0 } } })} />)
    expect(container.querySelector('[data-testid="media-cards"]')).toBeNull()
    expect(container.querySelector('[data-testid="tool-attachment"]')).toBeNull()
    expect(container.querySelector('details')).toBeInTheDocument()
    expect(screen.getByText('读取日志')).toBeInTheDocument()
  })
})
