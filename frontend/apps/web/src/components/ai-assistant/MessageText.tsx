import { Fragment, type ReactNode } from 'react'

// React text nodes only: provider output is never interpreted as HTML.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i}>{part.slice(1, -1)}</code>
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part)
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" className="underline text-primary-strong">{link[1]}</a>
    return <Fragment key={i}>{part}</Fragment>
  })
}

export function MessageText({ text }: { text: string }) {
  const lines = text.split('\n')
  const content: ReactNode[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.startsWith('|') && lines[i + 1]?.match(/^\|[\s:|\-]+\|$/)) {
      const cells = (row: string) => row.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim())
      const head = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].startsWith('|')) rows.push(cells(lines[i++]))
      i--
      content.push(<div key={i} className="my-2 max-w-full overflow-x-auto"><table className="text-sm"><thead><tr>{head.map((cell, c) => <th className="border-b p-2 text-left" key={c}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td className="border-b p-2" key={c}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>)
    } else if (/^#{1,6}\s/.test(line)) {
      content.push(<p key={i} className="mt-3 font-semibold">{inline(line.replace(/^#{1,6}\s/, ''))}</p>)
    } else {
      content.push(<div key={i} className="min-h-[.6em] whitespace-pre-wrap">{inline(line.replace(/^[-*]\s/, '• '))}</div>)
    }
  }
  return <div className="min-w-0 [overflow-wrap:anywhere]">{content}</div>
}
