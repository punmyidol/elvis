import { useState, useEffect, useRef, useCallback } from 'react'
import { fetchRun, fetchTaskFile, openStream } from '../api'
import type { Run, Task } from '../types'
import FileViewer from './FileViewer'

interface Props {
  runId: string
  autoStart: boolean
  onBack: () => void
  onComplete: () => void
}

const STATUS_BADGE: Record<string, string> = {
  done:    'bg-green-900/60 text-green-400',
  failed:  'bg-red-900/60 text-red-400',
  pending: 'bg-gray-800 text-gray-500',
}

export default function RunDetail({ runId, autoStart, onBack, onComplete }: Props) {
  const [run, setRun] = useState<Run | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [streaming, setStreaming] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [fileCache, setFileCache] = useState<Record<number, string>>({})
  const esRef = useRef<EventSource | null>(null)
  const logEndRef = useRef<HTMLDivElement | null>(null)

  const refreshRun = useCallback(() => fetchRun(runId).then(setRun), [runId])

  useEffect(() => {
    refreshRun()
    return () => esRef.current?.close()
  }, [refreshRun])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [log])

  const startStream = useCallback(() => {
    if (streaming || esRef.current) return
    setStreaming(true)
    const es = openStream(runId)
    esRef.current = es

    es.onmessage = async (e: MessageEvent) => {
      const data = JSON.parse(e.data as string) as { done?: boolean; line?: string }
      if (data.done) {
        es.close()
        esRef.current = null
        setStreaming(false)
        await refreshRun()
        onComplete()
      } else if (data.line) {
        setLog(prev => [...prev, data.line!])
        if (data.line.includes('Done ->') || data.line.includes('Failed:')) {
          refreshRun()
        }
      }
    }

    es.onerror = () => {
      es.close()
      esRef.current = null
      setStreaming(false)
    }
  }, [runId, streaming, refreshRun, onComplete])

  useEffect(() => {
    if (autoStart && run && !streaming) startStream()
  }, [autoStart, run, streaming, startStream])

  const toggleOutput = async (task: Task) => {
    if (!task.output_file) return
    if (expanded === task.index) {
      setExpanded(null)
      return
    }
    setExpanded(task.index)
    if (!(task.index in fileCache)) {
      const content = await fetchTaskFile(runId, task.output_file)
      setFileCache(prev => ({ ...prev, [task.index]: content }))
    }
  }

  const hasPending = run?.tasks.some(t => t.status === 'pending') ?? false

  return (
    <section className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="text-xs text-gray-600 hover:text-gray-300 transition-colors"
        >
          ← Back
        </button>
        <span className="text-sm text-gray-300">{runId}</span>
        {hasPending && !streaming && (
          <button
            onClick={startStream}
            className="ml-auto px-4 py-1.5 bg-white text-gray-950 text-xs font-semibold rounded-lg hover:bg-gray-200 transition-colors"
          >
            Resume
          </button>
        )}
        {streaming && (
          <span className="ml-auto text-xs text-yellow-400 animate-pulse">Running…</span>
        )}
      </div>

      {/* Tasks */}
      {run && (
        <div className="space-y-2">
          {run.tasks.map(task => (
            <div key={task.index} className="border border-gray-800 rounded-lg overflow-hidden">
              <button
                onClick={() => toggleOutput(task)}
                disabled={task.status !== 'done'}
                className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-gray-900 disabled:cursor-default transition-colors"
              >
                <span className="text-xs text-gray-600 pt-0.5 w-5 shrink-0 tabular-nums">
                  {task.index}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-sm text-gray-200 leading-relaxed">
                    {task.task}
                  </span>
                  {task.output_file && (
                    <span className="block text-xs text-gray-500 mt-0.5 truncate">
                      {task.output_file}
                    </span>
                  )}
                </span>
                <span className={`px-2 py-0.5 rounded text-xs shrink-0 ${STATUS_BADGE[task.status]}`}>
                  {task.status}
                </span>
              </button>

              {task.status === 'failed' && task.error && (
                <p className="px-4 pb-3 text-xs text-red-400 border-t border-gray-800 pt-2">
                  {task.error}
                </p>
              )}

              {expanded === task.index && task.index in fileCache && (
                <div className="border-t border-gray-800 px-4 py-3 bg-gray-900">
                  <FileViewer filename={task.output_file!} content={fileCache[task.index]} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Live log */}
      {log.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-gray-600 uppercase tracking-widest">Log</p>
          <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 space-y-1 max-h-48 overflow-y-auto">
            {log.map((line, i) => (
              <p key={i} className="text-xs text-gray-400">{line}</p>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </section>
  )
}
