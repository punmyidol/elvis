import { useState, useEffect } from 'react'
import RunForm from './components/RunForm'
import RunList from './components/RunList'
import RunDetail from './components/RunDetail'
import CadView from './components/CadView'
import { fetchRuns } from './api'
import type { RunSummary } from './types'

type View = 'tasks' | 'cad'

interface ActiveRun {
  runId: string
  autoStart: boolean
}

export default function App() {
  const [view, setView] = useState<View>('tasks')
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [active, setActive] = useState<ActiveRun | null>(null)

  const loadRuns = async () => {
    try {
      setRuns(await fetchRuns())
    } catch {
      // server not ready yet
    }
  }

  useEffect(() => { loadRuns() }, [])

  const handleViewChange = (v: View) => {
    setView(v)
    setActive(null)
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-mono">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <h1 className="text-base font-semibold tracking-tight text-white">Elvis</h1>
        <nav className="flex gap-1">
          {(['tasks', 'cad'] as View[]).map(v => (
            <button
              key={v}
              onClick={() => handleViewChange(v)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors capitalize ${
                view === v
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {v === 'tasks' ? 'Tasks' : 'CAD'}
            </button>
          ))}
        </nav>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-10">
        {view === 'cad' ? (
          <CadView />
        ) : active ? (
          <RunDetail
            runId={active.runId}
            autoStart={active.autoStart}
            onBack={() => { setActive(null); loadRuns() }}
            onComplete={loadRuns}
          />
        ) : (
          <>
            <RunForm
              onRunStarted={(runId) => setActive({ runId, autoStart: true })}
            />
            <RunList
              runs={runs}
              onSelect={(runId) => setActive({ runId, autoStart: false })}
              onRefresh={loadRuns}
            />
          </>
        )}
      </main>
    </div>
  )
}
