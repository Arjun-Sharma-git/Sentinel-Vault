// Small display formatters shared across pages.

/** 'security_secrets' -> 'security secrets' (every underscore, not just the first). */
export const humanize = (name: string) => name.replaceAll('_', ' ')

/** 840 -> '840 ms', 2310 -> '2.3 s' */
export const formatMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)

/** 0.437 -> '44%' */
export const percent = (p: number) => `${(p * 100).toFixed(0)}%`

/** 'Priya Raman' -> 'Priya' */
export const firstName = (name: string) => name.split(' ')[0]

/** 'Priya Raman' -> 'PR' */
export const initials = (name: string) =>
  name
    .split(' ')
    .map((part) => part[0])
    .join('')
