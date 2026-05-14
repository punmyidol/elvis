import { useEffect, useMemo, useState } from 'react'
import {
  fetchBrainSurfaced,
  fetchBrainStats,
  fetchBrainTrend,
  fetchBrainNote,
  runEngagementCheck,
  runSurfacing,
} from '../api'
import type {
  BrainBucket,
  BrainStats,
  BrainSurfacedRow,
  BrainTrendPoint,
} from '../types'
import { MarkdownContent } from './FileViewer'

const SIGNAL_TONE: Record<string, string> = {
  obsidian: 'bg-purple-900/60 text-purple-300',
  git: 'bg-amber-900/60 text-amber-300',
}

function formatDate(iso: string) {
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'))
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function formatRate(rate: number) {
  return `${(rate * 100).toFixed(0)}%`
}

function StatCard({ label, bucket }: { label: string; bucket: BrainBucket }) {
  return (
    <div className="border border-gray-800 rounded-lg px-4 py-3 space-y-1">
      <p className="text-xs text-gray-500 uppercase tracking-widest">{label}</p>
      <p className="text-2xl font-semibold text-gray-100 tabular-nums">
        {bucket.total === 0 ? '—' : formatRate(bucket.rate)}
      </p>
      <p className="text-xs text-gray-600 tabular-nums">
        {bucket.engaged} / {bucket.total} engaged
      </p>
    </div>
  )
}

function TrendChart({ points }: { points: BrainTrendPoint[] }) {
  if (points.length === 0) {
    return <p className="text-xs text-gray-600">No surfacings in this window.</p>
  }
  const max = Math.max(1, ...points.map(p => p.surfaced))
  const w = 600
  const h = 120
  const padding = { top: 8, right: 8, bottom: 18, left: 24 }
  const innerW = w - padding.left - padding.right
  const innerH = h - padding.top - padding.bottom
  const barW = innerW / points.length

  return (
    <div className="border border-gray-800 rounded-lg p-3">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-32" preserveAspectRatio="none">
        <line
          x1={padding.left}
          y1={padding.top + innerH}
          x2={padding.left + innerW}
          y2={padding.top + innerH}
          stroke="rgb(31, 41, 55)"
          strokeWidth={1}
        />
        {[0, 0.5, 1].map(t => (
          <text
            key={t}
            x={padding.left - 4}
            y={padding.top + innerH - innerH * t + 3}
            textAnchor="end"
            fontSize={9}
            fill="rgb(75, 85, 99)"
          >
            {Math.round(max * t)}
          </text>
        ))}
        {points.map((p, i) => {
          const x = padding.left + i * barW
          const surfacedH = (p.surfaced / max) * innerH
          const engagedH = (p.engaged / max) * innerH
          return (
            <g key={p.day}>
              <rect
                x={x + 1}
                y={padding.top + innerH - surfacedH}
                width={Math.max(0, barW - 2)}
                height={surfacedH}
                fill="rgb(55, 65, 81)"
              >
                <title>{`${p.day}: ${p.surfaced} surfaced`}</title>
              </rect>
              <rect
                x={x + 1}
                y={padding.top + innerH - engagedH}
                width={Math.max(0, barW - 2)}
                height={engagedH}
                fill="rgb(34, 197, 94)"
              >
                <title>{`${p.day}: ${p.engaged} engaged`}</title>
              </rect>
            </g>
          )
        })}
      </svg>
      <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-gray-700" />
          surfaced
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-green-500" />
          engaged
        </span>
        <span className="ml-auto tabular-nums">
          {points[0].day} → {points[points.length - 1].day}
        </span>
      </div>
    </div>
  )
}

function SurfacedRow({ row }: { row: BrainSurfacedRow }) {
  const [expanded, setExpanded] = useState(false)
  const [body, setBody] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const toggle = async () => {
    const next = !expanded
    setExpanded(next)
    if (next && body === null && !loading) {
      setLoading(true)
      setError(null)
      try {
        const { content } = await fetchBrainNote(row.obsidian_note_path)
        setBody(content)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    }
  }

  return (
    <article className="border border-gray-800 rounded-lg hover:border-gray-700 transition-colors">
      <button
        onClick={toggle}
        className="w-full px-4 py-3 flex items-start gap-3 text-left"
      >
        <span
          className={`mt-0.5 shrink-0 inline-flex items-center justify-center w-5 h-5 rounded text-xs ${
            row.engaged
              ? 'bg-green-900/60 text-green-300'
              : 'bg-gray-800 text-gray-500'
          }`}
          title={row.engaged ? 'Engaged' : 'Not engaged'}
        >
          {row.engaged ? '✓' : '·'}
        </span>
        <div className="flex-1 min-w-0 space-y-1">
          <p className="text-sm text-gray-200 leading-snug">{row.topic}</p>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-gray-600 tabular-nums">
              {formatDate(row.created_at)}
            </span>
            {row.source_signals.map(s => (
              <span
                key={s}
                className={`px-1.5 py-0.5 rounded text-[10px] ${
                  SIGNAL_TONE[s] ?? 'bg-gray-800 text-gray-400'
                }`}
              >
                {s}
              </span>
            ))}
          </div>
        </div>
        <span className="shrink-0 text-gray-600 text-xs mt-1">
          {expanded ? '−' : '+'}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-gray-800 px-4 py-3 space-y-3">
          <p className="text-xs text-gray-500">
            <span className="text-gray-600 uppercase tracking-widest mr-2">Reason</span>
            {row.reason}
          </p>
          <p className="text-xs text-gray-600 font-mono break-all">
            {row.obsidian_note_path}
          </p>
          {loading && <p className="text-xs text-gray-600">Loading note…</p>}
          {error && <p className="text-xs text-red-400">{error}</p>}
          {body !== null && (
            <div className="border-t border-gray-800 pt-3">
              <MarkdownContent content={body} />
            </div>
          )}
        </div>
      )}
    </article>
  )
}

