import { afterEach, describe, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
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

describe('ToolCallBar', () => {
  afterEach(cleanup)

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

  it('keeps the arguments and result JSON inside the details block', () => {
    const { container } = render(<ToolCallBar tool={tool()} />)
    const details = container.querySelector('details')!
    expect(details).toHaveTextContent('参数')
    expect(details).toHaveTextContent('结果')
    expect(details).toHaveTextContent('send_stage_map')
    expect(details).toHaveTextContent('main_01-07')
  })

  it('renders no media card for a tool result without media', () => {
    const { container } = render(<ToolCallBar tool={tool({ name: 'get_logs', result: { result: { items: [], total: 0 } } })} />)
    expect(container.querySelector('[data-testid="media-cards"]')).toBeNull()
    expect(container.querySelector('details')).toBeInTheDocument()
    expect(screen.getByText('读取日志')).toBeInTheDocument()
  })
})
