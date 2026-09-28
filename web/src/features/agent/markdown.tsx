import { Fragment, useMemo } from 'react'

/**
 * 自研的、流式安全的 Markdown 解析与渲染（不依赖任何第三方库）。
 *
 * 面向的场景是 Agent 回复的**逐字流式输出**：SSE 的 `text_delta` 一片片到达，
 * 中间态可能是 `正在**处理`、未闭合的 ` ``` ` 代码块、半截的 `` `git sta `` 或
 * `[文档](https://exa`。因此这里的铁律是：
 *
 * 1. **不抛异常**：所有匹配失败都退化成字面文本，绝不让解析器崩掉整棵组件树。
 * 2. **不丢内容**：任何未闭合的标记都必须把已到达的字符留在结果里（未闭合的
 *    围栏/行内代码当成代码节点，未闭合的强调标记当字面文本）。
 * 3. **纯数据到真实元素**：解析只产出 `MarkdownBlock` / `MarkdownInline` 描述
 *    对象，渲染只生成真实 React 元素，任何情况下都不产生 HTML 字符串
 *    （`<img onerror=...>` 一律当文本）。
 *
 * 只覆盖 Agent 实际会输出的子集：段落、ATX 标题、无序/有序列表、围栏代码块、
 * 引用块、行内代码、粗体、斜体、删除线、链接。表格、嵌套列表、图片、HTML 块
 * 都不在范围内。
 *
 * 解析器与组件放在同一个文件里，是为了绕开 macOS「大小写不敏感文件系统」的坑：
 * 若拆成 `markdown.ts` + `Markdown.tsx`，Vite 的解析顺序里 `.ts` 排在 `.tsx`
 * 之前，`import { Markdown } from './Markdown'` 会命中解析器模块而拿不到组件。
 * 统一由 `./markdown.tsx` 提供全部导出（类型、parseMarkdown、Markdown 组件）。
 */

export type MarkdownInline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; children: MarkdownInline[] }
  | { kind: 'em'; children: MarkdownInline[] }
  | { kind: 'del'; children: MarkdownInline[] }
  | { kind: 'link'; href: string; children: MarkdownInline[] }

export type MarkdownBlock =
  | { kind: 'paragraph'; children: MarkdownInline[] }
  | { kind: 'heading'; level: number; children: MarkdownInline[] }
  | { kind: 'list'; ordered: boolean; start: number; items: MarkdownInline[][] }
  | { kind: 'blockquote'; children: MarkdownInline[] }
  | { kind: 'code'; language: string | null; text: string }

/** ASCII 标点：CommonMark 里反斜杠只能转义这些字符，也是「标点」判定的一部分。 */
const ASCII_PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"

/** Unicode 标点（CommonMark 的 punctuation 取 P 类；`（`、`，` 这类全角标点也算）。 */
const UNICODE_PUNCTUATION = /\p{P}/u

const WHITESPACE = /\s/

const BLANK_LINE = /^\s*$/

/** 围栏代码块的开头：最多 3 个空格缩进，三个及以上的反引号或波浪号。 */
const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})(.*)$/

/** 围栏代码块的结尾：只有围栏字符，不能带 info string。 */
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/

/** ATX 标题：`# 标题`。 */
const HEADING = /^ {0,3}(#{1,6})(.*)$/

/** 引用块：`> 内容`。 */
const BLOCKQUOTE = /^ {0,3}> ?(.*)$/

/** 列表项：`- 内容` / `1. 内容` / `1) 内容`。 */
const LIST_ITEM = /^( {0,3})([-*+]|\d{1,9}[.)])([ \t]+)(.*)$/

/* -------------------------------------------------------------------------- */
/* 字符分类                                                                    */
/* -------------------------------------------------------------------------- */

function isWhitespace(character: string | undefined): boolean {
  // 文本边界等同于空白：`**粗**` 开头/结尾的定界符才能成立。
  return character === undefined || WHITESPACE.test(character)
}

function isPunctuation(character: string | undefined): boolean {
  if (character === undefined) return false
  return ASCII_PUNCTUATION.includes(character) || UNICODE_PUNCTUATION.test(character)
}

