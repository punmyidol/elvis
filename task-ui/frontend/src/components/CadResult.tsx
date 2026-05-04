import { useState, useEffect, useRef } from 'react'
import { fetchCadScript } from '../api'
import type { CadStatusEvent } from '../types'

interface Props {
  events: CadStatusEvent[]
  generating: boolean
  result: CadStatusEvent | null
}

const STATUS_ICON: Record<string, string> = {
  context:    '📚',
  generating: '✏️',
  executing:  '⚙️',
  validating: '🔍',
  retry:      '↩️',
  done:       '',
}

export default function CadResult({ events, generating, result }: Props) {
  const [scriptOpen, setScriptOpen] = useState(false)
  const [script, setScript] = useState<string | null>(null)
  const [scriptLoading, setScriptLoading] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const loadScript = async (basename: string) => {
    if (script !== null) { setScriptOpen(o => !o); return }
    setScriptLoading(true)
    try {
      const content = await fetchCadScript(basename)
      setScript(content)
      setScriptOpen(true)
    } catch {
      setScript('// Could not load script')
      setScriptOpen(true)
    } finally {
      setScriptLoading(false)
    }
  }

  const nonDoneEvents = events.filter(e => e.status !== 'done')

  return (
    <section className="space-y-4">
      {/* Status log */}
      {nonDoneEvents.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 space-y-1.5 max-h-40 overflow-y-auto">
          {nonDoneEvents.map((ev, i) => (
            <p key={i} className="text-xs text-gray-400 flex gap-2">
              <span>{STATUS_ICON[ev.status] ?? '•'}</span>
              <span>{ev.message}</span>
            </p>
          ))}
          {generating && (
            <p className="text-xs text-yellow-400 animate-pulse">Working…</p>
          )}
          <div ref={logEndRef} />
        </div>
      )}

      {/* Result card */}
      {result && (
        result.success ? (
          <div className="border border-green-900/60 bg-green-950/20 rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-3">
              <span className="text-green-400 text-sm font-medium">✓ {result.message}</span>
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <a
                href={`/api/cad/file/${result.basename}`}
                download
                className="px-4 py-2 bg-white text-gray-950 text-sm font-semibold rounded-lg hover:bg-gray-200 transition-colors"
              >
                Download STEP
              </a>
              <button
                onClick={() => result.basename && loadScript(result.basename)}
                disabled={scriptLoading}
                className="px-4 py-2 border border-gray-700 text-gray-300 text-sm rounded-lg hover:border-gray-500 transition-colors disabled:opacity-50"
              >
                {scriptLoading ? 'Loading…' : scriptOpen ? 'Hide script' : 'View script'}
              </button>
              <span className="text-xs text-gray-600">{result.attempts} attempt(s)</span>
            </div>
            {scriptOpen && script !== null && (
              <pre className="mt-2 bg-gray-900 border border-gray-800 rounded-lg p-3 text-xs text-gray-300 overflow-x-auto max-h-64 overflow-y-auto">
                {script}
              </pre>
            )}
          </div>
        ) : (
          <div className="border border-red-900/60 bg-red-950/10 rounded-lg p-4 space-y-3">
            <p className="text-red-400 text-sm font-medium">✗ {result.message}</p>
            {result.error && (
              <p className="text-xs text-gray-500">{result.error}</p>
            )}
            {result.script && (
              <>
                <button
                  onClick={() => setScriptOpen(o => !o)}
                  className="text-xs text-gray-500 hover:text-gray-300 underline"
                >
                  {scriptOpen ? 'Hide last script' : 'Show last script'}
                </button>
                {scriptOpen && (
                  <pre className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-xs text-gray-400 overflow-x-auto max-h-48 overflow-y-auto">
                    {result.script}
                  </pre>
                )}
              </>
            )}
          </div>
        )
      )}
    </section>
  )
}
