import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from '@/lib/clipboard'
import { Markdown } from './markdown.tsx'

// 与 api-console 的测试同一口径：剪贴板在 jsdom 里没有实现，直接替身掉。
vi.mock('@/lib/clipboard', () => ({ copyText: vi.fn(async () => true) }))

const mockedCopy = vi.mocked(copyText)

afterEach(() => {
  cleanup()
  mockedCopy.mockClear()
  mockedCopy.mockResolvedValue(true)
})

describe('Markdown component', () => {
  it('renders emphasis, code and lists as real elements, not literal markers', () => {
    const { container } = render(
      <Markdown text={'**重要**：先停止流水线。\n\n- 第一步\n- 第二步\n\n`maa_api/agent`'} />,
    )

    expect(screen.getByText('重要').tagName).toBe('STRONG')
    expect(screen.getByText('maa_api/agent').tagName).toBe('CODE')
    expect(container.querySelectorAll('li')).toHaveLength(2)
    expect(container.textContent).not.toContain('**')
    expect(container.textContent).not.toContain('`')
  })

  it('renders fenced code as a pre block and keeps the newlines', () => {
    const { container } = render(<Markdown text={'```bash\ngit status\nls\n```'} />)
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.textContent).toBe('git status\nls')
  })

  it('renders http links but refuses to make a javascript: link clickable', () => {
    const { container } = render(
      <Markdown text={'[文档](https://example.com) 和 [点我](javascript:alert(1))'} />,
    )
    const anchor = screen.getByRole('link', { name: '文档' })
    expect(anchor).toHaveAttribute('href', 'https://example.com')
    expect(anchor).toHaveAttribute('rel', expect.stringContaining('noreferrer'))
    expect(container.querySelectorAll('a')).toHaveLength(1)
    expect(screen.getByText('点我').tagName).not.toBe('A')
  })

  it('escapes html in the reply instead of interpreting it', () => {
    const { container } = render(<Markdown text={'<img src=x onerror=alert(1)>'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})

describe('code block copy button', () => {
  it('copies the fenced code verbatim and confirms visibly', async () => {
    const code = '# 演示\nadb devices\necho "示例输出"'
    render(<Markdown text={`\`\`\`bash\n${code}\n\`\`\``} />)

    // 只复制代码本身，不含语言标签与多余空行。
    const button = screen.getByRole('button', { name: '复制代码' })
    fireEvent.click(button)

    await waitFor(() => expect(mockedCopy).toHaveBeenCalledWith(code))
    expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument()
    expect(screen.getByText('已复制')).toBeInTheDocument()
  })

  it('reports a failed copy instead of pretending it worked', async () => {
    mockedCopy.mockResolvedValueOnce(false)
    render(<Markdown text={'```\nadb devices\n```'} />)

    fireEvent.click(screen.getByRole('button', { name: '复制代码' }))

    expect(await screen.findByRole('button', { name: '复制失败' })).toBeInTheDocument()
    expect(screen.getByText('复制失败')).toBeInTheDocument()
  })

  it('offers one button per code block and none for inline code', () => {
    const { container } = render(
      <Markdown text={'行内 `MaaCore`\n\n```bash\nls\n```\n\n```json\n{}\n```'} />,
    )

    expect(screen.getAllByRole('button', { name: '复制代码' })).toHaveLength(2)
    expect(container.querySelectorAll('pre')).toHaveLength(2)
  })

  it('keeps the language label outside the copied body', () => {
    const { container } = render(<Markdown text={'```python\nprint(1)\n```'} />)
    expect(container.querySelector('pre')?.textContent).toBe('print(1)')
    expect(screen.getByText('python')).toBeInTheDocument()
  })
})

describe('tables', () => {
  it('renders a pipe table as real table elements', () => {
    const { container } = render(
      <Markdown
        text={[
          '| 项 | 值 |',
          '|---|---|',
          '| 流水线 ID | 57da7202 |',
          '| 状态 | **failed** |',
        ].join('\n')}
      />,
    )

    const table = container.querySelector('table')
    expect(table).not.toBeNull()
    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
    expect(screen.getByRole('columnheader', { name: '项' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '57da7202' })).toBeInTheDocument()
    expect(screen.getByText('failed').tagName).toBe('STRONG')
    // 竖线与分隔行不该再以原文出现。
    expect(container.textContent).not.toContain('|---|')
    expect(container.textContent).not.toContain('| 项 |')
  })

  it('wraps a wide table so the page itself does not scroll sideways', () => {
    const { container } = render(
      <Markdown text={'| a | b |\n|---|---|\n| 1 | 2 |'} />,
    )
    const wrapper = container.querySelector('table')?.parentElement
    expect(wrapper?.className).toContain('overflow-x-auto')
  })
})