/** 只有 ASCII 标点前的反斜杠才是转义；`C:\Users` 这类路径要原样保留。 */
function isEscapable(character: string | undefined): boolean {
  return character !== undefined && ASCII_PUNCTUATION.includes(character)
}

/** 返回从 `start` 开始连续重复 `character` 的结束下标（不含）。 */
function runEnd(source: string, start: number, character: string): number {
  let cursor = start
  while (cursor < source.length && source[cursor] === character) cursor += 1
  return cursor
}

/* -------------------------------------------------------------------------- */
/* 行内解析                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 解析一行（或一段）行内内容。
 *
 * 优先级按需求固定为：行内代码 → 链接 → 粗体 → 斜体 → 删除线。行内代码最先处理，
 * 所以 `` `a ** b` `` 里的星号是字面量；代码段在寻找强调闭合符时也会被整体跳过。
 */
function parseInline(source: string): MarkdownInline[] {
  const nodes: MarkdownInline[] = []
  let pending = ''
  let cursor = 0

  // 相邻的字面文本必须合并成一个 text 节点（断言按节点逐个比对）。
  const flush = () => {
    if (pending !== '') {
      nodes.push({ kind: 'text', text: pending })
      pending = ''
    }
  }

  while (cursor < source.length) {
    const character = source[cursor]

    if (character === '\\' && isEscapable(source[cursor + 1])) {
      pending += source[cursor + 1]
      cursor += 2
      continue
    }

    if (character === '`') {
      const opened = runEnd(source, cursor, '`')
      const span = matchCodeSpan(source, opened, opened - cursor)
      if (span) {
        flush()
        nodes.push({ kind: 'code', text: span.text })
        cursor = span.next
      } else if (!isWhitespace(source[opened])) {
        // 未闭合、但后面紧跟正文（流式中的 `` `git sta ``）：把剩余内容整体当代码，
        // 既立刻可见又不丢字。
        flush()
        nodes.push({ kind: 'code', text: source.slice(opened) })
        cursor = source.length
      } else {
        // 未闭合、且后面是空白或已到文本末尾（`` ` ``、`` `` ``、`` 用 ` 表示 ``）：
        // 当字面文本保留，否则会渲染出一个空 <code> 并把它后面的字吃掉。
        pending += source.slice(cursor, opened)
        cursor = opened
      }
      continue
    }

    if (character === '[') {
      const link = matchLink(source, cursor)
      if (link) {
        flush()
        nodes.push(link.node)
        cursor = link.next
        continue
      }
    }

    if (character === '*' || character === '_' || character === '~') {
      const emphasis = matchEmphasis(source, cursor)
      if (emphasis) {
        flush()
        nodes.push(emphasis.node)
        cursor = emphasis.next
        continue
      }
    }

    pending += character
    cursor += 1
  }

  flush()
  return nodes
}

/** 行内代码：定界符必须等长闭合；没找到就交给调用方按流式中间态处理。 */
function matchCodeSpan(source: string, from: number, length: number): { text: string; next: number } | null {
  let cursor = from
  while (cursor < source.length) {
    if (source[cursor] !== '`') {
      cursor += 1
      continue
    }
    const end = runEnd(source, cursor, '`')
    if (end - cursor === length) {
      return { text: normalizeCodeSpan(source.slice(from, cursor)), next: end }
    }
    cursor = end
  }
  return null
}

/** CommonMark 规则：两端各有一个空格且内容不全为空格时，各去掉一个。 */
function normalizeCodeSpan(raw: string): string {
  if (raw.length >= 2 && raw.startsWith(' ') && raw.endsWith(' ') && raw.trim() !== '') {
    return raw.slice(1, -1)
  }
  return raw
}

