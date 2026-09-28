import { describe, expect, it } from 'vitest'
import { parseMarkdown, type MarkdownBlock, type MarkdownInline } from './markdown.tsx'

/** 把块里所有可见文本拼起来，用于判断「有没有把内容吞掉」。 */
function visibleText(blocks: readonly MarkdownBlock[]): string {
  const inline = (nodes: readonly MarkdownInline[]): string =>
    nodes
      .map((node) => {
        if (node.kind === 'text') return node.text
        if (node.kind === 'code') return node.text
        if (node.kind === 'link') return inline(node.children)
        return inline(node.children)
      })
      .join('')

  return blocks
    .map((block) => {
      switch (block.kind) {
        case 'paragraph':
        case 'heading':
        case 'blockquote':
          return inline(block.children)
        case 'list':
          return block.items.map(inline).join('\n')
        case 'code':
          return block.text
      }
    })
    .join('\n')
}

/** 用真实形态的回复样本做输入。 */
const SAMPLES = [
  '当前设备在线，队列为空。',
  '# 诊断结论\n\n流水线 `fecd51f4` 在 **Fight** 任务失败。\n\n- 关卡：CE-6\n- 原因：理智不足\n\n```bash\nadb shell dumpsys package com.hypergryph.arknights\n```\n\n> 建议先补充理智再重试。',
  '1. 停止流水线\n2. 重新连接设备\n3. 再试一次',
  '截图显示：主界面 | 顶部有搜索栏 | 底部有热门游戏\n\n[查看日志](https://example.com/logs?id=1)',
  '价格 3 * 4 = 12，变量 a_b_c 保持原样，`a ** b` 里的星号也是字面量。',
  '<img src=x onerror=alert(1)> 这段应该当纯文本。',
  '中文与 English 混排，箭头 → 与省略号 …… 都不能丢。',
  '空行分段\n\n\n\n以及多个连续空行。',
]

describe('robustness on real replies', () => {
  /**
   * 解析会把标记符号规范掉，所以不能整段比对。这里逐词检查：正文的每个词都要还在，
   * 只有下面这些**结构性标记**允许消失（它们变成了元素属性或列表序号）。
   */
  const NORMALIZED = new Set([
    '#', '##', '-', '1.', '2.', '3.', '```', '```bash', '>', '|', '**', '`', '*', '~~',
    'bash', 'https://example.com/logs?id=1',
  ])

  it.each(SAMPLES)('never throws and keeps the visible text of %#', (sample) => {
    const blocks = parseMarkdown(sample)
    expect(Array.isArray(blocks)).toBe(true)
    const text = visibleText(blocks)
    const words = sample
      .split(/[\s|#*`>\[\]()~-]+/)
      .map((part) => part.trim())
      .filter((part) => part.length >= 2 && !NORMALIZED.has(part))
    expect(words.length).toBeGreaterThan(0)
    for (const word of words) {
      expect(text).toContain(word)
    }
  })

  it('survives every prefix of a streaming reply without throwing', () => {
    const full = SAMPLES[1]
    for (let end = 1; end <= full.length; end += 1) {
      const prefix = full.slice(0, end)
      expect(() => parseMarkdown(prefix), `prefix length ${end}`).not.toThrow()
    }
  })

  it('keeps a lone backtick visible instead of swallowing it', () => {
    // 流式中「行内代码刚开出第一个反引号」的中间态：不能渲染成空的 <code> 把反引号吃掉。
    for (const partial of ['`', '``', '用 ` 表示']) {
      const text = visibleText(parseMarkdown(partial))
      expect(text, JSON.stringify(partial)).toContain('`')
    }
  })

  it('survives truncation in the middle of every marker it knows', () => {
    const partials = [
      '**', '*', '```', '```ba', '#', '##', '- ', '1. ', '> ', '[', '[]', '[a]', '[a](',
      '[a](http', '~~', '_', '__', '[a](https://example.com',
    ]
    for (const partial of partials) {
      expect(() => parseMarkdown(partial), JSON.stringify(partial)).not.toThrow()
    }
  })

  it('treats whitespace-only replies as empty, not as content', () => {
    for (const blank of ['', '\n\n', '   ', '\t']) {
      expect(visibleText(parseMarkdown(blank))).toBe('')
    }
  })
})

describe('the language label never leaks into the code body', () => {
  it('keeps the fence body exactly, whatever the info string looks like', () => {
    const blocks = parseMarkdown('```python title=x\nprint(1)\n```')
    const code = blocks.find((block) => block.kind === 'code')
    expect(code).toMatchObject({ kind: 'code', text: 'print(1)' })
  })
})
