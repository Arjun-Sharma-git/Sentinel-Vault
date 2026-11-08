// Design-system primitives: tones, badges, typography, surfaces, controls and data bars.

import clsx from 'clsx'
import { Check, LoaderCircle, Minus, TriangleAlert, X } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { StepStatus } from '../lib/api'

/** Join class names, skipping falsy values. */
export const cn = clsx

// ---- tones ------------------------------------------------------------------------------
export type Tone = 'neutral' | 'sage' | 'brick' | 'gold' | 'slate' | 'ink'

const TONES: Record<Tone, string> = {
  neutral: 'bg-cream text-muted border-line',
  sage: 'bg-sage-soft text-sage border-sage/20',
  brick: 'bg-brick-soft text-brick border-brick/20',
  gold: 'bg-gold-soft text-gold-deep border-gold/25',
  slate: 'bg-slate-soft text-slate border-slate/20',
  ink: 'bg-ink text-paper border-ink',
}

const TONES_ON_DARK: Record<Tone, string> = {
  neutral: 'bg-paper/10 text-paper/70 border-paper/15',
  sage: 'bg-[#9dbb84]/15 text-[#b7d19f] border-[#9dbb84]/25',
  brick: 'bg-[#e08a72]/15 text-[#f0a48e] border-[#e08a72]/25',
  gold: 'bg-gold/20 text-[#e2be86] border-gold/30',
  slate: 'bg-[#8fb0c8]/15 text-[#a9c4d8] border-[#8fb0c8]/25',
  ink: 'bg-paper text-ink border-paper',
}