/** `[标签](目标)`：标签里的行内语法照常解析，目标原样交给渲染层判安全。 */
function matchLink(source: string, start: number): { node: MarkdownInline; next: number } | null {
  let cursor = start + 1
  while (cursor < source.length) {
    const character = source[cursor]
    if (character === '\\' && isEscapable(source[cursor + 1])) {
      cursor += 2
      continue
    }
    // 不支持嵌套方括号：整段交回主循环按字面处理，内容不会丢。
    if (character === '[') return null
    if (character === ']') break
    cursor += 1
  }
  if (cursor >= source.length) return null
  if (source[cursor + 1] !== '(') return null

  const target = scanLinkTarget(source, cursor + 2)
  if (!target) return null

  return {
    node: { kind: 'link', href: target.href, children: parseInline(source.slice(start + 1, cursor)) },
    next: target.next,
  }
}

/**
 * 扫描 `(...)` 里的链接目标。
 *
 * 需要处理两种流式中间态：
 * - 括号还没闭合（`[文档](https://exa`）：把到文本末尾的内容当目标，链接立即可见；
 * - 目标里出现空白（说明后面还有正文，或者这是个带 title 的写法）：**放弃**这次
 *   匹配，交回主循环按字面文本输出，绝不把后续正文吞进 href。
 */
function scanLinkTarget(source: string, from: number): { href: string; next: number } | null {
  let cursor = from
  let depth = 0

  while (cursor < source.length) {
    const character = source[cursor]

    if (character === '\\' && isEscapable(source[cursor + 1])) {
      cursor += 2
      continue
    }
    if (character === '(') {
      depth += 1
      cursor += 1
      continue
    }
    if (character === ')') {
      // 允许目标里出现成对的括号，例如 `javascript:alert(1)`。
      if (depth === 0) return normalizeTarget(source.slice(from, cursor), cursor + 1)
      depth -= 1
      cursor += 1
      continue
    }
    if (isWhitespace(character)) {
      // `[a](url )` 这种尾随空白仍然算合法目标。
      let probe = cursor
      while (probe < source.length && isWhitespace(source[probe])) probe += 1
      if (source[probe] === ')') return normalizeTarget(source.slice(from, cursor), probe + 1)
      return null
    }

    cursor += 1
  }

  // 走到文本末尾仍未闭合：流式中间态，剩余内容都当目标。
  return normalizeTarget(source.slice(from), source.length)
}

function normalizeTarget(raw: string, next: number): { href: string; next: number } | null {
  const href = raw.trim()
  if (href === '' || WHITESPACE.test(href)) return null
  return { href, next }
}

type Delimiter = '*' | '_' | '~'

/** 粗体/斜体/删除线。找不到闭合定界符就返回 null，调用方把标记当字面文本保留。 */
function matchEmphasis(source: string, start: number): { node: MarkdownInline; next: number } | null {
  const character = source[start] as Delimiter
  const opened = runEnd(source, start, character)
  const runLength = opened - start

  // 单个 `~` 不是删除线；`**`/`__` 是粗体，单个是斜体。
  const take = character === '~' ? (runLength >= 2 ? 2 : 0) : runLength >= 2 ? 2 : 1
  if (take === 0) return null

  const before = start > 0 ? source[start - 1] : undefined
  const after = opened < source.length ? source[opened] : undefined
  if (!canOpenDelimiter(character, before, after)) return null

  const closer = findCloser(source, opened, character, take)
  if (!closer) return null

  // 定界符比需要的长（`***粗斜***`）时，多出来的字符留在内部由递归解析处理，
  // 于是 `***x***` 得到 粗体(斜体(x)) 而不是丢掉一层。
  const children = parseInline(source.slice(start + take, closer.start + (closer.length - take)))

  let node: MarkdownInline
  if (character === '~') node = { kind: 'del', children }
  else if (take === 2) node = { kind: 'strong', children }
  else node = { kind: 'em', children }

  return { node, next: closer.start + closer.length }
}

/** 向后找可闭合的定界符；跳过行内代码与转义，避免把代码里的星号当闭合符。 */
function findCloser(
  source: string,
  from: number,
  character: Delimiter,
  take: number,
): { start: number; length: number } | null {
  let cursor = from
  while (cursor < source.length) {
    const current = source[cursor]

    if (current === '\\' && isEscapable(source[cursor + 1])) {
      cursor += 2
      continue
    }
    if (current === '`') {
      const opened = runEnd(source, cursor, '`')
      const span = matchCodeSpan(source, opened, opened - cursor)
      cursor = span ? span.next : source.length
      continue
    }
    if (current !== character) {
      cursor += 1
      continue
    }

    const end = runEnd(source, cursor, character)
    const length = end - cursor
    const before = cursor > 0 ? source[cursor - 1] : undefined
    const after = end < source.length ? source[end] : undefined
    if (length >= take && canCloseDelimiter(character, before, after)) {
      return { start: cursor, length }
    }
    cursor = end
  }
  return null
}

