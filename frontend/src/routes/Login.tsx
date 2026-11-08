// Sign in. Demo accounts are listed for convenience; they exist only after `seed-demo`.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { LoaderCircle } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Backdrop, Logo } from '../components/Layout'
import { Badge, Display, Eyebrow } from '../components/ui'
import { api } from '../lib/api'

const DEMO = [
  { email: 'alice@acme.example.com', who: 'Alice', role: 'Admin · leadership' },
  { email: 'bob@acme.example.com', who: 'Bob', role: 'Finance' },
  { email: 'erin@acme.example.com', who: 'Erin', role: 'People (HR)' },
  { email: 'carol@acme.example.com', who: 'Carol', role: 'Engineering' },
  { email: 'dave@acme.example.com', who: 'Dave', role: 'Intern' },
]
const DEMO_PASSWORD = 'vault-demo-2026'

const field =
  'w-full rounded-2xl border border-line bg-paper px-4 py-3 outline-none transition-colors placeholder:text-muted/60 focus:border-gold'

export function LoginPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const login = useMutation({
    mutationFn: (creds: { email: string; password: string }) => api.login(creds.email, creds.password),
    onSuccess: (me) => {
      queryClient.clear() // nothing from a previous user may linger
      queryClient.setQueryData(['me'], me)
      navigate({ to: '/' })
    },
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    login.mutate({ email, password })
  }

  return (
    <div className="min-h-screen px-4 pb-16 pt-10">
      <Backdrop />
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <Logo />
        <Badge>Runs locally · your documents never leave this machine</Badge>
      </div>

      <div className="mx-auto mt-16 grid max-w-5xl gap-10 lg:grid-cols-[1fr_24rem] lg:items-center">
        <div>
          <Eyebrow>Sentinel Vault</Eyebrow>
          <Display title="Every answer," accent="from documents you may read." className="mt-4 text-[clamp(2.6rem,6vw,4.6rem)]" />
          <p className="mt-6 max-w-xl text-balance leading-relaxed text-muted">
            Department folders are indexed into a vector database. Admins grant folders to groups and people, and every
            search is filtered by those grants before a local model writes a cited answer.
          </p>
          <div className="mt-8 grid max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
            {DEMO.map((d) => (
              <button
                key={d.email}
                onClick={() => login.mutate({ email: d.email, password: DEMO_PASSWORD })}
                disabled={login.isPending}
                className="group flex items-center justify-between rounded-2xl border border-line bg-paper/80 px-4 py-3 text-left transition hover:-translate-y-0.5 hover:border-ink disabled:opacity-60"
              >
                <span>
                  <span className="block font-medium">{d.who}</span>
                  <span className="text-xs text-muted">{d.role}</span>
                </span>
                {login.isPending && login.variables?.email === d.email ? (
                  <LoaderCircle className="size-4 animate-spin text-gold" />
                ) : (
                  <span aria-hidden className="text-gold transition-transform group-hover:translate-x-1">→</span>
                )}
              </button>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted">Demo accounts, password {DEMO_PASSWORD}.</p>
        </div>

        <form onSubmit={submit} className="rounded-[28px] bg-ink p-7 text-paper">
          <Eyebrow dark>Sign in</Eyebrow>
          <div className="mt-5 grid gap-3">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="username"
              aria-label="Email"
              className={`${field} border-paper/15 bg-paper/5 text-paper placeholder:text-paper/35`}
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoComplete="current-password"
              aria-label="Password"
              className={`${field} border-paper/15 bg-paper/5 text-paper placeholder:text-paper/35`}
            />
            <button
              type="submit"
              disabled={!email || !password || login.isPending}
              className="mt-1 rounded-full bg-paper px-6 py-3 text-sm font-medium text-ink disabled:opacity-40"
            >
              Sign in
            </button>
            <p aria-live="polite" className="min-h-5 text-sm text-[#f0a48e]">
              {login.isError && (login.error as Error).message}
            </p>
          </div>
        </form>
      </div>
    </div>
  )
}
