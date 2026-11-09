// Access: grant folders to groups (matrix) and to individual people, and preview what anyone sees.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, FolderPlus, Trash2, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { PageHeader } from '../../components/Layout'
import { Badge, Button, Card, CardHeader, cn } from '../../components/ui'
import { api, type Folder, type GrantRow, type Relation } from '../../lib/api'

type Level = 'none' | Relation

const select = 'rounded-full border border-line bg-paper px-3 py-1.5 text-sm outline-none focus:border-gold'

function useAccessData() {
  const folders = useQuery({ queryKey: ['admin', 'folders'], queryFn: api.admin.folders })
  const groups = useQuery({ queryKey: ['admin', 'groups'], queryFn: api.admin.groups })
  const users = useQuery({ queryKey: ['admin', 'users'], queryFn: api.admin.users })
  const grants = useQuery({ queryKey: ['admin', 'grants'], queryFn: api.admin.grants })
  return { folders: folders.data ?? [], groups: groups.data ?? [], users: users.data ?? [], grants: grants.data ?? [] }
}

export function AccessPage() {
  const { folders, groups, users, grants } = useAccessData()
  return (
    <div>
      <PageHeader
        eyebrow="Admin · Access"
        title="Who can read"
        accent="which folders."
        subtitle="Changes apply on the very next question: the search filter is computed from these grants at query time, so nothing is re-indexed. Granting a folder includes its subfolders, and editors can also upload."
      />
      <div className="space-y-6">
        <GroupMatrix folders={folders} groups={groups} grants={grants} />
        <div className="grid items-start gap-6 xl:grid-cols-2">
          <UserGrants folders={folders} users={users} grants={grants} />
          <div className="space-y-6">
            <AccessPreview users={users} folders={folders} />
            <NewFolder />
          </div>
        </div>
      </div>
    </div>
  )
}

function useGrantMutations() {
  const queryClient = useQueryClient()
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['admin'] })
    queryClient.invalidateQueries({ queryKey: ['folders'] })
  }
  const grant = useMutation({ mutationFn: api.admin.grant, onSuccess: done })
  const revoke = useMutation({ mutationFn: api.admin.revoke, onSuccess: done })
  return { grant, revoke }
}

