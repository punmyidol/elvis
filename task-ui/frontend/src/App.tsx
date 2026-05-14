import { useState, useEffect } from 'react'
import RunForm from './components/RunForm'
import RunList from './components/RunList'
import RunDetail from './components/RunDetail'
import CadView from './components/CadView'
import ThinkView from './components/ThinkView'
import ChatView from './components/ChatView'
import WeeklyView from './components/WeeklyView'
import BrainView from './components/BrainView'
import { fetchRuns } from './api'
import type { RunSummary } from './types'

type View = 'tasks' | 'cad' | 'think' | 'chat' | 'weekly' | 'brain'

interface ActiveRun {
  runId: string
  autoStart: boolean
}

const NAV_LABELS: Record<View, string> = {
  tasks: 'Tasks',
  cad: 'CAD',
  think: 'Think',
  chat: 'Chat',
  weekly: 'Weekly',
  brain: 'Brain',
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

  const isChat = view === 'chat'

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-mono">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <h1 className="text-base font-semibold tracking-tight text-white">Elvis</h1>
        <nav className="flex gap-1">
          {(['tasks', 'cad', 'think', 'chat', 'weekly', 'brain'] as View[]).map(v => (
            <button
              key={v}
              onClick={() => handleViewChange(v)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                view === v
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {NAV_LABELS[v]}
            </button>
          ))}
        </nav>
      </header>

      <main className={`${isChat ? 'max-w-5xl' : 'max-w-3xl'} mx-auto px-6 py-8 ${isChat ? '' : 'space-y-10'}`}>
        {view === 'chat' ? (
          <ChatView />
        ) : view === 'think' ? (
          <ThinkView />
        ) : view === 'cad' ? (
          <CadView />
        ) : view === 'weekly' ? (
          <WeeklyView />
        ) : view === 'brain' ? (
          <BrainView />
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
