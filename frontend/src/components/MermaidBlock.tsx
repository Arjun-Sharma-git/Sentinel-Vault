// Renders a ```mermaid block from an answer as a diagram, with copy and SVG/PNG download.
// Mermaid is loaded only when a diagram appears, and runs with securityLevel "strict".

import { Check, Code2, Copy, Download, Image as ImageIcon } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { cn } from './ui'

let loader: Promise<typeof import('mermaid').default> | null = null
function loadMermaid() {
  loader ??= import('mermaid').then((m) => {
    m.default.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral', fontFamily: 'inherit' })
    return m.default
  })
  return loader
}

function download(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function svgToPng(svg: string): Promise<Blob> {
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement
  const vb = doc.getAttribute('viewBox')?.split(/\s+/).map(Number)
  const w = Math.ceil(vb?.[2] || 800)
  const h = Math.ceil(vb?.[3] || 600)
  doc.setAttribute('width', String(w))
  doc.setAttribute('height', String(h))
  const src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(doc))
  const img = new Image()
  await new Promise((ok, fail) => { img.onload = ok; img.onerror = fail; img.src = src })
  const scale = 2
  const canvas = Object.assign(document.createElement('canvas'), { width: w * scale, height: h * scale })
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.scale(scale, scale)
  ctx.drawImage(img, 0, 0, w, h)
  return new Promise((ok, fail) => canvas.toBlob((b) => (b ? ok(b) : fail(new Error('PNG export failed'))), 'image/png'))
}

const btn = 'inline-flex items-center gap-1.5 rounded-full border border-line bg-paper px-2.5 py-1 text-xs text-muted transition hover:border-gold hover:text-ink disabled:opacity-40'

export function MermaidBlock({ code, done }: { code: string; done: boolean }) {
  const id = 'm' + useId().replace(/[^a-zA-Z0-9]/g, '')
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCode, setShowCode] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!done) return
    let cancelled = false
    loadMermaid()
      .then((m) => m.render(id, code))
      .then(({ svg }) => { if (!cancelled) { setSvg(svg); setError(null) } })
      .catch((e: unknown) => {
        document.getElementById('d' + id)?.remove() // mermaid leaves an error node behind
        if (!cancelled) { setSvg(null); setError(e instanceof Error ? e.message.split('\n')[0] : 'Could not draw this diagram') }
      })
    return () => { cancelled = true }
  }, [code, done, id])

  const copy = async () => {
    try { await navigator.clipboard.writeText(code) } catch { /* clipboard blocked */ }
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }
  const codeVisible = showCode || !svg

  return (
    <figure className="overflow-hidden rounded-2xl border border-line bg-paper">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-cream/60 px-3 py-2">
        <span className="mr-auto text-[11px] font-medium uppercase tracking-[0.18em] text-muted">
          Diagram{!done && ' · writing…'}
        </span>
        {svg && (
          <button type="button" className={btn} onClick={() => setShowCode((v) => !v)}>
            {showCode ? <ImageIcon className="size-3.5" /> : <Code2 className="size-3.5" />}
            {showCode ? 'Diagram' : 'Code'}
          </button>
        )}
        <button type="button" className={btn} onClick={copy}>
          {copied ? <Check className="size-3.5 text-gold-deep" /> : <Copy className="size-3.5" />}
          {copied ? 'Copied' : 'Copy code'}
        </button>
        <button type="button" className={btn} disabled={!svg} onClick={() => svg && download('diagram.svg', new Blob([svg], { type: 'image/svg+xml' }))}>
          <Download className="size-3.5" /> SVG
        </button>
        <button type="button" className={btn} disabled={!svg} onClick={() => svg && svgToPng(svg).then((b) => download('diagram.png', b)).catch(() => setError('PNG export failed'))}>
          <Download className="size-3.5" /> PNG
        </button>
        <button type="button" className={btn} onClick={() => download('diagram.mmd', new Blob([code], { type: 'text/plain' }))}>
          <Download className="size-3.5" /> .mmd
        </button>
      </div>
      {svg && !showCode && (
        // Mermaid output is generated with securityLevel "strict" (no scripts, no HTML labels).
        <div className="overflow-x-auto p-4 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />
      )}
      {codeVisible && (
        <pre className={cn('overflow-x-auto p-4 font-mono text-[13px] leading-relaxed', error && 'pb-2')}>{code}</pre>
      )}
      {error && done && <p className="px-4 pb-3 text-xs text-brick">Couldn't draw this diagram: {error}. The code is shown instead.</p>}
    </figure>
  )
}
