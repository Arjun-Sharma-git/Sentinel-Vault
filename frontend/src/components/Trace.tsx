// The live decision trace for one question.

import { ChevronDown, FileText, Lock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { formatMs } from '../lib/format'
import type { StepEvent, StepId, StepStatus } from '../lib/types'
import { Badge, ProbBar, StatusIcon, cn } from './ui'

const STEPS: { id: StepId; short: string; title: string; hint: string }[] = [
  { id: 'gate', short: 'Gate', title: 'Prompt gate (Laya)', hint: 'Injection and bulk-export check' },
  { id: 'access', short: 'Access', title: 'Resolve folder access', hint: 'Your grants, fresh from the authz service' },
  { id: 'retrieve', short: 'Search', title: 'Search permitted folders', hint: 'Vector + full-text, filtered inside the query' },
  { id: 'generate', short: 'Answer', title: 'Write answer', hint: 'Local model, sources only' },
  { id: 'verify', short: 'Verify', title: 'Verify citations', hint: 'Only sources that were sent may be cited' },
  { id: 'audit', short: 'Audit', title: 'Audit log', hint: 'Who asked what, and which documents were used' },
]

const CHIP: Record<StepStatus, string> = {
  pending: 'border-paper/15 text-paper/40',
  running: 'step-running border-gold bg-gold/20 text-[#e2be86]',
  pass: 'border-paper bg-paper text-ink',
  warn: 'border-gold bg-gold text-ink',
  fail: 'border-[#e08a72] bg-[#e08a72] text-ink',
  skip: 'border-paper/10 text-paper/25 line-through',
}

export function TraceChips({ steps }: { steps: Partial<Record<StepId, StepEvent>> }) {
  return (
    <ol aria-label="Pipeline progress" className="flex flex-wrap items-center gap-1.5 text-[11px]">
      {STEPS.map((meta, i) => (
        <li key={meta.id} className="flex items-center gap-1.5">
          <span className={cn('rounded-full border px-2.5 py-1 transition-colors', CHIP[steps[meta.id]?.status ?? 'pending'])}>
            {meta.short}
          </span>
          {i < STEPS.length - 1 && <span aria-hidden className="text-paper/30">→</span>}
        </li>
      ))}
    </ol>
  )
}

export function Trace({ steps }: { steps: Partial<Record<StepId, StepEvent>> }) {
  return (
    <ol className="space-y-1">
      {STEPS.map((meta, i) => (
        <Row key={meta.id} index={i} meta={meta} step={steps[meta.id]} last={i === STEPS.length - 1} />
      ))}
    </ol>
  )
}

function Row({ meta, step, index, last }: { meta: (typeof STEPS)[number]; step?: StepEvent; index: number; last: boolean }) {
  const status: StepStatus = step?.status ?? 'pending'
  const hasDetail = !!step?.data && status !== 'running'
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (step && step.status !== 'running' && (step.status === 'fail' || step.id === 'retrieve')) setOpen(true)
  }, [step])

  return (
    <li className="relative pl-10">
      {!last && <span aria-hidden className="absolute bottom-[-6px] left-[11px] top-9 w-px bg-paper/10" />}
      <span className="absolute left-0 top-3">
        <StatusIcon status={status} dark />
      </span>
      <div className={cn('rounded-2xl', status === 'fail' && 'bg-[#e08a72]/[0.08]', status === 'running' && 'bg-gold/[0.08]')}>
        <button
          type="button"
          disabled={!hasDetail}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex w-full items-start gap-3 px-3 py-2.5 text-left disabled:cursor-default"
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2.5">
              <span className="font-serif text-base italic text-gold">{String(index + 1).padStart(2, '0')}</span>
              <span className={cn('text-sm font-medium', status === 'pending' || status === 'skip' ? 'text-paper/45' : 'text-paper')}>
                {meta.title}
              </span>
            </div>
            <div className={cn('mt-0.5 truncate pl-[1.85rem] text-xs', status === 'fail' ? 'text-[#f0a48e]' : 'text-paper/50')}>
              {step?.summary ?? meta.hint}
            </div>
          </div>
          {step?.ms != null && status !== 'running' && <span className="mt-0.5 font-mono text-[10px] text-paper/40">{formatMs(step.ms)}</span>}
          {hasDetail && <ChevronDown className={cn('mt-0.5 size-4 text-paper/40 transition-transform', open && 'rotate-180')} />}
        </button>
        {open && hasDetail && step?.data && (
          <div className="animate-fade-in px-3 pb-4 pl-[2.6rem] pt-1 text-xs">
            <StepDetail step={step} />
          </div>
        )}
      </div>
    </li>
  )
}

function StepDetail({ step }: { step: StepEvent }) {
  const d = step.data!
  switch (step.id) {
    case 'gate':
      return (
        <div className="space-y-1.5">
          <ProbBar dark label="prompt injection" value={d.injection} tone={d.injection >= d.thresholds.injection ? 'brick' : 'sage'} />
          <ProbBar dark label="bulk export" value={d.exfiltration} tone={d.exfiltration >= d.thresholds.exfiltration ? 'brick' : 'sage'} />
          <p className="pt-1 text-[11px] text-paper/40">engine: {d.engine}</p>
        </div>
      )
    case 'access':
      return (
        <p className="flex items-start gap-1.5 font-mono text-[10px] leading-relaxed text-paper/45">
          <Lock className="mt-0.5 size-3 shrink-0" /> {d.backend}: {d.folders.length} folder id(s) go into the search filter
        </p>
      )
    case 'retrieve':
      return (
        <div className="space-y-2">
          {d.passages.length === 0 && <p className="text-paper/50">Nothing relevant in the folders you can access.</p>}
          {d.passages.map((p: { n: number; title: string; heading: string; folder: string; via: string[]; score: number }) => (
            <div key={p.n} className="flex items-center gap-2">
              <FileText className="size-3.5 shrink-0 text-[#e2be86]" />
              <span className="min-w-0 flex-1 truncate text-paper/85" title={`${p.title} › ${p.heading}`}>
                <span className="text-paper/45">S{p.n}</span> {p.title}
                {p.heading && <span className="text-paper/45"> › {p.heading.split(' › ').at(-1)}</span>}
              </span>
              <Badge dark>{p.folder}</Badge>
              <Badge dark tone={p.via.length > 1 ? 'gold' : 'neutral'}>{p.via.join(' + ')}</Badge>
            </div>
          ))}
          {d.dropped_by_recheck > 0 && (
            <p className="text-[#e2be86]">{d.dropped_by_recheck} passage(s) removed by the final permission check</p>
          )}
        </div>
      )
    case 'generate':
      return <p className="text-paper/60">{d.error ? `Model unavailable, quoted sources instead: ${d.error}` : `model: ${d.provider}`}</p>
    case 'verify':
      return (
        <p className="text-paper/60">
          {d.model_reply !== undefined
            ? d.model_reply
              ? `The model's reply cited no source, so it isn't shown as an answer: “${d.model_reply}”`
              : 'The model found no answer in the passages.'
            : 'Every claim cites a source that was sent.'}
          {d.dropped?.length > 0 && ` Removed invalid citations: ${d.dropped.map((n: number) => `S${n}`).join(', ')}.`}
        </p>
      )
    default:
      return <pre className="overflow-x-auto font-mono text-[11px] text-paper/60">{JSON.stringify(d, null, 2)}</pre>
  }
}

