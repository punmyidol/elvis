import { useState, useEffect } from 'react'
import RunForm from './components/RunForm'
import RunList from './components/RunList'
import RunDetail from './components/RunDetail'
import { fetchRuns } from './api'
import type { RunSummary } from './types'

interface ActiveRun {
  runId: string
  autoStart: boolean
}

export default function App() {
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

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-mono">
      <header className="border-b border-gray-800 px-6 py-4">
        <h1 className="text-base font-semibold tracking-tight text-white">Elvis / Task Runner</h1>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-10">
        {active ? (
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