/** 定界符左边是不是「空白或标点」——CommonMark 的 left-flanking。 */
function isLeftFlanking(before: string | undefined, after: string | undefined): boolean {
  if (isWhitespace(after)) return false
  if (!isPunctuation(after)) return true
  return isWhitespace(before) || isPunctuation(before)
}

/** 定界符右边是不是「空白或标点」——CommonMark 的 right-flanking。 */
function isRightFlanking(before: string | undefined, after: string | undefined): boolean {
  if (isWhitespace(before)) return false
  if (!isPunctuation(before)) return true
  return isWhitespace(after) || isPunctuation(after)
}

/**
 * `*` 用标准 flanking 规则；`_` 额外要求不能出现在词内，所以 `a_b_c` / `snake_case`
 * 不会被误判成斜体。`~` 与 `*` 同规则。
 */
function canOpenDelimiter(character: Delimiter, before: string | undefined, after: string | undefined): boolean {
  const left = isLeftFlanking(before, after)
  if (character !== '_') return left
  return left && (!isRightFlanking(before, after) || isPunctuation(before))
}

function canCloseDelimiter(character: Delimiter, before: string | undefined, after: string | undefined): boolean {
  const right = isRightFlanking(before, after)
  if (character !== '_') return right
  return right && (!isLeftFlanking(before, after) || isPunctuation(after))
}

/* -------------------------------------------------------------------------- */
/* 块级解析                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 把 Markdown 源码解析成块级节点。
 *
 * 对未闭合围栏（流式中途的 ` ``` `）的处理：把它当成代码块输出，剩余所有行都算
 * 代码内容，这样用户既能看到「正在写代码」，也不会在中途闪烁出半截正文。
 */
export function parseMarkdown(source: string): MarkdownBlock[] {
  // 统一换行符，避免 `\r` 混进文本节点破坏断言与渲染。
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    if (BLANK_LINE.test(line)) {
      index += 1
      continue
    }

    const fence = matchFence(line)
    if (fence) {
      const body: string[] = []
      let cursor = index + 1
      while (cursor < lines.length && !closesFence(lines[cursor], fence)) {
        body.push(lines[cursor])
        cursor += 1
      }
      // 闭合围栏自身也要吃掉；没找到闭合就说明还在流式输出，剩余行已全部收进 body。
      if (cursor < lines.length) cursor += 1
      blocks.push({ kind: 'code', language: fence.language, text: body.join('\n') })
      index = cursor
      continue
    }

    const heading = matchHeading(line)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading.level, children: parseInline(heading.text) })
      index += 1
      continue
    }

    if (BLOCKQUOTE.test(line)) {
      const quoted: string[] = []
      let cursor = index
      while (cursor < lines.length) {
        const content = BLOCKQUOTE.exec(lines[cursor])?.[1]
        if (content === undefined) break
        quoted.push(content)
        cursor += 1
      }
      trimBlankEdges(quoted)
      blocks.push({ kind: 'blockquote', children: parseInline(quoted.join('\n')) })
      index = cursor
      continue
    }

    const item = matchListItem(line)
    if (item) {
      index = parseList(lines, index, item, blocks)
      continue
    }

    const paragraph: string[] = []
    let cursor = index
    while (cursor < lines.length && !BLANK_LINE.test(lines[cursor]) && !startsBlock(lines[cursor])) {
      paragraph.push(lines[cursor])
      cursor += 1
    }
    // 段内的单个换行原样保留：前端用 whitespace-pre-wrap 渲染，换行不能丢。
    blocks.push({ kind: 'paragraph', children: parseInline(paragraph.join('\n')) })
    index = cursor
  }

  return blocks
}