/** A small pill label. `dark` switches to the palette used on ink cards. */
export function Badge({
  tone = 'neutral',
  dark = false,
  className,
  children,
}: {
  tone?: Tone
  dark?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-medium',
        (dark ? TONES_ON_DARK : TONES)[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

// Classification levels, aligned with store.LEVELS on the backend.
export const LEVEL_TONE: Tone[] = ['sage', 'slate', 'gold', 'brick']
export const LEVEL_LABEL = ['Public', 'Internal', 'Confidential', 'Restricted']

export function ClassificationBadge({ level, dark }: { level: number; dark?: boolean }) {
  return (
    <Badge tone={LEVEL_TONE[level] ?? 'neutral'} dark={dark}>
      {LEVEL_LABEL[level] ?? level}
    </Badge>
  )
}

// ---- typography ----------------------------------------------------------------------------
export function Eyebrow({ children, dark, className }: { children: ReactNode; dark?: boolean; className?: string }) {
  return (
    <p className={cn('text-[11px] font-medium uppercase tracking-[0.2em]', dark ? 'text-gold' : 'text-gold-deep', className)}>
      {children}
    </p>
  )
}

/** Serif heading with an italic gold closing phrase, as on the portfolio. */
export function Display({
  title,
  accent,
  as: Tag = 'h1',
  className,
}: {
  title: ReactNode
  accent?: ReactNode
  as?: 'h1' | 'h2' | 'h3'
  className?: string
}) {
  return (
    <Tag className={cn('font-serif leading-[1] tracking-tight', className)}>
      {title} {accent && <em className="text-gold">{accent}</em>}
    </Tag>
  )
}

// ---- surfaces --------------------------------------------------------------------------------
/** The rounded surface every panel sits on. `featured` is the dark ink variant. */
export function Card({
  className,
  featured = false,
  children,
}: {
  className?: string
  featured?: boolean
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        'rounded-[28px] border',
        featured ? 'border-ink bg-ink text-paper shadow-[0_30px_60px_rgb(27_24_20/0.18)]' : 'border-line bg-paper',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function CardHeader({
  eyebrow,
  title,
  subtitle,
  right,
  dark,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
  dark?: boolean
}) {
  return (
    <div className={cn('flex items-start justify-between gap-4 px-6 pt-6 sm:px-7 sm:pt-7')}>
      <div className="min-w-0">
        {eyebrow && <Eyebrow dark={dark}>{eyebrow}</Eyebrow>}
        <h2 className={cn('font-serif text-[1.85rem] leading-tight', eyebrow && 'mt-1.5')}>{title}</h2>
        {subtitle && <p className={cn('mt-1 text-sm', dark ? 'text-paper/60' : 'text-muted')}>{subtitle}</p>}
      </div>
      {right}
    </div>
  )
}

// ---- controls ----------------------------------------------------------------------------------
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'light'
  arrow?: boolean
}

/** Pill button. `arrow` adds the round arrow affordance used for primary actions. */
export function Button({ variant = 'primary', arrow = false, className, children, ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={cn(
        'group inline-flex items-center justify-center gap-2 rounded-full text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-45',
        arrow ? 'py-1.5 pl-5 pr-1.5' : 'px-5 py-2',
        variant === 'primary' && 'bg-ink text-paper hover:bg-ink/90',
        variant === 'light' && 'bg-paper text-ink hover:bg-white',
        variant === 'secondary' && 'border border-line bg-paper/70 text-ink backdrop-blur hover:border-ink',
        variant === 'ghost' && 'text-muted hover:text-ink',
        className,
      )}
    >
      {children}
      {arrow && (
        <span
          aria-hidden
          className={cn(
            'grid size-7 place-items-center rounded-full transition-transform group-hover:translate-x-0.5 group-disabled:translate-x-0',
            variant === 'light' ? 'bg-ink/10' : 'bg-paper/15',
          )}
        >
          →
        </span>
      )}
    </button>
  )
}

/** A compact toggle or suggestion pill. `danger` marks attack examples. */
export function Chip({
  children,
  active,
  danger,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; danger?: boolean }) {
  return (
    <button
      {...props}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs transition disabled:opacity-45',
        active
          ? 'border-ink bg-ink text-paper'
          : danger
            ? 'border-brick/25 bg-brick-soft/60 text-brick hover:border-brick/60'
            : 'border-line bg-cream text-ink hover:border-ink',
        className,
      )}
    >
      {children}
    </button>
  )
}

// ---- data -----------------------------------------------------------------------------------------
const FILL: Record<Tone, string> = {
  neutral: 'bg-muted/40',
  sage: 'bg-sage',
  brick: 'bg-brick',
  gold: 'bg-gold',
  slate: 'bg-slate',
  ink: 'bg-ink',
}
const FILL_DARK: Record<Tone, string> = {
  neutral: 'bg-paper/30',
  sage: 'bg-[#9dbb84]',
  brick: 'bg-[#e08a72]',
  gold: 'bg-[#e2be86]',
  slate: 'bg-[#8fb0c8]',
  ink: 'bg-paper',
}

/** A labelled probability bar (0..1) with the percentage on the right. */
export function ProbBar({
  label,
  value,
  tone = 'gold',
  highlight,
  dark,
  suffix,
}: {
  label: ReactNode
  value: number
  tone?: Tone
  highlight?: boolean
  dark?: boolean
  suffix?: ReactNode
}) {
  return (
    <div className="grid grid-cols-[minmax(0,9.5rem)_1fr_2.75rem] items-center gap-3 text-xs">
      <div
        className={cn(
          'truncate',
          dark ? (highlight ? 'text-paper' : 'text-paper/55') : highlight ? 'font-medium text-ink' : 'text-muted',
        )}
      >
        {label}
      </div>
      <div className={cn('h-1.5 overflow-hidden rounded-full', dark ? 'bg-paper/10' : 'bg-line/70')}>
        <div
          className={cn('h-full rounded-full transition-[width] duration-700', (dark ? FILL_DARK : FILL)[tone])}
          style={{ width: `${Math.max(1.5, Math.min(100, value * 100))}%` }}
        />
      </div>
      <div className={cn('text-right font-mono tabular-nums', dark ? 'text-paper/55' : 'text-muted')}>
        {suffix ?? `${(value * 100).toFixed(0)}%`}
      </div>
    </div>
  )
}

/** The round status marker of a pipeline step. */
export function StatusIcon({ status, dark, className }: { status: StepStatus; dark?: boolean; className?: string }) {
  const base = cn('grid size-6 shrink-0 place-items-center rounded-full border', className)
  const icon = 'size-3.5'
  switch (status) {
    case 'pass':
      return (
        <span className={cn(base, dark ? 'border-[#9dbb84]/40 bg-[#9dbb84]/15 text-[#b7d19f]' : 'border-sage/30 bg-sage-soft text-sage')}>
          <Check className={icon} strokeWidth={2.5} />
        </span>
      )
    case 'fail':
      return (
        <span className={cn(base, dark ? 'border-[#e08a72]/50 bg-[#e08a72]/20 text-[#f0a48e]' : 'border-brick/30 bg-brick-soft text-brick')}>
          <X className={icon} strokeWidth={2.5} />
        </span>
      )
    case 'warn':
      return (
        <span className={cn(base, dark ? 'border-gold/50 bg-gold/20 text-[#e2be86]' : 'border-gold/30 bg-gold-soft text-gold-deep')}>
          <TriangleAlert className="size-3" strokeWidth={2.5} />
        </span>
      )
    case 'running':
      return (
        <span className={cn(base, 'step-running', dark ? 'border-gold bg-gold/25 text-[#e2be86]' : 'border-gold bg-gold-soft text-gold-deep')}>
          <LoaderCircle className={cn(icon, 'animate-spin')} />
        </span>
      )
    case 'skip':
      return (
        <span className={cn(base, dark ? 'border-paper/10 text-paper/30' : 'border-line text-muted/50')}>
          <Minus className={icon} />
        </span>
      )
    default:
      return <span className={cn(base, 'border-dashed', dark ? 'border-paper/20' : 'border-line')} />
  }
}

/** Empty-state placeholder for a card. */
export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-4 grid size-12 place-items-center rounded-full border border-line bg-cream text-gold">{icon}</div>
      <p className="font-serif text-2xl">{title}</p>
      {children && <div className="mt-2 max-w-sm text-sm leading-relaxed text-muted">{children}</div>}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-md border border-line bg-cream px-1.5 py-0.5 font-mono text-[10px] text-muted">{children}</kbd>
  )
}
