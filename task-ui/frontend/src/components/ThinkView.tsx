import { useEffect, useRef, useState } from 'react'
import { applyThinkToVault, continueThinking, fetchThinkFile, fetchThinkFiles, fetchThinkSession, fetchThinkSessions, startThinking } from '../api'
import type { ThinkEvent, ThinkFile, ThinkSessionSummary, ThinkTask } from '../types'
import FileViewer, { MarkdownContent } from './FileViewer'

function TaskBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: 'bg-green-900 text-green-300',
    failed: 'bg-red-900 text-red-300',
    skipped: 'bg-gray-700 text-gray-400',
    running: 'bg-yellow-900 text-yellow-300',
    pending: 'bg-gray-800 text-gray-500',
    invalidated: 'bg-orange-900 text-orange-300',
  }
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${colors[status] ?? 'bg-gray-800 text-gray-400'}`}>
      {status}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    paused: 'text-yellow-500',
    running: 'text-blue-400',
    done: 'text-gray-500',
  }
  return <span className={`text-[10px] font-mono ${colors[status] ?? 'text-gray-500'}`}>{status}</span>
}

function UserTaskPrompts({ tasks }: { tasks: ThinkTask[] }) {
  const pending = tasks.filter(t => t.type === 'user_task' && t.status !== 'skipped')
  const skipped = tasks.filter(t => t.type === 'user_task' && t.status === 'skipped')
  const all = [...pending, ...skipped]
  if (all.length === 0) return null
  return (
    <div className="border border-yellow-800 bg-yellow-950/30 rounded-lg p-4 space-y-2">
      <p className="text-xs font-semibold text-yellow-500 uppercase tracking-wider">
        ⚠ Waiting on you
      </p>
      <ul className="space-y-2">
        {all.map(t => (
          <li key={t.id} className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 text-yellow-600">›</span>
            <span className={`text-sm leading-relaxed ${t.status === 'skipped' ? 'text-yellow-300' : 'text-yellow-200'}`}>
              {t.description}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function TaskList({ tasks }: { tasks: ThinkTask[] }) {
  const byType: Record<string, ThinkTask[]> = {}
  for (const t of tasks) {
    ;(byType[t.type] ??= []).push(t)
  }
  return (
    <div className="space-y-3">
      {Object.entries(byType).map(([type, items]) => (
        <div key={type}>
          <p className="text-[10px] uppercase tracking-wider text-gray-600 mb-1">{type.replace(/_/g, ' ')}</p>
          <div className="space-y-1">
            {items.map(t => (
              <div key={t.id} className="flex items-start gap-2 text-xs text-gray-400">
                <TaskBadge status={t.status} />
                <span className="leading-relaxed">{t.description}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function SessionList({
  sessions,
  onSelect,
  onRefresh,
}: {
  sessions: ThinkSessionSummary[]
  onSelect: (id: string) => void
  onRefresh: () => void
}) {
  if (sessions.length === 0) return null
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-500 uppercase tracking-wider">Past sessions</p>
        <button onClick={onRefresh} className="text-[11px] text-gray-600 hover:text-gray-400 transition-colors">
          Refresh
        </button>
      </div>
      <div className="space-y-1">
        {sessions.map(s => (
          <button
            key={s.session_id}
            onClick={() => onSelect(s.session_id)}
            className="w-full text-left px-3 py-2.5 rounded-md bg-gray-900 hover:bg-gray-800 border border-gray-800 hover:border-gray-700 transition-colors group"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-gray-300 truncate flex-1">{s.prompt}</span>
              <div className="flex items-center gap-2 shrink-0">
                <StatusBadge status={s.status} />
                <span className="text-[10px] text-gray-600">pass {s.iteration}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-[10px] font-mono text-gray-600">{s.session_id}</span>
              <span className="text-[10px] text-gray-700">
                {new Date(s.created_at).toLocaleString()}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button
      onClick={handleCopy}
      className="text-[11px] text-gray-600 hover:text-gray-400 transition-colors"
    >
      {copied ? '✓ copied' : 'copy'}
    </button>
  )
}

function fileLabel(f: ThinkFile): string {
  if (f.is_checkpoint) {
    const n = f.filename.match(/checkpoint_(\d+)/)?.[1] ?? '?'
    return `Pass ${n} checkpoint`
  }
  return f.filename.replace(/\.md$/, '').replace(/_/g, ' ')
}

export default function ThinkView() {
  const [prompt, setPrompt] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionStatus, setSessionStatus] = useState<string>('running')
  const [streaming, setStreaming] = useState(false)
  const [statusLines, setStatusLines] = useState<string[]>([])
  const [tasks, setTasks] = useState<ThinkTask[]>([])
  const [checkpoint, setCheckpoint] = useState<string | null>(null)
  const [isDone, setIsDone] = useState(false)
  const [continueInput, setContinueInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sessions, setSessions] = useState<ThinkSessionSummary[]>([])
  const [files, setFiles] = useState<ThinkFile[]>([])
  const [openFile, setOpenFile] = useState<{ label: string; filename: string; content: string } | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [applyStatus, setApplyStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [applyResult, setApplyResult] = useState<{ applied: string[]; destination: string } | null>(null)
  const continueRef = useRef<HTMLInputElement>(null)

  const loadSessions = async () => {
    try { setSessions(await fetchThinkSessions()) } catch { /* server not ready */ }
  }

  const loadFiles = async (sid: string) => {
    try { setFiles(await fetchThinkFiles(sid)) } catch { /* ignore */ }
  }

  useEffect(() => { loadSessions() }, [])

  const runStream = async (gen: AsyncGenerator<ThinkEvent>) => {
    setStreaming(true)
    setError(null)
    let capturedSessionId: string | null = null
    try {
      for await (const event of gen) {
        switch (event.type) {
          case 'layer':
            if (event.message) setStatusLines(prev => [...prev, event.message!])
            break
          case 'tasks':
            if (event.tasks) setTasks(event.tasks)
            break
          case 'checkpoint':
            if (event.text) setCheckpoint(event.text)
            if (event.session_id) { setSessionId(event.session_id); capturedSessionId = event.session_id }
            break
          case 'done':
            setIsDone(event.is_done ?? false)
            if (event.is_done) setSessionStatus('done')
            break
          case 'error':
            setError(event.message ?? 'Unknown error')
            break
        }
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setStreaming(false)
      loadSessions()
      if (capturedSessionId) loadFiles(capturedSessionId)
      setTimeout(() => continueRef.current?.focus(), 100)
    }
  }

  const handleOpenFile = async (f: ThinkFile) => {
    if (!sessionId) return
    setFileLoading(true)
    try {
      const content = await fetchThinkFile(sessionId, f.filename)
      setOpenFile({ label: fileLabel(f), filename: f.filename, content })
    } catch (e) {
      setError(String(e))
    } finally {
      setFileLoading(false)
    }
  }

  const handleApplyToVault = async () => {
    if (!sessionId || applyStatus === 'loading') return
    setApplyStatus('loading')
    try {
      const result = await applyThinkToVault(sessionId)
      setApplyResult(result)
      setApplyStatus('done')
    } catch (e) {
      setError(String(e))
      setApplyStatus('error')
    }
  }

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!prompt.trim() || streaming) return
    setSessionId(null)
    setCheckpoint(null)
    setStatusLines([])
    setTasks([])
    setFiles([])
    setOpenFile(null)
    setIsDone(false)
    setSessionStatus('running')
    setApplyStatus('idle')
    setApplyResult(null)
    await runStream(startThinking(prompt.trim()))
  }

  const handleContinue = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!sessionId || !continueInput.trim() || streaming) return
    const input = continueInput.trim()
    setContinueInput('')
    setStatusLines([])
    setCheckpoint(null)
    setOpenFile(null)
    await runStream(continueThinking(sessionId, input))
  }

  const handleSelectSession = async (id: string) => {
    try {
      const detail = await fetchThinkSession(id)
      setSessionId(detail.session_id)
      setPrompt(detail.prompt)
      setTasks(detail.tasks)
      setCheckpoint(detail.latest_checkpoint)
      setIsDone(detail.status === 'done')
      setSessionStatus(detail.status)
      setStatusLines([])
      setOpenFile(null)
      setError(null)
      await loadFiles(detail.session_id)
    } catch (e) {
      setError(String(e))
    }
  }

  const handleNew = () => {
    setPrompt('')
    setSessionId(null)
    setCheckpoint(null)
    setStatusLines([])
    setTasks([])
    setFiles([])
    setOpenFile(null)
    setIsDone(false)
    setSessionStatus('running')
    setError(null)
    setApplyStatus('idle')
    setApplyResult(null)
  }

  const hasSession = sessionId !== null || streaming || checkpoint !== null
  const canContinue = sessionId !== null && !isDone && sessionStatus !== 'done'

  return (
    <div className="space-y-8">

      {/* — Start form — */}
      {!hasSession && (
        <form onSubmit={handleStart} className="space-y-3">
          <label className="block text-xs text-gray-500 uppercase tracking-wider">
            What should Elvis think about?
          </label>
          <textarea
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleStart(e as any) }}
            placeholder="think about building a drone at home…"
            rows={3}
            className="w-full bg-gray-900 border border-gray-700 rounded-md px-4 py-3 text-sm text-gray-100 placeholder-gray-600 resize-none focus:outline-none focus:border-gray-500"
          />
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={!prompt.trim() || streaming}
              className="px-4 py-2 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-md transition-colors"
            >
              Think
            </button>
            <span className="text-xs text-gray-600">⌘ Enter</span>
          </div>
        </form>
      )}

      {/* — Session list — */}
      {!hasSession && (
        <SessionList sessions={sessions} onSelect={handleSelectSession} onRefresh={loadSessions} />
      )}

      {/* — Active session header — */}
      {hasSession && (
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Topic</p>
            <MarkdownContent content={prompt} />
            {sessionId && (
              <p className="text-[10px] font-mono text-gray-600 mt-0.5">{sessionId}</p>
            )}
          </div>
          {!streaming && (
            <button onClick={handleNew} className="text-xs text-gray-600 hover:text-gray-400 transition-colors shrink-0">
              ← Back
            </button>
          )}
        </div>
      )}

      {/* — Status feed — */}
      {(statusLines.length > 0 || streaming) && (
        <div className="space-y-1.5">
          {statusLines.map((line, i) => (
            <div key={i} className="flex items-center gap-2 text-xs text-gray-500">
              <span className="text-green-600 shrink-0">✓</span>
              {line}
            </div>
          ))}
          {streaming && (
            <div className="flex items-center gap-2 text-xs text-gray-600 animate-pulse">
              <span className="inline-block w-2 h-2 rounded-full bg-gray-600 animate-bounce" />
              Working…
            </div>
          )}
        </div>
      )}

      {/* — Task tree — */}
      {tasks.length > 0 && (
        <div className="border border-gray-800 rounded-lg p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-3">Task tree</p>
          <TaskList tasks={tasks} />
        </div>
      )}

      {/* — User task prompts — */}
      {tasks.length > 0 && <UserTaskPrompts tasks={tasks} />}

      {/* — Files — */}
      {files.length > 0 && (
        <div className="border border-gray-800 rounded-lg p-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Files</p>
            <button
              onClick={handleApplyToVault}
              disabled={applyStatus === 'loading' || applyStatus === 'done'}
              className="text-[11px] px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-300 transition-colors"
            >
              {applyStatus === 'loading' ? 'Moving…' : applyStatus === 'done' ? '✓ In vault' : 'Move to Vault'}
            </button>
          </div>
          {applyResult && (
            <p className="text-[10px] text-gray-600 font-mono truncate">{applyResult.destination}</p>
          )}
          <div className="space-y-1">
            {files.map(f => (
              <button
                key={f.filename}
                onClick={() => handleOpenFile(f)}
                disabled={fileLoading}
                className="w-full text-left flex items-center justify-between gap-3 px-3 py-2 rounded-md bg-gray-900 hover:bg-gray-800 disabled:opacity-50 transition-colors"
              >
                <span className="text-xs text-gray-300 truncate">{fileLabel(f)}</span>
                <span className="text-[10px] text-gray-600 shrink-0">
                  {f.is_checkpoint ? 'checkpoint' : 'deliverable'} · {Math.round(f.size / 102.4) / 10} kB
                </span>
              </button>
            ))}
          </div>
          {fileLoading && <p className="text-[11px] text-gray-600 animate-pulse">Loading…</p>}
        </div>
      )}

      {/* — Open file viewer — */}
      {openFile && (
        <div className="border border-gray-700 rounded-lg">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800">
            <p className="text-xs text-gray-400">{openFile.label}</p>
            <div className="flex items-center gap-3">
              <CopyButton text={openFile.content} />
              <button
                onClick={() => setOpenFile(null)}
                className="text-[11px] text-gray-600 hover:text-gray-400 transition-colors"
              >
                ✕ close
              </button>
            </div>
          </div>
          <div className="p-5">
            <FileViewer filename={openFile.filename} content={openFile.content} />
          </div>
        </div>
      )}

      {/* — Checkpoint — */}
      {checkpoint && (
        <div className="border border-gray-800 rounded-lg p-5">
          <div className="flex items-center justify-end mb-3">
            <CopyButton text={checkpoint} />
          </div>
          <MarkdownContent content={checkpoint} />
        </div>
      )}

      {/* — Continue input — */}
      {canContinue && !streaming && (
        <form onSubmit={handleContinue} className="space-y-3">
          <label className="block text-xs text-gray-500 uppercase tracking-wider">Reply</label>
          <div className="flex gap-2">
            <input
              ref={continueRef}
              type="text"
              value={continueInput}
              onChange={e => setContinueInput(e.target.value)}
              placeholder="keep going  ·  use a Raspberry Pi  ·  stop"
              className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-gray-500"
            />
            <button
              type="submit"
              disabled={!continueInput.trim()}
              className="px-4 py-2.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-md transition-colors"
            >
              Send
            </button>
          </div>
          <p className="text-[11px] text-gray-600">
            Say <span className="text-gray-500">keep going</span> to continue,
            inject a new constraint, or <span className="text-gray-500">stop</span> to end.
          </p>
        </form>
      )}

      {/* — Done — */}
      {isDone && (
        <div className="border border-gray-800 rounded-lg p-4 text-center space-y-3">
          <p className="text-sm text-gray-400">Session complete. Checkpoints are in Obsidian staging.</p>
          <button onClick={handleNew} className="px-4 py-2 text-xs bg-gray-800 hover:bg-gray-700 rounded-md transition-colors">
            ← Back
          </button>
        </div>
      )}

      {/* — Error — */}
      {error && (
        <p className="text-sm text-red-400 border border-red-900 rounded-md px-4 py-3">{error}</p>
      )}

    </div>
  )
}