interface ListItemMatch {
  ordered: boolean
  start: number
  text: string
}

interface FenceMatch {
  character: '`' | '~'
  length: number
  language: string | null
}

/** 从列表的第一项开始收集同类项，返回下一处待处理的行号。 */
function parseList(lines: string[], start: number, first: ListItemMatch, blocks: MarkdownBlock[]): number {
  const items: MarkdownInline[][] = []
  let current: string[] = [first.text]
  let cursor = start + 1

  while (cursor < lines.length) {
    const line = lines[cursor]

    if (BLANK_LINE.test(line)) {
      // 空行不必然结束列表：后面还是同类列表项就继续（松散列表）。
      let probe = cursor
      while (probe < lines.length && BLANK_LINE.test(lines[probe])) probe += 1
      const next = probe < lines.length ? matchListItem(lines[probe]) : null
      if (next && next.ordered === first.ordered) {
        cursor = probe
        continue
      }
      break
    }

    const item = matchListItem(line)
    if (item && item.ordered === first.ordered) {
      items.push(parseInline(current.join('\n').trim()))
      current = [item.text]
      cursor += 1
      continue
    }
    // 换了列表类型、或者撞上标题/围栏/引用：当前列表到此为止，交给外层重新起块。
    if (item || startsBlock(line)) break

    // 惰性续行：没有缩进也算当前列表项的后续行。
    current.push(line.trim())
    cursor += 1
  }

  items.push(parseInline(current.join('\n').trim()))
  blocks.push({
    kind: 'list',
    ordered: first.ordered,
    start: first.ordered ? first.start : 1,
    items,
  })
  return cursor
}

function matchFence(line: string): FenceMatch | null {
  const match = FENCE_OPEN.exec(line)
  if (!match) return null
  const marker = match[1]
  const info = match[2].trim()
  // 反引号围栏的 info string 里不允许再出现反引号（CommonMark）。
  if (marker[0] === '`' && info.includes('`')) return null
  return {
    character: marker[0] as '`' | '~',
    length: marker.length,
    language: languageOf(info),
  }
}

function closesFence(line: string, fence: FenceMatch): boolean {
  const match = FENCE_CLOSE.exec(line)
  if (!match) return false
  return match[1][0] === fence.character && match[1].length >= fence.length
}

/** info string 的第一个词是语言；没有就是 null。 */
function languageOf(info: string): string | null {
  if (info === '') return null
  const token = info.split(/\s+/)[0]
  return token === '' ? null : token
}

function matchHeading(line: string): { level: number; text: string } | null {
  const match = HEADING.exec(line)
  if (!match) return null
  const rest = match[2]
  // `#tag` 不是标题：CommonMark 要求 `#` 后面是空白或行尾。
  if (rest !== '' && !/^[ \t]/.test(rest)) return null
  const text = rest
    .trim()
    // 去掉结尾的 `#` 序列（`## 标题 ##`）。
    .replace(/[ \t]+#+$/, '')
    .trim()
  return { level: match[1].length, text }
}

function matchListItem(line: string): ListItemMatch | null {
  const match = LIST_ITEM.exec(line)
  if (!match) return null
  const marker = match[2]
  const ordered = /\d/.test(marker)
  return {
    ordered,
    start: ordered ? Number.parseInt(marker, 10) : 1,
    text: match[4],
  }
}

/** 这一行能不能打断段落、重新起一个块。 */
function startsBlock(line: string): boolean {
  return (
    matchFence(line) !== null ||
    matchHeading(line) !== null ||
    BLOCKQUOTE.test(line) ||
    matchListItem(line) !== null
  )
}

function trimBlankEdges(lines: string[]): void {
  while (lines.length > 0 && BLANK_LINE.test(lines[0])) lines.shift()
  while (lines.length > 0 && BLANK_LINE.test(lines[lines.length - 1])) lines.pop()
}

/* -------------------------------------------------------------------------- */
/* 渲染层                                                                      */
/* -------------------------------------------------------------------------- */

/** 只放行 http / https / mailto：大小写不敏感，前后空白已经在解析层去掉。 */
const SAFE_PROTOCOL = /^(?:https?|mailto):/i

function safeHref(raw: string): string | null {
  const href = raw.trim()
  return SAFE_PROTOCOL.test(href) ? href : null
}

const HEADING_CLASS: Record<number, string> = {
  1: 'text-title font-semibold',
  2: 'text-headline font-semibold',
}

/**
 * Agent 回复的 Markdown 渲染。
 *
 * 只消费 `parseMarkdown` 产出的纯数据节点，全程是真实 React 元素，不用
 * `dangerouslySetInnerHTML`；回复里的 HTML（`<img onerror=...>`）会作为文本渲染。
 * 流式中间态（未闭合的强调、代码块、链接）由解析层兜底，这里对任何输入都必须
 * 能渲染，不抛异常、不丢内容。
 */
export function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text])

  return (
    <div className="space-y-2 text-body leading-relaxed break-words">
      {blocks.map((block, index) => renderBlock(block, index))}
    </div>
  )
}

