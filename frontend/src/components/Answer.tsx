// One question and its answer: streamed text with [S1] citation chips, then verified sources.

import { Ban, Check, Copy, FileText, SearchX, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { Markdown, toClipboard } from './Markdown'
import { sourceFor } from '../lib/sources'
import type { Citation, SourceInfo, Unsupported } from '../lib/types'
import type { Turn } from '../lib/useAsk'
import { Badge, Card, Eyebrow, cn } from './ui'

function citeChip(cited: Set<number> | null) {
  return (n: number, key: number) => {
    const verified = cited === null || cited.has(n)
    return (
      <sup
        key={key}
        title={verified ? `Source ${n}` : 'Not a valid source: removed by verification'}
        className={cn(
          'ml-0.5 rounded-full px-1.5 py-px font-mono text-[10px] font-medium',
          verified ? 'bg-gold-soft text-gold-deep' : 'bg-brick-soft text-brick line-through',
        )}
      >
        {n}
      </sup>
    )
  }
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs text-muted transition hover:border-gold hover:text-ink"
      aria-label="Copy answer"
    >
      {copied ? <Check className="size-3.5 text-gold-deep" /> : <Copy className="size-3.5" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

function Unverified({ items }: { items: Unsupported[] }) {
  const boxes = items.filter((i) => i.kind === 'node').length
  return (
    <div className="mt-4 rounded-2xl border border-brick/25 bg-brick-soft/60 px-4 py-3 text-xs text-brick">
      <p className="flex items-center gap-2 font-medium">
        <ShieldAlert className="size-4" />
        {items.length} statement{items.length === 1 ? '' : 's'} not supported by the cited sources
        {boxes > 0 && ` (${boxes} diagram box${boxes === 1 ? '' : 'es'}, dashed red)`}
      </p>
      <ul className="mt-2 space-y-1 pl-6">
        {items.map((item, i) => (
          <li key={i} className="list-disc">
            {item.kind === 'node' && <span className="mr-1 font-mono text-[10px] uppercase">box</span>}
            {item.text || '(empty label)'}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Sources({ citations, sources }: { citations: Citation[]; sources?: SourceInfo[] }) {
  return (
    <div className="mt-5 border-t border-line pt-4">
      <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted">Sources</p>
      <ul className="mt-2.5 space-y-2">
        {citations.map((c) => (
          <li key={c.chunk_id} className="text-sm">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="grid size-5 place-items-center rounded-full bg-gold-soft font-mono text-[10px] font-medium text-gold-deep">
                {c.n}
              </span>
              <FileText className="size-3.5 text-gold" />
              <span className="font-medium">{c.title}</span>
              {c.heading && <span className="text-muted">› {c.heading.split(' › ').at(-1)}</span>}
              {(sources?.length ?? 0) > 1 && <Badge tone="gold">{sourceFor(c.path, sources)?.title ?? c.folder}</Badge>}
              <Badge>{c.folder.split('/').at(-1)}</Badge>
            </div>
            <p className="ml-7 mt-1 line-clamp-2 text-xs leading-relaxed text-muted">{c.snippet}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function AnswerCard({ turn, sources }: { turn: Turn; sources?: SourceInfo[] }) {
  const searched = turn.sources?.length
    ? (sources ?? []).filter((s) => turn.sources?.includes(s.name)).map((s) => s.title)
    : []
  const final = turn.final
  const cited = final ? new Set(final.citations.map((c) => c.n)) : null

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-3xl rounded-br-lg bg-ink px-5 py-3 text-[15px] text-paper">
          {turn.question}
          {turn.searchedAs && (
            <span className="mt-1 block text-[11px] text-paper/70" title="Your follow-up, rewritten using the conversation">
              searched as: {turn.searchedAs}
            </span>
          )}
          {searched.length > 0 && <span className="mt-1 block text-[11px] text-paper/55">in {searched.join(', ')}</span>}
        </div>
      </div>

      {turn.error ? (
        <Card className="border-brick/25 bg-brick-soft p-5 text-sm text-brick">{turn.error}</Card>
      ) : final && !final.allowed ? (
        <Card className="animate-fade-in border-brick/25 p-6">
          <div className="flex items-start gap-4">
            <span className="grid size-10 shrink-0 place-items-center rounded-full bg-brick-soft text-brick">
              <Ban className="size-5" />
            </span>
            <div>
              <Eyebrow className="!text-brick">Blocked before search</Eyebrow>
              <p className="mt-1 text-sm text-muted">{turn.text}</p>
              <ul className="mt-2 space-y-1 text-sm">
                {(final.reasons ?? []).map((r) => (
                  <li key={r}>• {r}</li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      ) : final?.noAnswer ? (
        <Card className="animate-fade-in p-6">
          <div className="flex items-start gap-4">
            <span className="grid size-10 shrink-0 place-items-center rounded-full bg-cream text-gold-deep">
              <SearchX className="size-5" />
            </span>
            <div>
              <p className="font-medium">{turn.text}</p>
              <p className="mt-1 text-sm text-muted">
                {searched.length
                  ? `Only ${searched.join(' and ')} was searched, and only the folders you have access to. Try “All sources”, or ask an admin if the answer lives in a folder you can't read.`
                  : 'Only folders you have access to were searched. If the answer lives elsewhere, ask an admin for access.'}
              </p>
            </div>
          </div>
        </Card>
      ) : (
        <Card className="p-6">
          {turn.text ? (
            <Markdown text={turn.text} cite={citeChip(cited)} className="select-text" done={!!final} />
          ) : (
            <div className="space-y-3">
              <div className="h-3 w-3/4 animate-pulse rounded-full bg-line" />
              <div className="h-3 w-1/2 animate-pulse rounded-full bg-line" />
            </div>
          )}
          {final && !final.grounded && (
            <p className="mt-4 flex items-center gap-2 rounded-2xl bg-gold-soft px-3 py-2 text-xs text-gold-deep">
              <ShieldAlert className="size-4" /> This answer doesn't cite a source. Check the documents before relying on it.
            </p>
          )}
          {final && (final.unsupported?.length ?? 0) > 0 && <Unverified items={final.unsupported!} />}
          {final && final.citations.length > 0 && <Sources citations={final.citations} sources={sources} />}
          {final && turn.text && (
            <div className="mt-4 flex justify-end">
              <CopyButton text={toClipboard(turn.text, final.citations)} />
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
