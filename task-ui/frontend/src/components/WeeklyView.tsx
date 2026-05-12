import { useEffect, useMemo, useState } from 'react'
import { fetchWeeklySummaries } from '../api'
import type { WeeklySummary } from '../types'

const ALL = '__all__'

const SOURCE_TONE: Record<string, string> = {
  email:     'bg-blue-900/60 text-blue-300',
  obsidian:  'bg-purple-900/60 text-purple-300',
  git:       'bg-amber-900/60 text-amber-300',
  calendar:  'bg-teal-900/60 text-teal-300',
  todo:      'bg-pink-900/60 text-pink-300',
  goodnotes: 'bg-indigo-900/60 text-indigo-300',
}

function formatDate(iso: string) {
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'))
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export default function WeeklyView() {
  const [rows, setRows] = useState<WeeklySummary[]>([])
  const [period, setPeriod] = useState<string>(ALL)
  const [source, setSource] = useState<string>(ALL)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await fetchWeeklySummaries())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const periods = useMemo(
    () => Array.from(new Set(rows.map(r => r.period))).sort().reverse(),
    [rows],
  )
  const sources = useMemo(
    () => Array.from(new Set(rows.map(r => r.source))).sort(),
    [rows],
  )

  const filtered = rows.filter(r =>
    (period === ALL || r.period === period) &&
    (source === ALL || r.source === source),
  )

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">
          Weekly Summaries
        </h2>
        <button
          onClick={load}
          className="text-xs text-gray-600 hover:text-gray-300 transition-colors"
        >
          Refresh
        </button>
      </div>

      <div className="flex flex-wrap gap-3 items-center text-xs">
        <label className="flex items-center gap-2">
          <span className="text-gray-500">Week</span>
          <select
            value={period}
            onChange={e => setPeriod(e.target.value)}
            className="bg-gray-900 border border-gray-800 rounded px-2 py-1 text-gray-200 focus:outline-none focus:border-gray-600"
          >
            <option value={ALL}>All weeks</option>
            {periods.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-gray-500">Source</span>
          <select
            value={source}
            onChange={e => setSource(e.target.value)}
            className="bg-gray-900 border border-gray-800 rounded px-2 py-1 text-gray-200 focus:outline-none focus:border-gray-600"
          >
            <option value={ALL}>All sources</option>
            {sources.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <span className="text-gray-600 ml-auto tabular-nums">
          {filtered.length} / {rows.length}
        </span>
      </div>

      {error && (
        <p className="text-xs text-red-400">{error}</p>
      )}

      {loading ? (
        <p className="text-xs text-gray-600">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-xs text-gray-600">No summaries match these filters.</p>
      ) : (
        <div className="space-y-3">
          {filtered.map(r => (
            <article
              key={r.id}
              className="border border-gray-800 rounded-lg px-4 py-3 space-y-2 hover:border-gray-700 transition-colors"
            >
              <header className="flex items-center gap-2">
                <span className="shrink-0 px-2 py-0.5 rounded text-xs bg-gray-800 text-gray-200 tabular-nums">
                  {r.period}
                </span>
                <span
                  className={`shrink-0 px-2 py-0.5 rounded text-xs ${
                    SOURCE_TONE[r.source] ?? 'bg-gray-800 text-gray-300'
                  }`}
                >
                  {r.source}
                </span>
                <span className="ml-auto text-xs text-gray-600">
                  {formatDate(r.created_at)}
                </span>
              </header>
              <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">
                {r.summary}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
