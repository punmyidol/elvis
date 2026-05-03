import { useState } from 'react'
import { startRun } from '../api'

interface Props {
  onRunStarted: (runId: string) => void
}

export default function RunForm({ onRunStarted }: Props) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    const tasks = text.split('\n').map(t => t.trim()).filter(Boolean)
    if (!tasks.length) return
    setLoading(true)
    setError(null)
    try {
      const runId = await startRun(tasks)
      setText('')
      onRunStarted(runId)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') handleSubmit()
  }

  return (
    <section className="space-y-3">
      <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">New Run</h2>
      <textarea
        className="w-full bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-gray-600 resize-none"
        rows={5}
        placeholder={"One task per line:\nSummarize today's news\nCheck my calendar this week\nBased on the above, what should I focus on?"}
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="flex items-center gap-4">
        <button
          onClick={handleSubmit}
          disabled={loading || !text.trim()}
          className="px-4 py-2 bg-white text-gray-950 text-sm font-semibold rounded-lg disabled:opacity-30 hover:bg-gray-200 transition-colors"
        >
          {loading ? 'Starting…' : 'Start Run'}
        </button>
        <span className="text-xs text-gray-600">⌘ + Enter</span>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </section>
  )
}
