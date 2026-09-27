import { Fragment, type ReactNode } from 'react'
import guideMarkdown from '../../../../docs/14-开放API接入指南.md?raw'

const headingClass = {
  h2: 'text-title font-semibold tracking-tight',
  h3: 'pt-6 text-headline font-semibold',
  h4: 'pt-3 text-[0.9375rem] font-semibold',
  h5: 'pt-2 text-sm font-semibold',
} as const

function inline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g)
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={index} className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.8125rem] [overflow-wrap:anywhere]">{part.slice(1, -1)}</code>
    }
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index} className="font-semibold">{part.slice(2, -2)}</strong>
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) {
      const localMarkdown = /^(?:\.\.?\/)+[^?#]+\.md(?:[?#].*)?$/i.test(link[2])
      return <a key={index} href={localMarkdown ? '/docs' : link[2]} className="font-medium text-primary-text underline decoration-primary-text/40 underline-offset-2 hover:decoration-primary-text">
        {link[1]}{localMarkdown && <span className="ml-1 text-footnote font-normal text-muted-foreground">（/docs）</span>}
      </a>
    }
    return <Fragment key={index}>{part}</Fragment>
  })
}

export function GuideMarkdown() {
  const blocks: ReactNode[] = []
  const lines = guideMarkdown.split(/\r?\n/)
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) { index += 1; continue }
    if (line.startsWith('```')) {
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].startsWith('```')) code.push(lines[index++])
      index += 1
      blocks.push(<pre key={`code-${index}`} className="overflow-x-auto overscroll-x-contain rounded-xl border bg-card p-3.5 font-mono text-[0.8125rem] leading-6 shadow-card"><code>{code.join('\n')}</code></pre>)
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      const Tag = `h${Math.min(level + 1, 5)}` as 'h2' | 'h3' | 'h4' | 'h5'
      blocks.push(<Tag key={`heading-${index}`} className={headingClass[Tag]}>{inline(heading[2])}</Tag>)
      index += 1
      continue
    }
    if (/^\|.*\|$/.test(line) && /^\|[\s:|-]+\|$/.test(lines[index + 1] ?? '')) {
      const headers = line.split('|').slice(1, -1).map((cell) => cell.trim())
      index += 2
      const rows: string[][] = []
      while (index < lines.length && /^\|.*\|$/.test(lines[index])) rows.push(lines[index++].split('|').slice(1, -1).map((cell) => cell.trim()))
      blocks.push(
        <div key={`table-${index}`} className="overflow-x-auto overscroll-x-contain rounded-xl border bg-card shadow-card">
          <table className="w-full text-left text-sm [&_code]:[overflow-wrap:normal]">
            <thead className="bg-surface-2 text-footnote text-muted-foreground">
              <tr>{headers.map((cell) => <th key={cell} scope="col" className="whitespace-nowrap px-3 py-2 font-medium">{inline(cell)}</th>)}</tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} className="min-w-28 px-3 py-2.5 align-top leading-6">{inline(cell)}</td>)}</tr>)}
            </tbody>
          </table>
        </div>,
      )
      continue
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) items.push(lines[index++].replace(/^[-*]\s+/, ''))
      blocks.push(<ul key={`list-${index}`} className="list-disc space-y-1.5 pl-5 text-body leading-7 marker:text-muted-foreground">{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ul>)
      continue
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\d+\.\s+/.test(lines[index])) items.push(lines[index++].replace(/^\d+\.\s+/, ''))
      blocks.push(<ol key={`ordered-${index}`} className="list-decimal space-y-1.5 pl-5 text-body leading-7 marker:text-muted-foreground">{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ol>)
      continue
    }
    const paragraph = [line]
    index += 1
    while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|```|[-*]\s|\d+\.\s|\|.*\|$)/.test(lines[index])) paragraph.push(lines[index++])
    blocks.push(<p key={`paragraph-${index}`} className="text-body leading-7">{paragraph.map((part, partIndex) => <Fragment key={partIndex}>{partIndex > 0 ? ' ' : ''}{inline(part)}</Fragment>)}</p>)
  }
  return <article className="min-w-0 space-y-4">{blocks}</article>
}
