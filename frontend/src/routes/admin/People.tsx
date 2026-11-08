// People: users, their groups, and the groups themselves.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { PageHeader, useMe } from '../../components/Layout'
import { Badge, Button, Card, CardHeader, cn } from '../../components/ui'
import { api, type GroupRow, type UserRow } from '../../lib/api'

const input = 'rounded-full border border-line bg-paper px-3.5 py-2 text-sm outline-none focus:border-gold'

export function PeoplePage() {
  const users = useQuery({ queryKey: ['admin', 'users'], queryFn: api.admin.users })
  const groups = useQuery({ queryKey: ['admin', 'groups'], queryFn: api.admin.groups })
  return (
    <div>
      <PageHeader
        eyebrow="Admin · People"
        title="People and"
        accent="their groups."
        subtitle="Group membership is the usual way to give access: grant a folder to a group on the Access page, then add people here. Deactivating someone signs them out everywhere at once."
      />
      <div className="space-y-6">
        <UsersTable users={users.data ?? []} groups={groups.data ?? []} />
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <NewUser groups={groups.data ?? []} />
          <NewGroup />
        </div>
      </div>
    </div>
  )
}

function UsersTable({ users, groups }: { users: UserRow[]; groups: GroupRow[] }) {
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof api.admin.updateUser>[1] }) => api.admin.updateUser(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin'] }),
  })

  const toggleGroup = (user: UserRow, groupId: string) => {
    const next = user.group_ids.includes(groupId) ? user.group_ids.filter((g) => g !== groupId) : [...user.group_ids, groupId]
    update.mutate({ id: user.id, body: { group_ids: next } })
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader eyebrow="Directory" title="Everyone" subtitle="Click a group to add or remove someone." />
      {update.error && <p className="mx-7 mt-3 text-sm text-brick">{(update.error as Error).message}</p>}
      <div className="scrollbar-thin mt-5 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-y border-line text-left text-[10px] font-medium uppercase tracking-[0.18em] text-muted">
              <th className="px-6 py-3">Person</th>
              <th className="px-3 py-3">Groups</th>
              <th className="px-3 py-3">Role</th>
              <th className="px-6 py-3 text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={cn('border-b border-line/70', !u.is_active && 'opacity-50')}>
                <td className="px-6 py-3">
                  <div className="font-medium">{u.name}</div>
                  <div className="font-mono text-[11px] text-muted">{u.email}</div>
                </td>
                <td className="px-3 py-3">
                  <div className="flex flex-wrap gap-1">
                    {groups.map((g) => {
                      const member = u.group_ids.includes(g.id)
                      return (
                        <button
                          key={g.id}
                          onClick={() => toggleGroup(u, g.id)}
                          disabled={update.isPending}
                          className={cn(
                            'rounded-full border px-2.5 py-0.5 text-[11px] transition-colors',
                            member ? 'border-ink bg-ink text-paper' : 'border-line text-muted hover:border-ink',
                          )}
                        >
                          {g.name}
                        </button>
                      )
                    })}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <button
                    onClick={() => update.mutate({ id: u.id, body: { is_admin: !u.is_admin } })}
                    disabled={u.id === me?.id}
                    title={u.id === me?.id ? "You can't change your own role" : 'Toggle admin'}
                  >
                    <Badge tone={u.is_admin ? 'gold' : 'neutral'}>{u.is_admin ? 'admin' : 'member'}</Badge>
                  </button>
                </td>
                <td className="px-6 py-3 text-right">
                  <button
                    onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}
                    disabled={u.id === me?.id}
                    className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline disabled:no-underline"
                  >
                    {u.is_active ? 'active · deactivate' : 'inactive · activate'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function NewUser({ groups }: { groups: GroupRow[] }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: '', email: '', password: '', is_admin: false, group_ids: [] as string[] })
  const create = useMutation({
    mutationFn: () => api.admin.createUser(form),
    onSuccess: () => {
      setForm({ name: '', email: '', password: '', is_admin: false, group_ids: [] })
      queryClient.invalidateQueries({ queryKey: ['admin'] })
    },
  })
  const set = (key: 'name' | 'email' | 'password') => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value })

  return (
    <Card className="pb-6">
      <CardHeader eyebrow="Invite" title="New person" subtitle="Passwords need at least 10 characters." />
      <form
        className="mt-5 grid gap-2 px-6 sm:px-7"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <input className={input} placeholder="Full name" value={form.name} onChange={set('name')} aria-label="Full name" />
        <input className={input} placeholder="email@company.com" value={form.email} onChange={set('email')} aria-label="Email" />
        <input className={input} type="password" placeholder="Initial password" value={form.password} onChange={set('password')} aria-label="Password" />
        <div className="flex flex-wrap gap-1 pt-1">
          {groups.map((g) => {
            const on = form.group_ids.includes(g.id)
            return (
              <button
                type="button"
                key={g.id}
                onClick={() => setForm({ ...form, group_ids: on ? form.group_ids.filter((x) => x !== g.id) : [...form.group_ids, g.id] })}
                className={cn('rounded-full border px-2.5 py-0.5 text-[11px]', on ? 'border-ink bg-ink text-paper' : 'border-line text-muted')}
              >
                {g.name}
              </button>
            )
          })}
        </div>
        <label className="flex items-center gap-2 pt-1 text-sm text-muted">
          <input type="checkbox" checked={form.is_admin} onChange={(e) => setForm({ ...form, is_admin: e.target.checked })} />
          Administrator (manages access; reading still needs grants)
        </label>
        <Button type="submit" className="mt-1 w-fit" disabled={create.isPending || !form.name || !form.email || form.password.length < 10}>
          <UserPlus className="size-4" /> Create
        </Button>
        {create.error && <p className="text-sm text-brick">{(create.error as Error).message}</p>}
      </form>
    </Card>
  )
}

function NewGroup() {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const create = useMutation({
    mutationFn: () => api.admin.createGroup(name.trim(), description.trim()),
    onSuccess: () => {
      setName('')
      setDescription('')
      queryClient.invalidateQueries({ queryKey: ['admin'] })
    },
  })
  return (
    <Card className="pb-6">
      <CardHeader eyebrow="Groups" title="New group" subtitle="Usually one per team or department." />
      <form
        className="mt-5 grid gap-2 px-6 sm:px-7"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <input className={input} placeholder="legal" value={name} onChange={(e) => setName(e.target.value)} aria-label="Group name" />
        <input className={input} placeholder="Description" value={description} onChange={(e) => setDescription(e.target.value)} aria-label="Description" />
        <Button type="submit" className="mt-1 w-fit" disabled={!name.trim() || create.isPending}>
          <Plus className="size-4" /> Create group
        </Button>
        {create.error && <p className="text-sm text-brick">{(create.error as Error).message}</p>}
      </form>
    </Card>
  )
}
