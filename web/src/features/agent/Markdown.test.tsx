import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Markdown } from './markdown.tsx'

afterEach(cleanup)

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
