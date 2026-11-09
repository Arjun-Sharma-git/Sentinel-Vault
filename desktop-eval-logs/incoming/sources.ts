// Knowledge sources the signed-in user can read, and which of them a question should search.

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type SourceInfo } from './api'

export const sourcesQuery = { queryKey: ['sources'], queryFn: api.sources, staleTime: 60_000 }

export function useSources() {
  return useQuery(sourcesQuery)
}

const KEY = 'vault.sources'

function load(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((s) => typeof s === 'string') : []
  } catch {
    return [] // storage unavailable or corrupt: search everything
  }
}

/** Selected source names; an empty list means "all sources". Remembered per browser. */
export function useSourceSelection(available: SourceInfo[] | undefined) {
  const [selected, setSelected] = useState<string[]>(load)
  const known = new Set(available?.map((s) => s.name) ?? [])
  // Ignore remembered names the user can no longer read (or that were removed).
  const effective = available ? selected.filter((s) => known.has(s)) : selected

  function save(next: string[]) {
    setSelected(next)
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next))
    } catch {
      // not persisted; the choice still applies for this visit
    }
  }

  return {
    selected: effective,
    all: effective.length === 0,
    toggle: (name: string) => save(effective.includes(name) ? effective.filter((s) => s !== name) : [...effective, name]),
    only: (name: string) => save([name]),
    clear: () => save([]),
  }
}

/** The source a library path belongs to: a named folder first, the root source otherwise. */
export function sourceFor(path: string, sources: SourceInfo[] | undefined): SourceInfo | undefined {
  if (!sources) return undefined
  const top = path.split('/')[0]
  return sources.find((s) => s.folder === top) ?? sources.find((s) => s.folder === '.')
}

/** A folder's path inside its source ("gitlab-handbook/finance/x" -> "finance/x"). */
export function pathInSource(path: string, source: SourceInfo | undefined): string {
  if (!source || source.folder === '.') return path
  return path === source.folder ? '' : path.slice(source.folder.length + 1)
}

export function formatCount(n: number) {
  return n.toLocaleString('en-US')
}
