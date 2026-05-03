import type { RunSummary } from '../types'

interface Props {
  runs: RunSummary[]
  onSelect: (runId: string) => void
  onRefresh: () => void
}

export default function RunList({ runs, onSelect, onRefresh }: Props) {
  if (!runs.length) return null

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">Past Runs</h2>
        <button
          onClick={onRefresh}
          className="text-xs text-gray-600 hover:text-gray-300 transition-colors"
        >
          Refresh
        </button>
      </div>

      <div className="divide-y divide-gray-800 border border-gray-800 rounded-lg overflow-hidden">
        {runs.map(r => {
          const pending = r.total - r.done - r.failed
          return (
            <button
              key={r.run_id}
              onClick={() => onSelect(r.run_id)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-900 transition-colors"
            >
              <div className="space-y-0.5">
                <p className="text-sm text-gray-200">{r.run_id}</p>
                <p className="text-xs text-gray-600">
                  {new Date(r.created_at).toLocaleString()}
                </p>
              </div>
              <div className="flex gap-2 shrink-0">
                {r.done > 0 && (
                  <span className="px-2 py-0.5 rounded text-xs bg-green-900/60 text-green-400">
                    {r.done} done
                  </span>
                )}
                {r.failed > 0 && (
                  <span className="px-2 py-0.5 rounded text-xs bg-red-900/60 text-red-400">
                    {r.failed} failed
                  </span>
                )}
                {pending > 0 && (
                  <span className="px-2 py-0.5 rounded text-xs bg-gray-800 text-gray-500">
                    {pending} pending
                  </span>
                )}
              </div>
            </button>
          )
        })}
      </div>
    </section>
  )
}