function renderBlock(block: MarkdownBlock, key: number) {
  switch (block.kind) {
    case 'paragraph':
      // 解析层保留了段内换行，这里必须用 pre-wrap，否则流式输出的换行会消失。
      return (
        <p key={key} className="whitespace-pre-wrap break-words">
          {renderInline(block.children, `${key}`)}
        </p>
      )

    case 'heading': {
      const level = Math.min(Math.max(block.level, 1), 6)
      // 动态标签只是为了少写六个分支；className 在这里给出，保证标题有层级感。
      const Tag = `h${level}` as 'h1'
      return (
        <Tag key={key} className={`break-words ${HEADING_CLASS[level] ?? 'text-body font-semibold'}`}>
          {renderInline(block.children, `${key}`)}
        </Tag>
      )
    }

    case 'list': {
      const items = block.items.map((item, itemIndex) => (
        <li key={itemIndex} className="whitespace-pre-wrap break-words">
          {renderInline(item, `${key}-${itemIndex}`)}
        </li>
      ))
      return block.ordered ? (
        <ol key={key} start={block.start} className="list-decimal space-y-1 pl-5">
          {items}
        </ol>
      ) : (
        <ul key={key} className="list-disc space-y-1 pl-5">
          {items}
        </ul>
      )
    }

    case 'blockquote':
      return (
        <blockquote key={key} className="whitespace-pre-wrap break-words border-l-2 border-border pl-3 text-muted-foreground">
          {renderInline(block.children, `${key}`)}
        </blockquote>
      )

    case 'code':
      return (
        <div key={key} className="space-y-1">
          {block.language ? <div className="text-footnote text-muted-foreground">{block.language}</div> : null}
          {/* 语言名放在 <pre> 外面，<pre> 的 textContent 必须与代码原文逐字一致。 */}
          <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2">
            <code className="font-mono text-footnote whitespace-pre-wrap break-words">{block.text}</code>
          </pre>
        </div>
      )
  }
}

function renderInline(nodes: MarkdownInline[], prefix: string) {
  return nodes.map((node, index) => {
    const key = `${prefix}-${index}`
    switch (node.kind) {
      case 'text':
        return <Fragment key={key}>{node.text}</Fragment>

      case 'code':
        return (
          <code key={key} className="rounded-md bg-muted px-1 py-0.5 font-mono text-footnote break-words">
            {node.text}
          </code>
        )

      case 'strong':
        return (
          <strong key={key} className="font-semibold">
            {renderInline(node.children, key)}
          </strong>
        )

      case 'em':
        return <em key={key}>{renderInline(node.children, key)}</em>

      case 'del':
        return (
          <del key={key} className="line-through">
            {renderInline(node.children, key)}
          </del>
        )

      case 'link': {
        const href = safeHref(node.href)
        // 不安全或非白名单协议：不生成 <a>，链接文字当普通文本，仍然可见可选中。
        if (href === null) return <span key={key}>{renderInline(node.children, key)}</span>
        return (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-primary-text underline underline-offset-2 break-words"
          >
            {renderInline(node.children, key)}
          </a>
        )
      }

      default:
        return null
    }
  })
}
