import { describe, expect, it } from 'vitest'
import { parseMarkdown, type MarkdownBlock, type MarkdownInline } from './markdown.tsx'

/** 把行内节点压成便于断言的形状：`**粗**` → `['bold', '粗']`。 */
function inlineOf(block: MarkdownBlock): unknown {
  if (block.kind === 'table') {
    return [...block.header, ...block.rows.flat()].map((cell) => cell.map(compact))
  }
  return 'children' in block ? block.children.map(compact) : null
}

function compact(node: MarkdownInline): unknown {
  switch (node.kind) {
    case 'text':
      return node.text
    case 'code':
      return ['code', node.text]
    case 'strong':
      return ['bold', node.children.map(compact)]
    case 'em':
      return ['italic', node.children.map(compact)]
    case 'del':
      return ['strike', node.children.map(compact)]
    case 'link':
      return ['link', node.href, node.children.map(compact)]
  }
}

describe('block structure', () => {
  it('parses paragraphs, headings, fenced code, lists and quotes', () => {
    const blocks = parseMarkdown(
      [
        '# 标题一',
        '',
        '第一段**加粗**文字。',
        '第二行还是同一段。',
        '',
        '- 列表项 A',
        '- 列表项 B',
        '',
        '1. 第一步',
        '2. 第二步',
        '',
        '> 引用一行',
        '',
        '```python',
        'print("hi")',
        '```',
      ].join('\n'),
    )

    expect(blocks.map((block) => block.kind)).toEqual([
      'heading', 'paragraph', 'list', 'list', 'blockquote', 'code',
    ])
    expect(blocks[0]).toMatchObject({ kind: 'heading', level: 1 })
    expect(inlineOf(blocks[1])).toEqual(['第一段', ['bold', ['加粗']], '文字。\n第二行还是同一段。'])
    expect(blocks[2]).toMatchObject({ kind: 'list', ordered: false })
    expect(blocks[3]).toMatchObject({ kind: 'list', ordered: true, start: 1 })
    expect(blocks[5]).toMatchObject({ kind: 'code', language: 'python', text: 'print("hi")' })
  })

  it('treats a single newline inside a paragraph as a line break, matching pre-wrap today', () => {
    const [paragraph] = parseMarkdown('第一行\n第二行')
    expect(inlineOf(paragraph)).toEqual(['第一行\n第二行'])
  })
})

describe('inline structure', () => {
  it('parses inline code before emphasis so asterisks inside code stay literal', () => {
    const [paragraph] = parseMarkdown('用 `a ** b` 表示，**重点**，*斜体*，~~删除~~。')
    expect(inlineOf(paragraph)).toEqual([
      '用 ',
      ['code', 'a ** b'],
      ' 表示，',
      ['bold', ['重点']],
      '，',
      ['italic', ['斜体']],
      '，',
      ['strike', ['删除']],
      '。',
    ])
  })

  it('keeps the underscore form of emphasis working', () => {
    const [paragraph] = parseMarkdown('__粗__ 与 _斜_')
    expect(inlineOf(paragraph)).toEqual([['bold', ['粗']], ' 与 ', ['italic', ['斜']]])
  })

  it('parses links and keeps the label', () => {
    const [paragraph] = parseMarkdown('见 [文档](https://example.com/a?b=1)。')
    expect(inlineOf(paragraph)).toEqual(['见 ', ['link', 'https://example.com/a?b=1', ['文档']], '。'])
  })

  it('keeps unknown or partial markers as literal text', () => {
    const [paragraph] = parseMarkdown('价格 3 * 4 = 12，a_b_c 不是斜体。')
    expect(inlineOf(paragraph)).toEqual(['价格 3 * 4 = 12，a_b_c 不是斜体。'])
  })
})

describe('streaming safety', () => {
  it('renders an unterminated code fence as a code block instead of dropping it', () => {
    const blocks = parseMarkdown('说明：\n\n```bash\ngit status')
    expect(blocks.map((block) => block.kind)).toEqual(['paragraph', 'code'])
    expect(blocks[1]).toMatchObject({ kind: 'code', language: 'bash', text: 'git status' })
  })

  it('keeps a dangling emphasis marker visible while the reply is still streaming', () => {
    const [paragraph] = parseMarkdown('正在**处理')
    expect(inlineOf(paragraph)).toEqual(['正在**处理'])
  })

  it('does not crash or drop content on partial inline code and links', () => {
    expect(inlineOf(parseMarkdown('看 `git sta')[0])).toEqual(['看 ', ['code', 'git sta']])
    expect(inlineOf(parseMarkdown('见 [文档](https://exa')[0])).toEqual([
      '见 ',
      ['link', 'https://exa', ['文档']],
    ])
  })
})

