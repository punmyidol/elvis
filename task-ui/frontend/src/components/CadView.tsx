import { useState, useEffect } from 'react'
import { generateCad, fetchCadHistory } from '../api'
import type { CadStatusEvent, CadOutput } from '../types'
import CadForm from './CadForm'
import CadResult from './CadResult'
import CadHistory from './CadHistory'

export default function CadView() {
  const [events, setEvents] = useState<CadStatusEvent[]>([])
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<CadStatusEvent | null>(null)
  const [history, setHistory] = useState<CadOutput[]>([])

  const loadHistory = async () => {
    try {
      setHistory(await fetchCadHistory())
    } catch {
      // server not ready
    }
  }

  useEffect(() => { loadHistory() }, [])

  const handleGenerate = async (prompt: string) => {
    setGenerating(true)
    setEvents([])
    setResult(null)

    try {
      for await (const event of generateCad(prompt)) {
        setEvents(prev => [...prev, event])
        if (event.status === 'done') {
          setResult(event)
          if (event.success) loadHistory()
        }
      }
    } catch (e) {
      const errEvent: CadStatusEvent = {
        status: 'done',
        success: false,
        message: String(e),
      }
      setEvents(prev => [...prev, errEvent])
      setResult(errEvent)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-10">
      <CadForm onGenerate={handleGenerate} disabled={generating} />
      {(events.length > 0 || result) && (
        <CadResult events={events} generating={generating} result={result} />
      )}
      <CadHistory history={history} onRefresh={loadHistory} />
    </div>
  )
}
