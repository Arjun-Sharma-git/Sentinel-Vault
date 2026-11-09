// State for one streamed question: trace steps, the answer text as it arrives, citations.

import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { ask } from './api'
import type { AskEvent, Citation, StepEvent, StepId, Unsupported } from './types'

export type Turn = {
  question: string
  sources?: string[] // the knowledge sources searched; undefined means all
  requestId?: string
  searchedAs?: string // the follow-up rewritten to stand alone, when it was
  steps: Partial<Record<StepId, StepEvent>>
  text: string // streamed answer text; replaced by the verified text at the end
  final?: {
    allowed: boolean
    citations: Citation[]
    reasons?: string[]
    noAnswer?: boolean
    grounded?: boolean
    unsupported?: Unsupported[] // claims the grounding check found no support for
    faithfulness?: number | null
  }
  done?: { outcome: string; latencyMs: number }
  error?: string
}

function apply(turn: Turn, event: AskEvent): Turn {
  switch (event.type) {
    case 'start':
      return { ...turn, requestId: event.request_id }
    case 'step':
      return { ...turn, steps: { ...turn.steps, [event.id]: event } }
    case 'token':
      return { ...turn, text: turn.text + event.text }
    case 'rewrite':
      return { ...turn, searchedAs: event.question }
    case 'reset':
      return { ...turn, text: '' }
    case 'answer':
      return {
        ...turn,
        text: event.text,
        final: {
          allowed: event.allowed,
          citations: event.citations,
          reasons: event.reasons,
          noAnswer: event.no_answer,
          grounded: event.grounded,
          unsupported: event.unsupported,
          faithfulness: event.faithfulness,
        },
      }
    case 'done':
      return { ...turn, done: { outcome: event.outcome, latencyMs: event.latency_ms } }
  }
}

/** The last answered turns, for resolving a follow-up ("his email?"). Diagrams are left out. */
function conversation(turns: Turn[]) {
  return turns
    .filter((t) => t.final?.allowed && !t.final.noAnswer && t.text)
    .slice(-3)
    .map((t) => ({
      question: t.searchedAs ?? t.question,
      answer: t.text.replace(/```[\s\S]*?```/g, '').slice(0, 1500),
    }))
}

export function useAsk() {
  const queryClient = useQueryClient()
  const [turns, setTurns] = useState<Turn[]>([])
  const abort = useRef<AbortController | null>(null)
  const current = turns.at(-1)
  const running = !!current && !current.done && !current.error

  async function submit(question: string, sources?: string[]) {
    if (!question || running) return
    const controller = new AbortController()
    abort.current = controller
    const history = conversation(turns)
    setTurns((all) => [...all, { question, sources, steps: {}, text: '' }])
    const update = (fn: (t: Turn) => Turn) => setTurns((all) => [...all.slice(0, -1), fn(all[all.length - 1])])
    try {
      await ask(question, (event) => update((t) => apply(t, event)), controller.signal, sources, history)
      queryClient.invalidateQueries({ queryKey: ['audit'] })
    } catch (err) {
      if ((err as Error).name === 'AbortError') update((t) => ({ ...t, error: 'Stopped.' }))
      else update((t) => ({ ...t, error: (err as Error).message }))
    }
  }

  return { turns, current, running, submit, stop: () => abort.current?.abort(), clear: () => setTurns([]) }
}