function GroupMatrix({ folders, groups, grants }: { folders: Folder[]; groups: { id: string; name: string }[]; grants: GrantRow[] }) {
  const { grant, revoke } = useGrantMutations()
  const [busy, setBusy] = useState<string | null>(null)

  const forCell = (folderId: string, groupId: string) =>
    grants.filter((g) => g.folder_id === folderId && g.subject_type === 'group' && g.subject_id === groupId)

  const level = (folderId: string, groupId: string): Level => {
    const relations = forCell(folderId, groupId)
    return relations.some((g) => g.relation === 'editor') ? 'editor' : relations.length ? 'viewer' : 'none'
  }

  async function change(folderId: string, groupId: string, next: Level) {
    setBusy(`${folderId}:${groupId}`)
    try {
      for (const g of forCell(folderId, groupId)) await revoke.mutateAsync(g.id)
      if (next !== 'none') await grant.mutateAsync({ folder_id: folderId, subject_type: 'group', subject_id: groupId, relation: next })
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader eyebrow="Groups × folders" title="Department access" subtitle="Pick what each group may do in each folder." />
      <div className="scrollbar-thin mt-5 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-y border-line">
              <th className="w-full px-6 py-3 text-left text-[10px] font-medium uppercase tracking-[0.18em] text-muted">Folder</th>
              {groups.map((g) => (
                <th key={g.id} className="min-w-[5.5rem] whitespace-nowrap px-3 py-3 text-center text-xs font-medium">
                  {g.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {folders.map((f) => (
              <tr key={f.id} className="border-b border-line/70">
                {/* max-w-0 + w-full: the folder column takes only the width the group columns leave,
                    and long handbook paths are cut with an ellipsis (full path on hover). */}
                <td
                  className="max-w-0 py-3 pr-6"
                  style={{ paddingLeft: `${1.5 + Math.min(f.path.split('/').length - 1, 4) * 1.1}rem` }}
                  title={f.path}
                >
                  <div className="truncate">
                    <span className="font-medium">{f.name}</span>
                    <span className="ml-2 font-mono text-[11px] text-muted">{f.path}</span>
                  </div>
                </td>
                {groups.map((g) => {
                  const current = level(f.id, g.id)
                  return (
                    <td key={g.id} className="px-2 py-2 text-center">
                      <select
                        aria-label={`${g.name} on ${f.path}`}
                        value={current}
                        disabled={busy === `${f.id}:${g.id}`}
                        onChange={(e) => change(f.id, g.id, e.target.value as Level)}
                        className={cn(
                          'rounded-full border px-2.5 py-1 text-xs outline-none',
                          current === 'editor' && 'border-ink bg-ink text-paper',
                          current === 'viewer' && 'border-sage/30 bg-sage-soft text-sage',
                          current === 'none' && 'border-line bg-paper text-muted',
                        )}
                      >
                        <option value="none">—</option>
                        <option value="viewer">view</option>
                        <option value="editor">edit</option>
                      </select>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function UserGrants({ folders, users, grants }: { folders: Folder[]; users: { id: string; name: string }[]; grants: GrantRow[] }) {
  const { grant, revoke } = useGrantMutations()
  const [userId, setUserId] = useState('')
  const [folderId, setFolderId] = useState('')
  const [relation, setRelation] = useState<Relation>('viewer')
  const userGrants = grants.filter((g) => g.subject_type === 'user')
  const name = (id: string) => users.find((u) => u.id === id)?.name ?? id
  const path = (id: string) => folders.find((f) => f.id === id)?.path ?? id

  return (
    <Card className="pb-6">
      <CardHeader eyebrow="Exceptions" title="Per-person grants" subtitle="One-off access without adding someone to a group." />
      <form
        className="mt-5 flex flex-wrap gap-2 px-6 sm:px-7"
        onSubmit={(e) => {
          e.preventDefault()
          if (userId && folderId) grant.mutate({ folder_id: folderId, subject_type: 'user', subject_id: userId, relation })
        }}
      >
        <select className={select} value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="Person">
          <option value="">Person…</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>
        <select className={select} value={folderId} onChange={(e) => setFolderId(e.target.value)} aria-label="Folder">
          <option value="">Folder…</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.path}
            </option>
          ))}
        </select>
        <select className={select} value={relation} onChange={(e) => setRelation(e.target.value as Relation)} aria-label="Access">
          <option value="viewer">view</option>
          <option value="editor">edit</option>
        </select>
        <Button type="submit" disabled={!userId || !folderId || grant.isPending}>
          <UserPlus className="size-4" /> Grant
        </Button>
      </form>
      {grant.error && <p className="mt-2 px-7 text-sm text-brick">{(grant.error as Error).message}</p>}
      <ul className="mt-4 divide-y divide-line px-6 sm:px-7">
        {userGrants.length === 0 && <li className="py-4 text-sm text-muted">No per-person grants.</li>}
        {userGrants.map((g) => (
          <li key={g.id} className="flex items-center gap-3 py-2.5 text-sm">
            <span className="font-medium">{name(g.subject_id)}</span>
            <Badge tone={g.relation === 'editor' ? 'ink' : 'sage'}>{g.relation === 'editor' ? 'edit' : 'view'}</Badge>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">{path(g.folder_id)}</span>
            <button onClick={() => revoke.mutate(g.id)} aria-label="Revoke" className="rounded-full p-1.5 text-muted hover:bg-brick-soft hover:text-brick">
              <Trash2 className="size-4" />
            </button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function AccessPreview({ users, folders }: { users: { id: string; name: string }[]; folders: Folder[] }) {
  const [userId, setUserId] = useState('')
  const access = useQuery({ queryKey: ['admin', 'access', userId], queryFn: () => api.admin.access(userId), enabled: !!userId })
  const path = (id: string) => folders.find((f) => f.id === id)?.path ?? id
  return (
    <Card className="pb-6">
      <CardHeader eyebrow="Preview" title="What does this person see?" subtitle="Answered live by the authorization service." />
      <div className="px-6 sm:px-7">
        <select className={`${select} mt-5`} value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="Person to preview">
          <option value="">Choose a person…</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>
        {access.data && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {access.data.viewer.length === 0 && <span className="text-sm text-muted">No folders.</span>}
            {access.data.viewer.map((id) => (
              <Badge key={id} tone={access.data!.editor.includes(id) ? 'ink' : 'sage'}>
                <Eye className="size-3" /> {path(id)}
                {access.data!.editor.includes(id) && ' · edit'}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

function NewFolder() {
  const queryClient = useQueryClient()
  const [path, setPath] = useState('')
  const create = useMutation({
    mutationFn: () => api.admin.createFolder(path.trim().toLowerCase(), ''),
    onSuccess: () => {
      setPath('')
      queryClient.invalidateQueries({ queryKey: ['admin'] })
    },
  })
  return (
    <Card className="pb-6">
      <CardHeader eyebrow="Folders" title="New folder" subtitle="Use / for a subfolder, e.g. hr/policies. It inherits its parent's grants." />
      <form
        className="mt-5 flex gap-2 px-6 sm:px-7"
        onSubmit={(e) => {
          e.preventDefault()
          if (path.trim()) create.mutate()
        }}
      >
        <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="legal/contracts" aria-label="Folder path" className={`${select} flex-1`} />
        <Button type="submit" disabled={!path.trim() || create.isPending}>
          <FolderPlus className="size-4" /> Create
        </Button>
      </form>
      {create.error && <p className="mt-2 px-7 text-sm text-brick">{(create.error as Error).message}</p>}
    </Card>
  )
}
