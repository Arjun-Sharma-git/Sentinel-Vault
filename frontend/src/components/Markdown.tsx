// A small, safe markdown renderer for answers: headings, lists, bold/italic, code, with [S1] citation chips.
// No HTML is ever injected; everything becomes React elements.

import { Fragment, type ReactNode } from 'react'
import { MermaidBlock } from './MermaidBlock'
import { cn } from './ui'

type Block =
  | { kind: 'p'; text: string }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul'; items: string[]; start: number }
  | { kind: 'ol'; items: string[]; start: number }
  | { kind: 'code'; text: string; lang: string; closed: boolean }

const BULLET = /^\s*[-*•]\s+/
const NUMBER = /^\s*(\d+)[.)]\s+/

// Models sometimes put a whole numbered list on one line ("… 1. **A** … 2. **B** …"); split it back out.
function normalise(text: string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$))/)
    .map((part) => (part.startsWith('```') ? part : normaliseProse(part)))
    .join('')
}

function normaliseProse(text: string): string {
  return text
    .replace(/\r/g, '')
    .replace(/([.:!?)\]])\s+(?=\d+[.)]\s+\*\*)/g, '$1\n')
    .replace(/([.:!?)\]])\s+(?=[-•]\s+\*\*)/g, '$1\n')
}

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = []
  const lines = normalise(text).split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    if (line.trim().startsWith('```')) {
      const lang = line.trim().slice(3).trim().toLowerCase()
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) body.push(lines[i++])
      const closed = i < lines.length
      i++
      blocks.push({ kind: 'code', text: body.join('\n'), lang, closed })
      continue
    }
    const h = line.match(/^\s*(#{1,4})\s+(.*)$/)
    if (h) { blocks.push({ kind: 'h', level: h[1].length, text: h[2] }); i++; continue }
    const listKind = NUMBER.test(line) ? 'ol' : BULLET.test(line) ? 'ul' : null
    if (listKind) {
      const re = listKind === 'ol' ? NUMBER : BULLET
      const start = listKind === 'ol' ? Number(line.match(NUMBER)![1]) : 1
      const items: string[] = []
      while (i < lines.length) {
        const l = lines[i]
        if (re.test(l)) items.push(l.replace(re, ''))
        else if (l.trim() && /^\s{2,}/.test(l) && items.length) items[items.length - 1] += ' ' + l.trim()
        else if (!l.trim() && i + 1 < lines.length && re.test(lines[i + 1])) { /* blank between items */ }
        else break
        i++
      }
      blocks.push({ kind: listKind, items, start })
      continue
    }
    const para: string[] = [line]
    i++
    while (i < lines.length && lines[i].trim() && !NUMBER.test(lines[i]) && !BULLET.test(lines[i]) && !/^\s*#{1,4}\s/.test(lines[i]) && !lines[i].trim().startsWith('```'))
      para.push(lines[i++])
    blocks.push({ kind: 'p', text: para.join(' ') })
  }
  return blocks
}

const INLINE = /(\[S\d+\]|\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*)/g

function Inline({ text, cite }: { text: string; cite: (n: number, key: number) => ReactNode }) {
  return (
    <>
      {text.split(INLINE).map((part, i) => {
        if (!part) return null
        const c = part.match(/^\[S(\d+)\]$/)
        if (c) return cite(Number(c[1]), i)
        if (/^(\*\*|__).+(\*\*|__)$/.test(part)) return <strong key={i} className="font-semibold text-ink"><Inline text={part.slice(2, -2)} cite={cite} /></strong>
        if (/^`.+`$/.test(part)) return <code key={i} className="rounded bg-cream px-1 py-px font-mono text-[13px]">{part.slice(1, -1)}</code>
        if (/^\*.+\*$/.test(part)) return <em key={i}>{part.slice(1, -1)}</em>
        return <Fragment key={i}>{part}</Fragment>
      })}
    </>
  )
}

export function Markdown({ text, cite, className, done = true }: { text: string; cite: (n: number, key: number) => ReactNode; className?: string; done?: boolean }) {
  return (
    <div className={cn('space-y-3 text-[15px] leading-relaxed', className)}>
      {parseBlocks(text).map((b, i) => {
        if (b.kind === 'h')
          return <p key={i} className={cn('font-semibold text-ink', b.level <= 2 ? 'text-lg' : 'text-base')}><Inline text={b.text} cite={cite} /></p>
        if (b.kind === 'code' && b.lang === 'mermaid')
          return <MermaidBlock key={i} code={b.text} done={done && b.closed} />
        if (b.kind === 'code')
          return <pre key={i} className="overflow-x-auto rounded-2xl bg-cream p-3 font-mono text-[13px]">{b.text}</pre>
        if (b.kind === 'ul')
          return (
            <ul key={i} className="space-y-2">
              {b.items.map((it, j) => (
                <li key={j} className="flex gap-3">
                  <span aria-hidden className="mt-2.5 size-1.5 shrink-0 rounded-full bg-gold" />
                  <span><Inline text={it} cite={cite} /></span>
                </li>
              ))}
            </ul>
          )
        if (b.kind === 'ol')
          return (
            <ol key={i} className="space-y-2.5">
              {b.items.map((it, j) => (
                <li key={j} className="flex gap-3">
                  <span aria-hidden className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-cream font-mono text-[11px] font-medium text-gold-deep">{b.start + j}</span>
                  <span><Inline text={it} cite={cite} /></span>
                </li>
              ))}
            </ol>
          )
        return <p key={i}><Inline text={b.text} cite={cite} /></p>
      })}
    </div>
  )
}

// Plain-text version for the clipboard: markdown kept, [S1] → [1], sources listed at the end.
export function toClipboard(text: string, sources: { n: number; title: string; path: string }[]): string {
  const body = normalise(text).replace(/\[S(\d+)\]/g, '[$1]').trim()
  if (!sources.length) return body
  return body + '\n\nSources:\n' + sources.map((s) => `[${s.n}] ${s.title} (${s.path})`).join('\n')
}
