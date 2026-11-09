// Library: the folders you may view, their documents, uploads where you're an editor.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, Folder, LoaderCircle, Trash2, Upload, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { FolderTree } from '../components/FolderTree'
import { PageHeader } from '../components/Layout'
import { Badge, Button, Card, Empty, Eyebrow } from '../components/ui'
import { api, type DocumentRow, type MyFolder } from '../lib/api'
import { useSources } from '../lib/sources'

const ACCEPT = '.md,.markdown,.txt,.csv,.json,.pdf,.docx,.pptx,.xlsx,.html,.htm'
const STATUS_TONE = { indexed: 'sage', pending: 'gold', indexing: 'gold', failed: 'brick' } as const

function formatBytes(n: number) {
  return n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`
}

export function LibraryPage() {
  const folders = useQuery({ queryKey: ['folders'], queryFn: api.folders })
  const sources = useSources()
  const [selected, setSelected] = useState<string | null>(null)
  const [openDoc, setOpenDoc] = useState<string | null>(null)
  const folder = folders.data?.find((f) => f.id === selected) ?? folders.data?.[0]

  return (
    <div>
      <PageHeader
        eyebrow="Library"
        title="Folders you"
        accent="can read."
        subtitle="Folders are grouped by knowledge source. You see the ones an admin has granted to you or your groups; access to a folder includes its subfolders."
      />
      {folders.data?.length === 0 ? (
        <Card>
          <Empty icon={<Folder className="size-5" />} title="No folders yet.">
            An admin hasn't granted you access to any folder.
          </Empty>
        </Card>
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-[17rem_minmax(0,1fr)]">
          <Card className="p-2 lg:sticky lg:top-24 lg:max-h-[calc(100vh-8rem)] lg:overflow-y-auto">
            <FolderTree
              folders={folders.data ?? []}
              sources={sources.data}
              selected={folder?.id}
              onSelect={(id) => {
                setSelected(id)
                setOpenDoc(null)
              }}
            />
          </Card>
          {folder && <FolderView folder={folder} openDoc={openDoc} setOpenDoc={setOpenDoc} />}
        </div>
      )}
    </div>
  )
}

function FolderView({ folder, openDoc, setOpenDoc }: { folder: MyFolder; openDoc: string | null; setOpenDoc: (id: string | null) => void }) {
  const queryClient = useQueryClient()
  const documents = useQuery({
    queryKey: ['folder-docs', folder.id],
    queryFn: () => api.folderDocuments(folder.id),
    // keep polling while something is still being indexed
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === 'pending' || d.status === 'indexing') ? 2000 : false),
  })
  const input = useRef<HTMLInputElement>(null)
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['folder-docs', folder.id] })
    queryClient.invalidateQueries({ queryKey: ['folders'] })
  }
  const upload = useMutation({ mutationFn: (file: File) => api.upload(folder.id, file), onSuccess: refresh })
  const remove = useMutation({ mutationFn: api.deleteDocument, onSuccess: refresh })

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-5">
          <div>
            <Eyebrow>{folder.path}</Eyebrow>
            <h2 className="mt-1 font-serif text-3xl">{folder.name}</h2>
          </div>
          {folder.can_upload ? (
            <>
              <input
                ref={input}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) upload.mutate(file)
                  e.target.value = ''
                }}
              />
              <Button onClick={() => input.current?.click()} disabled={upload.isPending}>
                {upload.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Upload className="size-4" />} Upload
              </Button>
            </>
          ) : (
            <Badge>read only</Badge>
          )}
        </div>
        {(upload.error || remove.error) && (
          <p className="border-b border-line bg-brick-soft px-6 py-2 text-sm text-brick">{((upload.error || remove.error) as Error).message}</p>
        )}
        <ul className="divide-y divide-line">
          {documents.data?.length === 0 && <li className="px-6 py-10 text-center text-sm text-muted">No documents in this folder.</li>}
          {documents.data?.map((doc) => (
            <DocumentLine key={doc.id} doc={doc} canEdit={folder.can_upload} open={() => setOpenDoc(doc.id)} remove={() => remove.mutate(doc.id)} />
          ))}
        </ul>
      </Card>
      {openDoc && <DocumentPanel id={openDoc} close={() => setOpenDoc(null)} />}
    </div>
  )
}

function DocumentLine({ doc, canEdit, open, remove }: { doc: DocumentRow; canEdit: boolean; open: () => void; remove: () => void }) {
  return (
    <li className="flex items-center gap-3 px-6 py-3.5">
      <FileText className="size-4 shrink-0 text-gold" />
      <button onClick={open} disabled={doc.status !== 'indexed'} className="min-w-0 flex-1 text-left disabled:cursor-default">
        <span className="block truncate font-medium">{doc.title}</span>
        <span className="block truncate font-mono text-[11px] text-muted">
          {doc.rel_path} · {formatBytes(doc.size_bytes)} · {doc.chunk_count} chunks
        </span>
      </button>
      <Badge tone={STATUS_TONE[doc.status]}>{doc.status}</Badge>
      {canEdit && (
        <button
          onClick={() => window.confirm(`Delete ${doc.title}? This removes the file and its index.`) && remove()}
          aria-label={`Delete ${doc.title}`}
          className="rounded-full p-1.5 text-muted hover:bg-brick-soft hover:text-brick"
        >
          <Trash2 className="size-4" />
        </button>
      )}
    </li>
  )
}

function DocumentPanel({ id, close }: { id: string; close: () => void }) {
  const doc = useQuery({ queryKey: ['doc', id], queryFn: () => api.document(id) })
  return (
    <Card className="animate-fade-in p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Eyebrow>Indexed text</Eyebrow>
          <h3 className="mt-1 font-serif text-2xl">{doc.data?.title ?? '…'}</h3>
        </div>
        <button onClick={close} aria-label="Close" className="rounded-full border border-line p-1.5 text-muted hover:text-ink">
          <X className="size-4" />
        </button>
      </div>
      <div className="mt-4 space-y-4">
        {doc.data?.chunks.map((chunk) => (
          <section key={chunk.id}>
            {chunk.heading && <h4 className="text-xs font-medium uppercase tracking-[0.15em] text-gold-deep">{chunk.heading}</h4>}
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{chunk.text}</p>
          </section>
        ))}
      </div>
    </Card>
  )
}
