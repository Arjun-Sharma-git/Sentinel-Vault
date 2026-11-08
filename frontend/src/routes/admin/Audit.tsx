// Audit: every sign-in, question, document read and permission change.

import { useQuery } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { useMemo, useState } from 'react'
import { DataTable } from '../../components/DataTable'
import { PageHeader } from '../../components/Layout'
import { Badge, Card, cn } from '../../components/ui'
import { api, type AuditRow } from '../../lib/api'

const col = createColumnHelper<AuditRow>()
const FILTERS = ['all', 'query', 'document.read', 'grant.create', 'grant.revoke', 'auth.login'] as const

function describe(row: AuditRow): string {
  const d = row.detail as Record<string, unknown>
  if (row.action === 'query') return String(d.question ?? '')
  if (Array.isArray(d.reasons) && d.reasons.length) return String(d.reasons[0])
  return row.target
}

export function AuditPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all')
  const audit = useQuery({
    queryKey: ['audit', filter],
    queryFn: () => api.admin.audit(filter === 'all' ? undefined : filter),
    refetchInterval: 10_000,
  })

  const columns = useMemo(
    () => [
      col.accessor('ts', {
        header: 'Time',
        cell: (c) => <span className="whitespace-nowrap font-mono text-xs text-muted">{new Date(c.getValue()).toLocaleString()}</span>,
      }),
      col.accessor('actor', { header: 'Who', cell: (c) => <span className="font-medium">{c.getValue() ?? '—'}</span> }),
      col.accessor('action', { header: 'Action', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
      col.accessor('outcome', {
        header: 'Outcome',
        cell: (c) => <Badge tone={c.getValue() === 'ok' ? 'sage' : 'brick'}>{c.getValue()}</Badge>,
      }),
      col.accessor((r) => describe(r), { id: 'detail', header: 'Detail', cell: (c) => <div className="max-w-xl truncate">{c.getValue()}</div> }),
      col.accessor((r) => ((r.detail as { documents?: string[] }).documents ?? []).length, {
        id: 'docs',
        header: 'Docs used',
        cell: (c) => <span className="font-mono text-xs">{c.getValue() || ''}</span>,
      }),
    ],
    [],
  )

  return (
    <div>
      <PageHeader
        eyebrow="Admin · Audit"
        title="Every decision,"
        accent="on the record."
        subtitle="Questions, document reads, sign-ins and permission changes. Prompts are kept here, behind admin access, and never written to process logs."
      />
      <Card className="overflow-hidden">
        <DataTable
          data={audit.data ?? []}
          columns={columns}
          getRowId={(r) => String(r.id)}
          initialSorting={[{ id: 'ts', desc: true }]}
          searchPlaceholder="Search the log…"
          toolbar={
            <div className="flex flex-wrap rounded-full border border-line bg-cream p-0.5 text-xs">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={cn('rounded-full px-3 py-1 transition-colors', filter === f ? 'bg-ink text-paper' : 'text-muted hover:text-ink')}
                >
                  {f}
                </button>
              ))}
            </div>
          }
        />
      </Card>
    </div>
  )
}
