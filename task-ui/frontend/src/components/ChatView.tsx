import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { createChatThread, fetchChatHistory, fetchChatThreads, sendChatMessage } from '../api'
import type { ChatEvent, ChatMessage, ChatThread } from '../types'

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

function Sidebar({
  threads,
  activeId,
  onSelect,
  onNew,
}: {
  threads: ChatThread[]
  activeId: string
  onSelect: (id: string) => void
  onNew: () => void
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
          <button
            key={t.thread_id}
            onClick={() => onSelect(t.thread_id)}
            className={`w-full text-left px-3 py-2.5 rounded-md transition-colors ${
              t.thread_id === activeId
                ? 'bg-gray-800 text-gray-100'
                : 'text-gray-500 hover:text-gray-300 hover:bg-gray-900'
            }`}
          >
            <p className="text-xs leading-snug truncate">{t.title}</p>
            <p className="text-[10px] text-gray-600 mt-0.5">{relativeTime(t.updated_at)}</p>
          </button>
        ))}
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[75%] bg-gray-800 rounded-2xl rounded-tr-sm px-4 py-2.5">
        <div className="prose prose-invert prose-sm max-w-none
          prose-p:text-gray-100 prose-p:leading-relaxed prose-p:my-0.5
          prose-strong:text-white
          prose-ul:my-1 prose-ol:my-1 prose-li:text-gray-100 prose-li:my-0
          prose-code:text-gray-200 prose-code:bg-gray-700 prose-code:px-1 prose-code:rounded prose-code:text-xs
          prose-pre:bg-gray-700 prose-pre:border prose-pre:border-gray-600 prose-pre:rounded-lg
        ">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

function AssistantBubble({ content, streaming = false }: { content: string; streaming?: boolean }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%]">
        <div className="prose prose-invert prose-sm max-w-none
          prose-headings:text-gray-200 prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1
          prose-p:text-gray-300 prose-p:leading-relaxed prose-p:my-1
          prose-strong:text-gray-200
          prose-ul:my-1 prose-ol:my-1
          prose-li:text-gray-300 prose-li:my-0
          prose-hr:border-gray-800
          prose-table:text-sm prose-th:text-gray-300 prose-td:text-gray-400 prose-td:border-gray-800
          prose-code:text-gray-300 prose-code:bg-gray-900 prose-code:px-1 prose-code:rounded prose-code:text-xs
          prose-pre:bg-gray-900 prose-pre:border prose-pre:border-gray-800 prose-pre:rounded-lg
        ">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
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
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

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

  const handleSelectThread = (id: string) => {
    if (id === threadId || streaming) return
    setError(null)
    setThreadId(id)
  }

  const handleNew = async () => {
    if (streaming) return
    try {
      const t = await createChatThread()
      setThreads(prev => [t, ...prev])
      setThreadId(t.thread_id)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const sendMessage = async (text: string) => {
    if (!text.trim() || streaming || !threadId) return
    setError(null)
    setMessages(prev => [...prev, { role: 'user', content: text }])
    setStreaming(true)
    setStreamingText('')
    let full = ''
    try {
      for await (const event of sendChatMessage(text, threadId) as AsyncGenerator<ChatEvent>) {
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
    if (!input.trim() || streaming || !threadId) return
    const text = input.trim()
    setInput('')
    await sendMessage(text)
  }

  const handleBriefing = () => sendMessage(
    "Give me my daily briefing. Use Markdown: ## for each section heading (Today's Tasks, Upcoming Events, Carried Over), " +
    '- bullet lists for items within each section, and --- between sections. ' +
    'Skip any empty checkbox items (bare [ ] with no text). Only include sections that have real content. ' +
    'Do not add any closing question or offer to help.'
  )

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend(e as unknown as React.FormEvent)
    }
  }

  return (
    <div className="flex flex-row" style={{ height: 'calc(100vh - 9rem)' }}>

      <Sidebar
        threads={threads}
        activeId={threadId}
        onSelect={handleSelectThread}
        onNew={handleNew}
      />

      {/* Chat pane */}
      <div className="flex flex-col flex-1 min-w-0">

        {/* Messages */}
        <div className="flex-1 overflow-y-auto space-y-4 pb-2 min-h-0">
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

        {/* Quick actions */}
        <div className="flex gap-2 pb-3 shrink-0">
          <button
            onClick={handleBriefing}
            disabled={streaming || !threadId}
            className="px-3 py-1 text-[11px] rounded-full border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Daily Briefing
          </button>
        </div>

        {/* Input */}
        <form onSubmit={handleSend} className="border-t border-gray-800 pt-4 shrink-0">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={threadId ? 'Message Elvis… (Enter to send, Shift+Enter for newline)' : 'Create a new chat to start'}
              disabled={streaming || !threadId}
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
              disabled={!input.trim() || streaming || !threadId}
              className="px-4 py-2.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-md transition-colors shrink-0"
            >
              Send
            </button>
          </div>
          <p className="text-[11px] text-gray-700 mt-1.5">Enter to send · Shift+Enter for newline</p>
        </form>

      </div>
    </div>
  )
}
