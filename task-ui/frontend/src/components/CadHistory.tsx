import type { CadOutput } from '../types'

interface Props {
  history: CadOutput[]
  onRefresh: () => void
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export default function CadHistory({ history, onRefresh }: Props) {
  if (history.length === 0) {
    return (
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">History</h2>
          <button onClick={onRefresh} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Refresh</button>
        </div>
        <p className="text-xs text-gray-600">No generations yet.</p>
      </section>
    )
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">History</h2>
        <button onClick={onRefresh} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Refresh</button>
      </div>
      <div className="border border-gray-800 rounded-lg overflow-hidden divide-y divide-gray-800">
        {history.map(item => (
          <div key={item.id} className="flex items-center gap-3 px-4 py-3 hover:bg-gray-900 transition-colors">
            <span
              className={`shrink-0 px-2 py-0.5 rounded text-xs ${
                item.success
                  ? 'bg-green-900/60 text-green-400'
                  : 'bg-red-900/60 text-red-400'
              }`}
            >
              {item.success ? 'done' : 'failed'}
            </span>
            <span className="flex-1 min-w-0 text-sm text-gray-300 truncate" title={item.prompt}>
              {item.prompt}
            </span>
            <span className="text-xs text-gray-600 shrink-0 tabular-nums">
              {item.attempts} attempt{item.attempts !== 1 ? 's' : ''}
            </span>
            <span className="text-xs text-gray-600 shrink-0 hidden sm:block">
              {formatDate(item.created_at)}
            </span>
            {item.success && item.basename && (
              <a
                href={`/api/cad/file/${item.basename}`}
                download
                className="shrink-0 px-2.5 py-1 text-xs border border-gray-700 text-gray-400 rounded hover:border-gray-500 hover:text-gray-200 transition-colors"
                onClick={e => e.stopPropagation()}
              >
                .step
              </a>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