describe('safety', () => {
  it('never emits raw html nodes for html in the reply', () => {
    const [paragraph] = parseMarkdown('<img src=x onerror=alert(1)> 纯文本')
    expect(inlineOf(paragraph)).toEqual(['<img src=x onerror=alert(1)> 纯文本'])
  })

  it('flags links whose scheme is not http, https or mailto', () => {
    const [paragraph] = parseMarkdown('[点我](javascript:alert(1))')
    expect(inlineOf(paragraph)).toEqual([['link', 'javascript:alert(1)', ['点我']]])
  })
})

describe('tables', () => {
  it('parses a pipe table into header and rows', () => {
    const blocks = parseMarkdown(
      [
        '| 项 | 值 |',
        '|---|---|',
        '| 流水线 ID | 57da7202 |',
        '| 状态 | failed |',
      ].join('\n'),
    )
    expect(blocks).toHaveLength(1)
    expect(blocks[0]).toMatchObject({ kind: 'table' })
    const table = blocks[0] as Extract<MarkdownBlock, { kind: 'table' }>
    expect(table.header.map((cell) => cell.map((node) => (node.kind === 'text' ? node.text : '')))).toEqual([
      ['项'],
      ['值'],
    ])
    expect(table.rows).toHaveLength(2)
  })

  it('keeps inline formatting inside cells', () => {
    const blocks = parseMarkdown(
      ['| 项 | 值 |', '|:--|--:|', '| **状态** | `failed` |', '| 耗时 | [日志](https://example.com) |'].join('\n'),
    )
    const table = blocks[0] as Extract<MarkdownBlock, { kind: 'table' }>
    // 单元格是行内节点：粗体与行内代码都要保留结构，而不是变成字面标记。
    expect(table.rows[0].map((cell) => (cell[0] ? compact(cell[0]) : null))).toEqual([
      ['bold', ['状态']],
      ['code', 'failed'],
    ])
    expect(table.rows[1].map((cell) => (cell[0] ? compact(cell[0]) : null))).toEqual([
      '耗时',
      ['link', 'https://example.com', ['日志']],
    ])
  })

  it('accepts tables without outer pipes and pads short rows', () => {
    const blocks = parseMarkdown(['项 | 值', '--- | ---', 'a | b', 'c'].join('\n'))
    const table = blocks[0] as Extract<MarkdownBlock, { kind: 'table' }>
    expect(table.header).toHaveLength(2)
    expect(table.rows).toHaveLength(2)
    // 缺列的行补空单元格，渲染时不会错位。
    expect(table.rows[1]).toHaveLength(2)
  })

  it('does not treat a lone pipe line as a table', () => {
    expect(parseMarkdown('| 只是一条竖线 |').map((block) => block.kind)).toEqual(['paragraph'])
    expect(parseMarkdown('| a | b |\n| c | d |').map((block) => block.kind)).toEqual(['paragraph'])
  })

  it('still renders a table that is only half streamed', () => {
    // 流式中途：表头与分隔行到了、数据行还没到，不该丢内容也不该抛异常。
    const blocks = parseMarkdown('| 项 | 值 |\n|---|---|')
    expect(blocks[0]).toMatchObject({ kind: 'table' })
    const table = blocks[0] as Extract<MarkdownBlock, { kind: 'table' }>
    expect(table.rows).toEqual([])
  })

  it('parses a leading-pipe header even when the delimiter row is compact', () => {
    // 真实回复的两种写法混在一起：表头带外框（`| a | b |` 同时符合 `|` 列表项的形状），
    // 分隔行却是紧凑的 `|---|---|`。两种都不能让表格退化成原文。
    for (const delimiter of ['|---|---|', '| --- | --- |']) {
      const blocks = parseMarkdown(['| 项 | 值 |', delimiter, '| a | b |'].join('\n'))
      expect(blocks.map((block) => block.kind), delimiter).toEqual(['table'])
    }
  })

  it('starts a table that directly follows a paragraph without a blank line', () => {
    // 模型常在 `**小标题**` 后紧接表头、不空行；表格必须自成一块，正文不能把它吞掉。
    const blocks = parseMarkdown(['**结果**', '| 项 | 值 |', '|---|---|', '| a | b |'].join('\n'))
    expect(blocks.map((block) => block.kind)).toEqual(['paragraph', 'table'])
    expect(inlineOf(blocks[0])).toEqual([['bold', ['结果']]])
  })
})

