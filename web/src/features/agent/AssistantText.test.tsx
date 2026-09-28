import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AssistantText } from './MessageViews'

afterEach(cleanup)

/**
 * 这条回复是从真实运行的 Agent 抓下来的原文（deepseek-flash，含二级标题、无序列表、
 * 加粗、行内代码、bash 代码块与链接），用来锁住「接入后确实渲染成元素」：
 * 单元测试只覆盖解析器，这里覆盖 `AssistantText` → `Markdown` 的整条渲染链。
 */
const LIVE_REPLY = [
  '## 回答说明',
  '',
  '本次未调用任何工具，也未对设备或流水线执行任何操作，因此下面的内容仅是一份 Markdown 排版示例。',
  '',
  '- 第一个要点：本回复没有提交队列、没有重启内核、没有进行截图。',
  '- 第二个要点：`resolve_stage`、`submit_pipeline` 等工具均未被调用。',
  '- 第三个要点：如需真实执行任务，请另行告知具体需求。',
  '',
  '上面的列表用于演示格式。**注意：我无法在此处声称任何设备操作成功**，一切以工具返回结果为准。',
  '',
  '行内代码示例：`MaaCore` 与设备状态需通过工具查询确认。',
  '',
  '```bash',
  '# 仅作 Markdown 格式演示，未在本次会话中运行',
  'adb devices',
  'echo "示例输出"',
  '```',
  '',
  '参考链接：[https://example.com](https://example.com)',
].join('\n')

describe('AssistantText renders an agent reply as markdown', () => {
  it('turns the live reply into elements instead of literal markers', () => {
    const { container } = render(<AssistantText text={LIVE_REPLY} messageId={42} />)

    // 结构：标题、列表、代码块都成了真实元素。
    expect(screen.getByRole('heading', { level: 2, name: '回答说明' })).toBeInTheDocument()
    expect(container.querySelectorAll('li')).toHaveLength(3)
    expect(container.querySelector('pre')?.textContent).toBe(
      '# 仅作 Markdown 格式演示，未在本次会话中运行\nadb devices\necho "示例输出"',
    )

    // 行内标记：加粗与行内代码各有元素，且正文里不再残留标记符号。
    expect(screen.getByText(/我无法在此处声称任何设备操作成功/).tagName).toBe('STRONG')
    expect(screen.getByText('resolve_stage').tagName).toBe('CODE')
    expect(container.textContent).not.toContain('**')
    expect(container.textContent).not.toContain('##')

    // 链接：可点击且带安全属性。
    const link = screen.getByRole('link', { name: 'https://example.com' })
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'))

    // 无障碍标签与 message id 仍然保留（原实现的行为不能丢）。
    expect(screen.getByText('Agent：')).toBeInTheDocument()
    expect(container.firstElementChild).toHaveAttribute('data-message-id', '42')
  })

  it('renders a half-streamed reply without losing the text', () => {
    // 流式中间态：斜体标记与代码块都还没闭合。
    const { container } = render(<AssistantText text={'正在**处理\n\n```bash\nadb devi'} />)

    expect(container.textContent).toContain('正在**处理')
    expect(container.querySelector('pre')?.textContent).toBe('adb devi')
  })
})
