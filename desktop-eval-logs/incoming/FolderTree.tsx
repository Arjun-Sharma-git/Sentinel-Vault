// The library's folder list: grouped by knowledge source, collapsible, searchable.
//
// Folders are placed by their path inside the source, not by parent_id: a parent the user
// can't read (a source's closed root folder) would otherwise leave its sections attached to
// whatever happened to be listed before them.

import { BookOpen, ChevronRight, Folder, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { MyFolder, SourceInfo } from '../lib/api'
import { formatCount, pathInSource } from '../lib/sources'
import { cn } from './ui'

type Node = { folder: MyFolder; rel: string; depth: number; hasChildren: boolean }

function nodesFor(folders: MyFolder[], source: SourceInfo | undefined): Node[] {
  const rels = folders.map((f) => ({ folder: f, rel: pathInSource(f.path, source) })).filter((n) => n.rel !== '')
  const paths = new Set(rels.map((n) => n.rel))
  return rels
    .sort((a, b) => a.rel.localeCompare(b.rel))
    .map(({ folder, rel }) => {
      // depth counts only visible ancestors, so a hidden parent doesn't leave a gap
      const parts = rel.split('/')
      const visibleAncestors = parts.slice(0, -1).filter((_, i) => paths.has(parts.slice(0, i + 1).join('/'))).length
      const hasChildren = rels.some((o) => o.rel.startsWith(rel + '/'))
      return { folder, rel, depth: visibleAncestors, hasChildren }
    })
}

function ancestors(rel: string): string[] {
  const parts = rel.split('/')
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))
}

export function FolderTree({
  folders,
  sources,
  selected,
  onSelect,
}: {
  folders: MyFolder[]
  sources: SourceInfo[] | undefined
  selected: string | undefined
  onSelect: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [closedSources, setClosedSources] = useState<Set<string>>(() => new Set())

  const groups = useMemo(() => {
    const bySource = new Map<string, MyFolder[]>()
    for (const f of folders) {
      const key = f.source ?? ''
      bySource.set(key, [...(bySource.get(key) ?? []), f])
    }
    return [...bySource.entries()].map(([name, list]) => {
      const source = sources?.find((s) => s.name === name)
      return { name, source, nodes: nodesFor(list, source), documents: list.reduce((n, f) => n + f.documents, 0) }
    })
  }, [folders, sources])

  const q = query.trim().toLowerCase()
  const toggle = (key: string) =>
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  return (
    <div>
      <label className="flex items-center gap-2 rounded-2xl bg-cream/70 px-3 py-2 text-sm">
        <Search className="size-4 text-muted" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a folder"
          aria-label="Find a folder"
          className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted/60"
        />
      </label>
      {groups.map(({ name, source, nodes, documents }) => {
        const open = q !== '' || !closedSources.has(name)
        const shown = nodes.filter((n) =>
          q ? n.rel.toLowerCase().includes(q) || n.folder.name.toLowerCase().includes(q) : ancestors(n.rel).every((a) => !nodes.some((m) => m.rel === a) || expanded.has(`${name}:${a}`)),
        )
        if (q && shown.length === 0) return null
        return (
          <div key={name} className="mt-3">
            <button
              onClick={() =>
                setClosedSources((s) => {
                  const next = new Set(s)
                  if (next.has(name)) next.delete(name)
                  else next.add(name)
                  return next
                })
              }
              aria-expanded={open}
              className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
            >
              <ChevronRight className={cn('size-3.5 text-muted transition-transform', open && 'rotate-90')} />
              <BookOpen className="size-3.5 text-gold" />
              <span className="min-w-0 flex-1 truncate text-[11px] font-medium uppercase tracking-[0.15em] text-gold-deep">
                {source?.title ?? 'Other folders'}
              </span>
              <span className="font-mono text-[10px] text-muted">{formatCount(documents)}</span>
            </button>
            {open &&
              shown.map((n) => {
                const key = `${name}:${n.rel}`
                const isOpen = expanded.has(key)
                return (
                  <div
                    key={n.folder.id}
                    className={cn(
                      'flex items-center rounded-2xl text-sm transition-colors',
                      n.folder.id === selected ? 'bg-ink text-paper' : 'hover:bg-cream',
                    )}
                    style={{ paddingLeft: `${0.25 + (q ? 0 : n.depth) * 0.85}rem` }}
                  >
                    {n.hasChildren && !q ? (
                      <button onClick={() => toggle(key)} aria-label={isOpen ? `Collapse ${n.folder.name}` : `Expand ${n.folder.name}`} className="p-1.5">
                        <ChevronRight className={cn('size-3.5 opacity-60 transition-transform', isOpen && 'rotate-90')} />
                      </button>
                    ) : (
                      <span className="w-6" />
                    )}
                    <button
                      onClick={() => {
                        onSelect(n.folder.id)
                        if (n.hasChildren && !isOpen) toggle(key)
                      }}
                      className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-3 text-left"
                      title={q ? n.rel : n.folder.path}
                    >
                      <Folder className="size-4 shrink-0 opacity-70" />
                      <span className="min-w-0 flex-1 truncate">{q ? n.rel : n.folder.name}</span>
                      <span className="font-mono text-[11px] opacity-60">{n.folder.documents}</span>
                    </button>
                  </div>
                )
              })}
          </div>
        )
      })}
    </div>
  )
}