export default function BrainView() {
  const [rows, setRows] = useState<BrainSurfacedRow[]>([])
  const [stats, setStats] = useState<BrainStats | null>(null)
  const [trend, setTrend] = useState<BrainTrendPoint[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'engaged' | 'not_engaged'>('all')

  const [engagementBusy, setEngagementBusy] = useState(false)
  const [surfaceBusy, setSurfaceBusy] = useState(false)
  const [runLog, setRunLog] = useState<string[]>([])

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [r, s, t] = await Promise.all([
        fetchBrainSurfaced(),
        fetchBrainStats(),
        fetchBrainTrend(30),
      ])
      setRows(r)
      setStats(s)
      setTrend(t)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const filtered = useMemo(() => {
    if (filter === 'engaged') return rows.filter(r => r.engaged)
    if (filter === 'not_engaged') return rows.filter(r => !r.engaged)
    return rows
  }, [rows, filter])

  const handleEngagement = async () => {
    setEngagementBusy(true)
    setRunLog([])
    try {
      const result = await runEngagementCheck()
      setRunLog([
        `Engagement check complete. Newly engaged: ${result.newly_engaged}. Total engaged: ${result.total_engaged}.`,
      ])
      await load()
    } catch (e) {
      setRunLog([`Error: ${e instanceof Error ? e.message : String(e)}`])
    } finally {
      setEngagementBusy(false)
    }
  }

  const handleSurface = async () => {
    setSurfaceBusy(true)
    setRunLog(['Starting surfacing pass…'])
    try {
      for await (const ev of runSurfacing()) {
        if (ev.type === 'log') {
          setRunLog(prev => [...prev, ev.message])
        } else if (ev.type === 'done') {
          setRunLog(prev => [...prev, `Done. Wrote ${ev.written} note(s).`])
        } else if (ev.type === 'error') {
          setRunLog(prev => [...prev, `Error: ${ev.message}`])
        }
      }
      await load()
    } catch (e) {
      setRunLog(prev => [...prev, `Error: ${e instanceof Error ? e.message : String(e)}`])
    } finally {
      setSurfaceBusy(false)
    }
  }

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">
          Brain — Surfaced Topics
        </h2>
        <button
          onClick={load}
          className="text-xs text-gray-600 hover:text-gray-300 transition-colors"
        >
          Refresh
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {stats && (
        <div className="grid grid-cols-3 gap-3">
          <StatCard label="Last 7 days" bucket={stats.last_7d} />
          <StatCard label="Last 30 days" bucket={stats.last_30d} />
          <StatCard label="All time" bucket={stats.all_time} />
        </div>
      )}

      {stats && Object.keys(stats.by_signal).length > 0 && (
        <div className="flex flex-wrap gap-3 text-xs">
          <span className="text-gray-500 uppercase tracking-widest">By signal</span>
          {Object.entries(stats.by_signal).map(([sig, b]) => (
            <span key={sig} className="text-gray-400">
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] mr-1.5 ${
                  SIGNAL_TONE[sig] ?? 'bg-gray-800 text-gray-400'
                }`}
              >
                {sig}
              </span>
              <span className="tabular-nums">
                {b.engaged}/{b.total} · {b.total === 0 ? '—' : formatRate(b.rate)}
              </span>
            </span>
          ))}
        </div>
      )}

      <div>
        <p className="text-xs text-gray-500 uppercase tracking-widest mb-2">
          Last 30 days
        </p>
        <TrendChart points={trend} />
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-gray-500 uppercase tracking-widest">Manual run</span>
          <button
            onClick={handleSurface}
            disabled={surfaceBusy || engagementBusy}
            className="px-3 py-1.5 rounded-md border border-gray-800 text-gray-300 hover:border-gray-600 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {surfaceBusy ? 'Surfacing…' : 'Run surfacer'}
          </button>
          <button
            onClick={handleEngagement}
            disabled={surfaceBusy || engagementBusy}
            className="px-3 py-1.5 rounded-md border border-gray-800 text-gray-300 hover:border-gray-600 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {engagementBusy ? 'Checking…' : 'Run engagement check'}
          </button>
        </div>
        {runLog.length > 0 && (
          <pre className="text-xs text-gray-400 bg-gray-900 border border-gray-800 rounded-md p-3 max-h-48 overflow-auto whitespace-pre-wrap leading-relaxed">
            {runLog.join('\n')}
          </pre>
        )}
      </div>

      <div className="flex items-center gap-2 text-xs pt-2">
        <span className="text-gray-500 uppercase tracking-widest">Filter</span>
        {(['all', 'engaged', 'not_engaged'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2 py-1 rounded transition-colors ${
              filter === f
                ? 'bg-gray-800 text-white'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {f === 'not_engaged' ? 'not engaged' : f}
          </button>
        ))}
        <span className="ml-auto text-gray-600 tabular-nums">
          {filtered.length} / {rows.length}
        </span>
      </div>

      {loading ? (
        <p className="text-xs text-gray-600">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-xs text-gray-600">No surfaced topics match this filter.</p>
      ) : (
        <div className="space-y-2">
          {filtered.map(r => (
            <SurfacedRow key={r.id} row={r} />
          ))}
        </div>
      )}
    </section>
  )
}
