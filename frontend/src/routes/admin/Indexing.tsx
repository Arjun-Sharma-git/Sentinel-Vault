// Indexing: every document's status, plus re-scan and re-index.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { RefreshCw, RotateCw } from 'lucide-react'
import { useMemo } from 'react'
import { DataTable } from '../../components/DataTable'
import { PageHeader } from '../../components/Layout'
import { Badge, Button, Card } from '../../components/ui'
import { api, type DocumentRow } from '../../lib/api'

const col = createColumnHelper<DocumentRow>()
const TONE = { indexed: 'sage', pending: 'gold', indexing: 'gold', failed: 'brick' } as const

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-3xl border border-line bg-paper px-5 py-4">
      <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-muted">{label}</p>
      <p className="mt-1 font-serif text-4xl leading-none">{value}</p>
    </div>
  )
}

export function IndexingPage() {
  const queryClient = useQueryClient()
  const data = useQuery({
    queryKey: ['admin', 'documents'],
    queryFn: api.admin.documents,
    refetchInterval: (q) => ((q.state.data?.jobs_pending ?? 0) > 0 ? 2000 : 15000),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin'] })
  const scan = useMutation({ mutationFn: api.admin.scan, onSuccess: refresh })
  const reindex = useMutation({ mutationFn: api.admin.reindex, onSuccess: refresh })

  const columns = useMemo(
    () => [
      col.accessor('rel_path', {
        header: 'Document',
        cell: (c) => (
          <div>
            <div className="font-medium">{c.row.original.title}</div>
            <div className="font-mono text-[11px] text-muted">{c.getValue()}</div>
            {c.row.original.error && <div className="mt-1 max-w-xl text-xs text-brick">{c.row.original.error}</div>}
          </div>
        ),
      }),
      col.accessor('status', { header: 'Status', cell: (c) => <Badge tone={TONE[c.getValue()]}>{c.getValue()}</Badge> }),
      col.accessor('chunk_count', { header: 'Chunks', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
      col.accessor('source', { header: 'Source', cell: (c) => <span className="text-xs text-muted">{c.getValue()}</span> }),
      col.accessor('indexed_at', {
        header: 'Indexed',
        cell: (c) => <span className="whitespace-nowrap text-xs text-muted">{c.getValue() ? new Date(c.getValue()!).toLocaleString() : '—'}</span>,
      }),
      col.display({
        id: 'actions',
        cell: (c) => (
          <button onClick={() => reindex.mutate(c.row.original.id)} title="Re-index" className="rounded-full p-1.5 text-muted hover:bg-cream hover:text-ink">
            <RotateCw className="size-4" />
          </button>
        ),
      }),
    ],
    [reindex],
  )

  const documents = data.data?.documents ?? []
  return (
    <div>
      <PageHeader
        eyebrow="Admin · Indexing"
        title="The library,"
        accent="indexed."
        subtitle="Files in data/library are scanned on startup and on demand. Changed files are re-indexed, moved files keep their vectors, and deleted files leave the index."
        right={
          <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
            <RefreshCw className={scan.isPending ? 'size-4 animate-spin' : 'size-4'} /> Scan library
          </Button>
        }
      />
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Documents" value={documents.length} />
        <Stat label="Indexed" value={documents.filter((d) => d.status === 'indexed').length} />
        <Stat label="Queued jobs" value={data.data?.jobs_pending ?? 0} />
        <Stat label="Vectors" value={data.data?.vectors ?? 0} />
      </div>
      {scan.data && (
        <p className="mb-4 text-sm text-muted">
          Scan: {String(scan.data.queued)} queued · {String(scan.data.moved)} moved · {String(scan.data.deleted)} deleted · {String(scan.data.unchanged)} unchanged
        </p>
      )}
      <Card className="overflow-hidden">
        <DataTable data={documents} columns={columns} getRowId={(d) => d.id} searchPlaceholder="Search documents…" />
      </Card>
    </div>
  )
}
