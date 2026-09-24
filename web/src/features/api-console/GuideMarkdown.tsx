import { Fragment, type ReactNode } from 'react'
import guideMarkdown from '../../../../docs/14-开放API接入指南.md?raw'

function inline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g)
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index} className="rounded bg-muted px-1 py-0.5">{part.slice(1, -1)}</code>
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) return <a key={index} href={link[2]} className="text-primary underline underline-offset-2">{link[1]}</a>
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
      blocks.push(<pre key={`code-${index}`} className="overflow-x-auto rounded-lg border bg-muted/40 p-3 text-xs leading-5"><code>{code.join('\n')}</code></pre>)
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      const Tag = `h${Math.min(level + 1, 5)}` as 'h2' | 'h3' | 'h4' | 'h5'
      blocks.push(<Tag key={`heading-${index}`} className="font-semibold">{inline(heading[2])}</Tag>)
      index += 1
      continue
    }
    if (/^\|.*\|$/.test(line) && /^\|[\s:|-]+\|$/.test(lines[index + 1] ?? '')) {
      const headers = line.split('|').slice(1, -1).map((cell) => cell.trim())
      index += 2
      const rows: string[][] = []
      while (index < lines.length && /^\|.*\|$/.test(lines[index])) rows.push(lines[index++].split('|').slice(1, -1).map((cell) => cell.trim()))
      blocks.push(<div key={`table-${index}`} className="overflow-x-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-muted"><tr>{headers.map((cell) => <th key={cell} className="whitespace-nowrap p-2 font-semibold">{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex} className="border-t">{row.map((cell, cellIndex) => <td key={cellIndex} className="min-w-24 p-2 align-top">{inline(cell)}</td>)}</tr>)}</tbody></table></div>)
      continue
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) items.push(lines[index++].replace(/^[-*]\s+/, ''))
      blocks.push(<ul key={`list-${index}`} className="list-disc space-y-1 pl-5">{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ul>)
      continue
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\d+\.\s+/.test(lines[index])) items.push(lines[index++].replace(/^\d+\.\s+/, ''))
      blocks.push(<ol key={`ordered-${index}`} className="list-decimal space-y-1 pl-5">{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ol>)
      continue
    }
    const paragraph = [line]
    index += 1
    while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|```|[-*]\s|\d+\.\s|\|.*\|$)/.test(lines[index])) paragraph.push(lines[index++])
    blocks.push(<p key={`paragraph-${index}`} className="text-sm leading-6 text-muted-foreground">{paragraph.map((part, partIndex) => <Fragment key={partIndex}>{partIndex > 0 ? ' ' : ''}{inline(part)}</Fragment>)}</p>)
  }
  return <article className="space-y-3">{blocks}</article>
}
