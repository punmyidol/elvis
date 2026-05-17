import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { listNotes, saveNote, fetchBrainNote, sendNotesChatMessage } from '../api'
import { MarkdownContent } from './FileViewer'
import type { SurfacedNote } from '../types'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

function makeApplyComponents(onApply: (text: string) => void): React.ComponentProps<typeof ReactMarkdown>['components'] {
  return {
    h1: ({ children }) => <h1 className="text-xl font-bold text-gray-100 mt-4 mb-2">{children}</h1>,
    h2: ({ children }) => <h2 className="text-lg font-semibold text-gray-100 mt-4 mb-2">{children}</h2>,
    h3: ({ children }) => <h3 className="text-base font-semibold text-gray-200 mt-3 mb-1.5">{children}</h3>,
    h4: ({ children }) => <h4 className="text-sm font-semibold text-gray-200 mt-2 mb-1">{children}</h4>,
    p: ({ children }) => <p className="text-gray-300 leading-relaxed mb-2 last:mb-0">{children}</p>,
    ul: ({ children }) => <ul className="list-disc list-outside pl-5 text-gray-300 space-y-1 mb-2">{children}</ul>,
    ol: ({ children }) => <ol className="list-decimal list-outside pl-5 text-gray-300 space-y-1 mb-2">{children}</ol>,
    li: ({ children }) => <li className="text-gray-300 leading-relaxed">{children}</li>,
    strong: ({ children }) => <strong className="text-gray-100 font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic text-gray-300">{children}</em>,
    code: ({ children }) => <code className="text-green-400 bg-gray-900 px-1 py-0.5 rounded text-xs font-mono">{children}</code>,
    pre({ children, node }: any) {
      const codeNode = node?.children?.[0]
      const className: string = codeNode?.properties?.className?.[0] ?? ''
      const lang = /language-(\w+)/.exec(className)?.[1]
      const text: string = codeNode?.children?.[0]?.value ?? ''
      return (
        <div className="mb-2">
          <pre className="bg-gray-900 border border-gray-700 rounded-md p-3 overflow-x-auto text-xs leading-relaxed">
            {children}
          </pre>
          {lang === 'markdown' && (
            <button
              onClick={() => onApply(text)}
              className="mt-1 text-xs bg-green-900/40 text-green-400 border border-green-800 px-2 py-1 rounded hover:bg-green-800/40 transition-colors"
            >
              Apply to editor
            </button>
          )}
        </div>
      )
    },
    blockquote: ({ children }) => <blockquote className="border-l-2 border-gray-600 pl-3 text-gray-400 italic mb-2">{children}</blockquote>,
    hr: () => <hr className="border-gray-700 my-4" />,
    a: ({ href, children }) => <a href={href} className="text-blue-400 hover:underline" target="_blank" rel="noopener noreferrer">{children}</a>,
  }
}

