import { useEffect, useRef, useState } from 'react'
import { createChatThread, deleteChatThread, fetchChatHistory, fetchChatThreads, sendChatMessage, listProjects, getProject, finishIntake, runBuildPlan, runAlignTimeline } from '../api'
import { ChatMarkdownContent } from './FileViewer'
import type { BuildPlanEvent, ChatEvent, ChatMessage, ChatThread, IntakeProject } from '../types'

const WELCOME: ChatMessage = {
  role: 'assistant',
  content: 'Hi, I am Elvis, your personal home assistant.\n\nHow can I help?',
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()
  if (ms < 60_000) return 'just now'
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`
  return `${Math.floor(ms / 86_400_000)}d ago`
}

function TrashIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
      <path d="M5.5 1.5A1.5 1.5 0 0 1 7 0h2a1.5 1.5 0 0 1 1.5 1.5H12a1 1 0 0 1 0 2H4a1 1 0 0 1 0-2h1.5ZM4.118 4.5l.69 8.283A1 1 0 0 0 5.8 13.8h4.4a1 1 0 0 0 .994-.917l.69-8.283H4.118Z" />
    </svg>
  )
}

function Sidebar({
  threads,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: {
  threads: ChatThread[]
  activeId: string
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}) {
  return (
    <div className="w-52 shrink-0 flex flex-col border-r border-gray-800 pr-3 mr-5 min-h-0">
      <button
        onClick={onNew}
        className="w-full text-left px-3 py-2 text-xs rounded-md bg-gray-900 hover:bg-gray-800 border border-gray-700 hover:border-gray-600 transition-colors mb-3 shrink-0"
      >
        + New chat
      </button>
      <div className="flex-1 overflow-y-auto space-y-0.5 min-h-0">
        {threads.length === 0 && (
          <p className="text-[11px] text-gray-700 px-3 py-2">No conversations yet</p>
        )}
        {threads.map(t => (
          <div
            key={t.thread_id}
            className={`group relative flex items-center rounded-md transition-colors ${
              t.thread_id === activeId
                ? 'bg-gray-800'
                : 'hover:bg-gray-900'
            }`}
          >
            <button
              onClick={() => onSelect(t.thread_id)}
              className={`flex-1 text-left px-3 py-2.5 min-w-0 ${
                t.thread_id === activeId ? 'text-gray-100' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <p className="text-xs leading-snug truncate pr-5">{t.title}</p>
              <p className="text-[10px] text-gray-600 mt-0.5">{relativeTime(t.updated_at)}</p>
            </button>
            <button
              onClick={e => { e.stopPropagation(); onDelete(t.thread_id) }}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 p-1 rounded text-gray-600 hover:text-red-400 transition-all"
              title="Delete conversation"
            >
              <TrashIcon />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[75%] bg-gray-800 rounded-2xl rounded-tr-sm px-4 py-2.5">
        <ChatMarkdownContent content={content} />
      </div>
    </div>
  )
}

function AssistantBubble({ content, streaming = false }: { content: string; streaming?: boolean }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%]">
        <ChatMarkdownContent content={content} />
        {streaming && (
          <span className="inline-block w-2 h-4 bg-gray-500 animate-pulse ml-0.5 align-text-bottom" />
        )}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1 py-2">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-600 animate-bounce [animation-delay:0ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-gray-600 animate-bounce [animation-delay:150ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-gray-600 animate-bounce [animation-delay:300ms]" />
      </div>
    </div>
  )
}

