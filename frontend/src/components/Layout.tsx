// The signed-in shell: floating pill navigation, page header, and shared hooks.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import { FolderLock, MessagesSquare, ScrollText, ShieldCheck, Users, Workflow } from 'lucide-react'
import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { firstName, initials } from '../lib/format'
import { meQuery } from '../lib/queries'
import { Display, Eyebrow, cn } from './ui'

export function useMe() {
  return useQuery(meQuery)
}

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: 30_000 })
}

/** Slow-drifting warm light and a fine paper grain behind every page (pure CSS). */
export function Backdrop() {
  return (
    <div aria-hidden className="backdrop">
      <div className="backdrop-glow backdrop-glow-a" />
      <div className="backdrop-glow backdrop-glow-b" />
      <div className="backdrop-glow backdrop-glow-c" />
      <div className="backdrop-grain" />
    </div>
  )
}

export function Logo() {
  return (
    <span className="flex items-center gap-2 font-medium">
      <span aria-hidden className="grid size-7 place-items-center rounded-full bg-ink font-serif text-[15px] text-paper">
        V
      </span>
      <span className="hidden sm:inline">Vault</span>
    </span>
  )
}

type NavItem = { to: '/' | '/library' | '/admin/people' | '/admin/access' | '/admin/indexing' | '/admin/audit'; label: string; icon: ReactNode }

const USER_NAV: NavItem[] = [
  { to: '/', label: 'Ask', icon: <MessagesSquare className="size-4" /> },
  { to: '/library', label: 'Library', icon: <FolderLock className="size-4" /> },
]
const ADMIN_NAV: NavItem[] = [
  { to: '/admin/access', label: 'Access', icon: <ShieldCheck className="size-4" /> },
  { to: '/admin/people', label: 'People', icon: <Users className="size-4" /> },
  { to: '/admin/indexing', label: 'Indexing', icon: <Workflow className="size-4" /> },
  { to: '/admin/audit', label: 'Audit', icon: <ScrollText className="size-4" /> },
]

function NavLink({ item }: { item: NavItem }) {
  return (
    <Link
      to={item.to}
      title={item.label}
      className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-muted transition-colors hover:text-ink"
      activeProps={{ className: '!bg-cream !text-ink' }}
      activeOptions={{ exact: item.to === '/' }}
    >
      <span className="md:hidden">{item.icon}</span>
      <span className="hidden md:inline">{item.label}</span>
    </Link>
  )
}

export function AppLayout() {
  const { data: me } = useMe()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  async function signOut() {
    await api.logout().catch(() => undefined)
    queryClient.clear()
    navigate({ to: '/login' })
  }

  return (
    <div className="min-h-screen">
      <Backdrop />
      <header className="fixed inset-x-0 top-4 z-40 flex justify-center px-3">
        <nav
          aria-label="Primary"
          className="flex items-center gap-0.5 rounded-full border border-line/80 bg-paper/80 p-1.5 text-sm shadow-[0_8px_30px_rgb(27_24_20/0.08)] backdrop-blur-md"
        >
          <Link to="/" className="rounded-full px-2 py-1 sm:px-3">
            <Logo />
          </Link>
          {USER_NAV.map((item) => (
            <NavLink key={item.to} item={item} />
          ))}
          {me?.is_admin && (
            <>
              <span aria-hidden className="mx-1 h-5 w-px bg-line" />
              {ADMIN_NAV.map((item) => (
                <NavLink key={item.to} item={item} />
              ))}
            </>
          )}
          {me && (
            <span
              className="ml-1 hidden items-center gap-2 border-l border-line py-1 pl-3 pr-2 text-xs text-muted lg:flex"
              title={`${me.email} · groups: ${me.groups.join(', ') || 'none'}`}
            >
              <span className="grid size-6 place-items-center rounded-full bg-gold-soft text-[10px] font-semibold text-gold-deep">
                {initials(me.name)}
              </span>
              <span className="text-ink">{firstName(me.name)}</span>
              {me.is_admin && <span className="text-gold-deep">· admin</span>}
            </span>
          )}
          <button
            onClick={signOut}
            className="ml-1 whitespace-nowrap rounded-full bg-ink px-4 py-1.5 font-medium text-paper transition-transform hover:scale-[1.03]"
          >
            Sign out
          </button>
        </nav>
      </header>
      <main className="mx-auto max-w-7xl px-4 pb-20 pt-28 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}

/** Which engines answer: shown in every page header. */
export function EngineBadge() {
  const { data } = useHealth()
  if (!data) return null
  const degraded = data.degraded ?? []
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2 rounded-full border bg-paper/80 py-1.5 pl-3 pr-3.5 text-xs backdrop-blur',
        degraded.length ? 'border-brick/40' : 'border-line',
      )}
      title={[
        `Authorization: ${data.authz} · gate: ${data.gate} · embeddings: ${data.embeddings}`,
        `reranker: ${data.reranker ?? '?'} · claim judge: ${data.claim_judge ?? '?'} · ${data.vectors} vectors`,
        ...degraded.map((d) => `Not running: ${d}`),
      ].join('\n')}
    >
      <span
        className={cn(
          'size-2 rounded-full',
          degraded.length ? 'bg-brick' : data.llm === 'extractive' ? 'bg-gold' : 'bg-sage',
        )}
      />
      <span className="font-medium">{data.llm}</span>
      <span className="font-mono text-muted">
        {data.authz} · {data.gate}
      </span>
      {degraded.length > 0 && <span className="font-medium text-brick">degraded</span>}
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  accent,
  subtitle,
  right,
}: {
  eyebrow: string
  title: ReactNode
  accent?: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
      <div className="max-w-3xl">
        <Eyebrow>{eyebrow}</Eyebrow>
        <Display title={title} accent={accent} className="mt-3 text-[clamp(2.2rem,4.5vw,3.4rem)]" />
        {subtitle && <p className="mt-3 max-w-2xl text-balance leading-relaxed text-muted">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">
        {right}
        <EngineBadge />
      </div>
    </header>
  )
}