export default function NotesView() {
  const [notes, setNotes] = useState<SurfacedNote[]>([])
  const [selectedNote, setSelectedNote] = useState<SurfacedNote | null>(null)
  const [editorContent, setEditorContent] = useState('')
  const [editorMode, setEditorMode] = useState<'edit' | 'preview'>('edit')
  const [isDirty, setIsDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const [loadingNote, setLoadingNote] = useState(false)

  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([])
  const [chatInput, setChatInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')

  const chatBottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    listNotes().then(setNotes).catch(console.error)
  }, [])

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages, streamingText])

  const handleSelectNote = async (note: SurfacedNote) => {
    if (isDirty && !window.confirm('Discard unsaved changes?')) return
    setSelectedNote(note)
    setEditorMode('edit')
    setIsDirty(false)
    setLoadingNote(true)
    setChatMessages([])
    setStreamingText('')
    try {
      const { content } = await fetchBrainNote(note.path)
      setEditorContent(content)
    } catch (e) {
      console.error(e)
    } finally {
      setLoadingNote(false)
    }
  }

  const handleSave = async () => {
    if (!selectedNote) return
    setSaving(true)
    setSaveMsg('')
    try {
      await saveNote(selectedNote.path, editorContent)
      setIsDirty(false)
      setSaveMsg('Saved')
      setTimeout(() => setSaveMsg(''), 2500)
    } catch {
      setSaveMsg('Error saving')
    } finally {
      setSaving(false)
    }
  }

  const handleApply = useCallback((content: string) => {
    setEditorContent(content)
    setIsDirty(true)
    setEditorMode('edit')
    textareaRef.current?.focus()
  }, [])

  const applyComponents = useMemo(() => makeApplyComponents(handleApply), [handleApply])

  const handleChatSend = async () => {
    const msg = chatInput.trim()
    if (!msg || isStreaming || !selectedNote) return
    setChatInput('')
    setChatMessages(prev => [...prev, { role: 'user', content: msg }])
    setIsStreaming(true)
    setStreamingText('')

    let accumulated = ''
    try {
      for await (const event of sendNotesChatMessage(msg, selectedNote.path, editorContent, chatMessages)) {
        if (event.type === 'chunk' && event.text) {
          accumulated += event.text
          setStreamingText(accumulated)
        } else if (event.type === 'done' || event.type === 'error') {
          break
        }
      }
    } catch (e) {
      accumulated = `Error: ${String(e)}`
    } finally {
      setChatMessages(prev => [...prev, { role: 'assistant', content: accumulated }])
      setStreamingText('')
      setIsStreaming(false)
    }
  }

  return (
    <div className="flex gap-3 h-[calc(100vh-5.5rem)]">
      {/* Left pane: note list */}
      <div className="w-52 flex-shrink-0 flex flex-col border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-gray-800 flex-shrink-0">
          <span className="text-xs text-gray-500 font-medium">elvis-surfaced/</span>
        </div>
        <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
          {notes.length === 0 ? (
            <p className="text-xs text-gray-600 p-2">No notes found</p>
          ) : (
            notes.map(note => (
              <button
                key={note.path}
                onClick={() => handleSelectNote(note)}
                className={`w-full text-left px-2.5 py-2 rounded transition-colors ${
                  selectedNote?.path === note.path
                    ? 'bg-gray-700 text-white'
                    : 'text-gray-400 hover:bg-gray-800/60 hover:text-gray-200'
                }`}
              >
                <div className="text-xs font-medium truncate leading-snug">{note.title}</div>
                <div className="text-xs text-gray-600 mt-0.5">{note.created}</div>
                {note.source_signals.length > 0 && (
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {note.source_signals.map(s => (
                      <span key={s} className="text-[10px] bg-gray-800 text-gray-500 px-1 rounded">
                        {s}
                      </span>
                    ))}
                  </div>
                )}
              </button>
            ))
          )}
        </div>
      </div>

      {/* Middle pane: editor */}
      <div className="flex-1 flex flex-col min-w-0 border border-gray-800 rounded-lg overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800 flex-shrink-0">
          <span className="text-xs text-gray-400 truncate flex-1 min-w-0">
            {selectedNote ? selectedNote.title : 'Select a note'}
            {isDirty && <span className="ml-1 text-yellow-500" title="Unsaved changes">•</span>}
          </span>
          <div className="flex gap-1 flex-shrink-0">
            <button
              onClick={() => setEditorMode('edit')}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                editorMode === 'edit' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              Edit
            </button>
            <button
              onClick={() => setEditorMode('preview')}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                editorMode === 'preview' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              Preview
            </button>
          </div>
          {saveMsg && (
            <span className={`text-xs flex-shrink-0 ${saveMsg.startsWith('Error') ? 'text-red-400' : 'text-green-400'}`}>
              {saveMsg}
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={!selectedNote || !isDirty || saving}
            className="px-3 py-1 text-xs bg-gray-700 text-white rounded hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0 transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>

        <div className="flex-1 overflow-hidden">
          {!selectedNote ? (
            <div className="h-full flex items-center justify-center text-gray-600 text-sm">
              Select a note from the list
            </div>
          ) : loadingNote ? (
            <div className="h-full flex items-center justify-center text-gray-600 text-sm">
              Loading…
            </div>
          ) : editorMode === 'edit' ? (
            <textarea
              ref={textareaRef}
              value={editorContent}
              onChange={e => { setEditorContent(e.target.value); setIsDirty(true) }}
              className="w-full h-full bg-gray-950 text-gray-200 font-mono text-xs p-4 resize-none focus:outline-none leading-relaxed"
              spellCheck={false}
            />
          ) : (
            <div className="h-full overflow-y-auto p-4">
              <MarkdownContent content={editorContent} />
            </div>
          )}
        </div>
      </div>

      {/* Right pane: chat */}
      <div className="w-80 flex-shrink-0 flex flex-col border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-2 border-b border-gray-800 flex-shrink-0">
          <span className="text-xs text-gray-500 font-medium">Chat with Elvis</span>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {chatMessages.length === 0 && !isStreaming && (
            <p className="text-xs text-gray-600 pt-2">
              {selectedNote
                ? 'Ask Elvis to edit or extend this note'
                : 'Select a note to start chatting'}
            </p>
          )}
          {chatMessages.map((msg, i) => (
            <div key={i} className={msg.role === 'user' ? 'flex justify-end' : ''}>
              {msg.role === 'user' ? (
                <div className="bg-gray-800 text-gray-200 text-xs px-3 py-2 rounded-lg max-w-[92%] break-words">
                  {msg.content}
                </div>
              ) : (
                <div className="text-xs min-w-0">
                  <ReactMarkdown components={applyComponents} remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          ))}
          {isStreaming && streamingText && (
            <div className="text-xs min-w-0">
              <ReactMarkdown components={applyComponents} remarkPlugins={[remarkGfm]}>
                {streamingText}
              </ReactMarkdown>
            </div>
          )}
          {isStreaming && !streamingText && (
            <div className="flex gap-1 py-2 px-1">
              {[0, 150, 300].map(delay => (
                <div
                  key={delay}
                  className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-bounce"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </div>
          )}
          <div ref={chatBottomRef} />
        </div>

        <div className="border-t border-gray-800 p-3 flex-shrink-0">
          <div className="flex gap-2 items-end">
            <textarea
              value={chatInput}
              onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleChatSend()
                }
              }}
              placeholder={selectedNote ? 'Ask Elvis… (Enter to send)' : 'Select a note first'}
              disabled={!selectedNote || isStreaming}
              className="flex-1 bg-gray-900 border border-gray-700 rounded text-xs text-gray-200 p-2 resize-none focus:outline-none focus:border-gray-500 disabled:opacity-50 leading-relaxed"
              rows={2}
            />
            <button
              onClick={handleChatSend}
              disabled={!selectedNote || isStreaming || !chatInput.trim()}
              className="px-3 py-2 text-xs bg-gray-700 text-white rounded hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors self-end"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