export default function ChatView() {
  const [threads, setThreads] = useState<ChatThread[]>([])
  const [threadId, setThreadId] = useState<string>('')
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [obsidianOnly, setObsidianOnly] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)

  // Project intake state
  const [intakeMode, setIntakeMode] = useState<'idle' | 'naming' | 'dumping'>('idle')
  const [projectName, setProjectName] = useState('')
  const [dumpMessages, setDumpMessages] = useState<string[]>([])
  const [intakeLoading, setIntakeLoading] = useState(false)
  const [intakeContext, setIntakeContext] = useState<{ projectName: string; concerns: string[] } | null>(null)

  // Continue project state
  const [projects, setProjects] = useState<IntakeProject[]>([])
  const [projectPickerOpen, setProjectPickerOpen] = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)

  // Active project (set after new project intake or continuing a project)
  const [activeProject, setActiveProject] = useState<string | null>(null)
  const [buildPlanRunning, setBuildPlanRunning] = useState(false)
  const [alignRunning, setAlignRunning] = useState(false)

  const loadThreads = async () => {
    try {
      const ts = await fetchChatThreads()
      setThreads(ts)
      return ts
    } catch {
      return []
    }
  }

  const loadHistory = async (tid: string) => {
    setMessages([WELCOME])
    try {
      const hist = await fetchChatHistory(tid)
      setMessages(hist.length > 0 ? hist : [WELCOME])
    } catch {
      setMessages([WELCOME])
    }
  }

  // On mount: load threads, select the most recent one
  useEffect(() => {
    loadThreads().then(ts => {
      if (ts.length > 0) {
        setThreadId(ts[0].thread_id)
      }
    })
  }, [])

  // Load history when active thread changes
  useEffect(() => {
    if (threadId) loadHistory(threadId)
  }, [threadId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  // Focus name input when entering naming mode
  useEffect(() => {
    if (intakeMode === 'naming') {
      setTimeout(() => nameInputRef.current?.focus(), 50)
    }
  }, [intakeMode])

  // Close picker on outside click
  useEffect(() => {
    if (!projectPickerOpen) return
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setProjectPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [projectPickerOpen])

  const handleSelectThread = (id: string) => {
    if (id === threadId || streaming) return
    setError(null)
    setIntakeMode('idle')
    setDumpMessages([])
    setIntakeContext(null)
    setActiveProject(null)
    setThreadId(id)
  }

  const handleDelete = async (id: string) => {
    if (streaming) return
    try {
      await deleteChatThread(id)
      const updated = threads.filter(t => t.thread_id !== id)
      setThreads(updated)
      if (id === threadId) {
        const next = updated[0]?.thread_id ?? ''
        setThreadId(next)
        if (!next) setMessages([WELCOME])
      }
    } catch (e) {
      setError(String(e))
    }
  }

  const handleNew = async () => {
    if (streaming) return
    setIntakeMode('idle')
    setDumpMessages([])
    try {
      const t = await createChatThread()
      setThreads(prev => [t, ...prev])
      setThreadId(t.thread_id)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const sendMessage = async (text: string, displayText?: string, explicitThreadId?: string) => {
    const tid = explicitThreadId ?? threadId
    if (!text.trim() || streaming || !tid) return
    setError(null)
    setMessages(prev => [...prev, { role: 'user', content: displayText ?? text }])
    setStreaming(true)
    setStreamingText('')
    let full = ''
    try {
      for await (const event of sendChatMessage(text, tid) as AsyncGenerator<ChatEvent>) {
        if (event.type === 'chunk' && event.text) {
          full += event.text
          setStreamingText(full)
        } else if (event.type === 'error') {
          setError(event.message ?? 'Unknown error')
          break
        }
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setStreaming(false)
      if (full) setMessages(prev => [...prev, { role: 'assistant', content: full }])
      setStreamingText('')
      loadThreads()
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || !threadId) return

    if (intakeMode === 'dumping') {
      // Accumulate locally — do not send to Elvis
      setMessages(prev => [...prev, { role: 'user', content: input.trim() }])
      setDumpMessages(prev => [...prev, input.trim()])
      setInput('')
      return
    }

    if (streaming) return
    const raw = input.trim()
    setInput('')

    if (intakeContext) {
      const ctx =
        `[Project intake clarification for "${intakeContext.projectName}". ` +
        `Requirements.md has been written. Concerns raised: ${intakeContext.concerns.join('; ')}]\n\n`
      setIntakeContext(null)
      await sendMessage(ctx + raw, raw)
      return
    }

    const text = obsidianOnly ? raw + '\n\nSearch only in the Obsidian vault.' : raw
    await sendMessage(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend(e as unknown as React.FormEvent)
    }
  }

  // --- New Project flow ---

  const handleNewProject = async () => {
    if (streaming) return
    // Ensure we have a thread
    if (!threadId) {
      try {
        const t = await createChatThread()
        setThreads(prev => [t, ...prev])
        setThreadId(t.thread_id)
      } catch (e) {
        setError(String(e))
        return
      }
    }
    setProjectName('')
    setDumpMessages([])
    setIntakeMode('naming')
  }

  const handleNameConfirm = (e: React.FormEvent) => {
    e.preventDefault()
    if (!projectName.trim()) return
    setIntakeMode('dumping')
    setMessages([WELCOME, {
      role: 'assistant',
      content: `Starting project **${projectName.trim()}**. Dump your ideas — anything goes. Click **Done** when finished.`,
    }])
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  const handleDone = async () => {
    if (intakeLoading || dumpMessages.length === 0) return
    setIntakeLoading(true)
    setIntakeMode('idle')
    setStreaming(true)
    setStreamingText('')
    try {
      const result = await finishIntake(projectName.trim(), dumpMessages)
      const concernText = result.concerns.length > 0
        ? `I need clarification on:\n${result.concerns.map(c => `- ${c}`).join('\n')}`
        : `Requirements written to \`${result.note_path}\`. Let me know if you need any changes.`
      setMessages(prev => [...prev, { role: 'assistant', content: concernText }])
      setActiveProject(projectName.trim())
      if (result.concerns.length > 0) {
        setIntakeContext({ projectName: projectName.trim(), concerns: result.concerns })
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setIntakeLoading(false)
      setStreaming(false)
      setDumpMessages([])
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  // --- Continue Project flow ---

  const handleContinueProject = async () => {
    if (streaming) return
    try {
      const ps = await listProjects()
      setProjects(ps)
      setProjectPickerOpen(true)
    } catch (e) {
      setError(String(e))
    }
  }

  const handleProjectSelect = async (name: string) => {
    setProjectPickerOpen(false)
    try {
      const [detail, t] = await Promise.all([getProject(name), createChatThread(name)])
      setThreads(prev => [t, ...prev])
      setIntakeContext(null)
      setActiveProject(name)
      setMessages([WELCOME])
      setThreadId(t.thread_id)

      const fileList = detail.paths.map(p => `- ${p}`).join('\n')
      const contextMsg =
        `[Project context — continuing project "${name}"]\n\nFiles in vault:\n${fileList}\n\n` +
        `Briefly acknowledge the project and ask what Pun wants to work on.`
      await sendMessage(contextMsg, `Continue project: **${name}**`, t.thread_id)
    } catch (e) {
      setError(String(e))
    }
  }

  const handleRunBuildPlan = async () => {
    if (!activeProject || buildPlanRunning) return
    setBuildPlanRunning(true)
    let log = ''
    setMessages(prev => [...prev, { role: 'assistant', content: `Running build plan for **${activeProject}**…` }])
    try {
      for await (const event of runBuildPlan(activeProject) as AsyncGenerator<BuildPlanEvent>) {
        if (event.type === 'log') {
          log += event.message + '\n'
          setMessages(prev => {
            const next = [...prev]
            next[next.length - 1] = { role: 'assistant', content: `Running build plan for **${activeProject}**…\n\`\`\`\n${log}\`\`\`` }
            return next
          })
        } else if (event.type === 'done') {
          setMessages(prev => {
            const next = [...prev]
            next[next.length - 1] = { role: 'assistant', content: `Build plan written to \`${event.note_path}\`` }
            return next
          })
        } else if (event.type === 'error') {
          setError(event.message)
          setMessages(prev => prev.slice(0, -1))
        }
      }
    } catch (e) {
      setError(String(e))
      setMessages(prev => prev.slice(0, -1))
    } finally {
      setBuildPlanRunning(false)
    }
  }

  const handleAlignTimeline = async () => {
    if (!activeProject || alignRunning) return
    setAlignRunning(true)
    let log = ''
    setMessages(prev => [...prev, { role: 'assistant', content: `Aligning timeline to calendar for **${activeProject}**…` }])
    try {
      for await (const event of runAlignTimeline(activeProject) as AsyncGenerator<BuildPlanEvent>) {
        if (event.type === 'log') {
          log += event.message + '\n'
          setMessages(prev => {
            const next = [...prev]
            next[next.length - 1] = { role: 'assistant', content: `Aligning timeline to calendar for **${activeProject}**…\n\`\`\`\n${log}\`\`\`` }
            return next
          })
        } else if (event.type === 'done') {
          setMessages(prev => {
            const next = [...prev]
            next[next.length - 1] = { role: 'assistant', content: `Aligned timeline written to \`${event.note_path}\`` }
            return next
          })
        } else if (event.type === 'error') {
          setError(event.message)
          setMessages(prev => prev.slice(0, -1))
        }
      }
    } catch (e) {
      setError(String(e))
      setMessages(prev => prev.slice(0, -1))
    } finally {
      setAlignRunning(false)
    }
  }

  const handleBriefing = () => sendMessage(
    "Give me my daily briefing. Use Markdown: ## for each section heading (Today's Tasks, Upcoming Events, Carried Over), " +
    '- bullet lists for items within each section, and --- between sections. ' +
    'Skip any empty checkbox items (bare [ ] with no text). Only include sections that have real content. ' +
    'Do not add any closing question or offer to help.'
  )

  const isDumping = intakeMode === 'dumping'

  return (
    <div className="flex flex-row" style={{ height: 'calc(100vh - 9rem)' }}>

      <Sidebar
        threads={threads}
        activeId={threadId}
        onSelect={handleSelectThread}
        onNew={handleNew}
        onDelete={handleDelete}
      />

      {/* Chat pane */}
      <div className="flex flex-col flex-1 min-w-0">

        {/* Messages */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden space-y-4 pb-2 min-h-0">
          {!threadId ? (
            <p className="text-xs text-gray-600 py-8 text-center">Start a new chat or select one</p>
          ) : (
            <>
              {messages.map((msg, i) =>
                msg.role === 'user'
                  ? <UserBubble key={i} content={msg.content} />
                  : <AssistantBubble key={i} content={msg.content} />
              )}
              {streaming && streamingText
                ? <AssistantBubble content={streamingText} streaming />
                : streaming
                ? <TypingIndicator />
                : null
              }
              {error && (
                <p className="text-xs text-red-400 border border-red-900 rounded-md px-3 py-2">{error}</p>
              )}
            </>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Quick actions / intake controls */}
        <div className="flex gap-2 pb-3 shrink-0 flex-wrap items-center relative">
          {intakeMode === 'naming' ? (
            /* Inline name input */
            <form onSubmit={handleNameConfirm} className="flex gap-2 items-center w-full">
              <input
                ref={nameInputRef}
                value={projectName}
                onChange={e => setProjectName(e.target.value)}
                placeholder="Project name…"
                className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-3 py-1.5 text-xs text-gray-100 placeholder-gray-600 focus:outline-none focus:border-gray-500"
              />
              <button
                type="submit"
                disabled={!projectName.trim()}
                className="px-3 py-1.5 text-[11px] rounded-md bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Start Dump
              </button>
              <button
                type="button"
                onClick={() => setIntakeMode('idle')}
                className="px-3 py-1.5 text-[11px] rounded-md text-gray-500 hover:text-gray-300 transition-colors"
              >
                Cancel
              </button>
            </form>
          ) : isDumping ? (
            /* Dump mode: Done button */
            <>
              <span className="text-[11px] text-violet-400">
                Dumping into <strong>{projectName}</strong> — {dumpMessages.length} message{dumpMessages.length !== 1 ? 's' : ''}
              </span>
              <button
                onClick={handleDone}
                disabled={intakeLoading || dumpMessages.length === 0}
                className="px-3 py-1 text-[11px] rounded-full border border-violet-600 bg-violet-900/40 text-violet-300 hover:bg-violet-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ml-auto"
              >
                {intakeLoading ? 'Analysing…' : 'Done'}
              </button>
            </>
          ) : (
            /* Normal mode */
            <>
              <button
                onClick={handleBriefing}
                disabled={streaming || !threadId}
                className="px-3 py-1 text-[11px] rounded-full border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Daily Briefing
              </button>
              <button
                onClick={() => setObsidianOnly(v => !v)}
                disabled={!threadId}
                className={`px-3 py-1 text-[11px] rounded-full border transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${
                  obsidianOnly
                    ? 'border-violet-600 bg-violet-900/40 text-violet-300'
                    : 'border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500'
                }`}
              >
                Obsidian Only
              </button>
              <button
                onClick={handleNewProject}
                disabled={streaming}
                className="px-3 py-1 text-[11px] rounded-full border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                New Project
              </button>
              <div className="relative" ref={pickerRef}>
                <button
                  onClick={handleContinueProject}
                  disabled={streaming}
                  className="px-3 py-1 text-[11px] rounded-full border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  Continue Project
                </button>
                {projectPickerOpen && (
                  <div className="absolute bottom-full mb-1 left-0 bg-gray-900 border border-gray-700 rounded-md shadow-lg z-10 min-w-[180px]">
                    {projects.length === 0 ? (
                      <p className="text-[11px] text-gray-500 px-3 py-2">No projects found</p>
                    ) : (
                      projects.map(p => (
                        <button
                          key={p.name}
                          onClick={() => handleProjectSelect(p.name)}
                          className="w-full text-left px-3 py-2 text-xs text-gray-300 hover:bg-gray-800 transition-colors first:rounded-t-md last:rounded-b-md"
                        >
                          {p.name}
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
              {activeProject && (
                <>
                  <button
                    onClick={handleRunBuildPlan}
                    disabled={buildPlanRunning}
                    className="px-3 py-1 text-[11px] rounded-full border border-emerald-700 text-emerald-400 hover:bg-emerald-900/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    {buildPlanRunning ? 'Running…' : `Run Build Plan`}
                  </button>
                  <button
                    onClick={handleAlignTimeline}
                    disabled={alignRunning}
                    className="px-3 py-1 text-[11px] rounded-full border border-sky-700 text-sky-400 hover:bg-sky-900/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    {alignRunning ? 'Aligning…' : 'Align to Calendar'}
                  </button>
                </>
              )}
            </>
          )}
        </div>

        {/* Input */}
        <form onSubmit={handleSend} className="border-t border-gray-800 pt-4 shrink-0">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                isDumping
                  ? 'Dump your ideas… (click Done when finished)'
                  : threadId
                  ? 'Message Elvis… (Enter to send, Shift+Enter for newline)'
                  : 'Create a new chat to start'
              }
              disabled={(streaming && !isDumping) || !threadId}
              rows={1}
              className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-gray-500 disabled:opacity-50 resize-none leading-relaxed"
              style={{ minHeight: '42px', maxHeight: '160px', overflowY: 'auto' }}
              onInput={e => {
                const t = e.currentTarget
                t.style.height = 'auto'
                t.style.height = Math.min(t.scrollHeight, 160) + 'px'
              }}
            />
            <button
              type="submit"
              disabled={!input.trim() || (streaming && !isDumping) || !threadId}
              className="px-4 py-2.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-md transition-colors shrink-0"
            >
              {isDumping ? 'Add' : 'Send'}
            </button>
          </div>
          <p className="text-[11px] text-gray-700 mt-1.5">Enter to send · Shift+Enter for newline</p>
        </form>

      </div>
    </div>
  )
}
