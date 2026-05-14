import type { Run, RunSummary, CadStatusEvent, CadOutput, ThinkEvent, ThinkSessionSummary, ThinkSessionDetail, ThinkFile, ChatThread, ChatMessage, ChatEvent, WeeklySummary, BrainSurfacedRow, BrainStats, BrainTrendPoint, BrainNote, BrainEngagementRunResult, BrainSurfaceEvent } from './types'

const BASE = '/api'

export async function startRun(tasks: string[]): Promise<string> {
  const res = await fetch(`${BASE}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tasks }),
  })
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()).run_id as string
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const res = await fetch(`${BASE}/runs`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchRun(runId: string): Promise<Run> {
  const res = await fetch(`${BASE}/runs/${runId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchTaskFile(runId: string, filename: string): Promise<string> {
  const res = await fetch(`${BASE}/runs/${runId}/tasks/${filename}`)
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()).content as string
}

export function openStream(runId: string): EventSource {
  return new EventSource(`${BASE}/runs/${runId}/stream`)
}

export async function* generateCad(prompt: string): AsyncGenerator<CadStatusEvent> {
  const res = await fetch(`${BASE}/cad/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  })
  if (!res.ok) throw new Error(await res.text())

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop()!
      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith('data: ')) {
          yield JSON.parse(trimmed.slice(6)) as CadStatusEvent
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function fetchCadHistory(): Promise<CadOutput[]> {
  const res = await fetch(`${BASE}/cad/history`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchCadScript(basename: string): Promise<string> {
  const res = await fetch(`${BASE}/cad/script/${basename}`)
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()).content as string
}

async function* _sseStream<T>(res: Response): AsyncGenerator<T> {
  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop()!
      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith('data: ')) {
          yield JSON.parse(trimmed.slice(6)) as T
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function* startThinking(prompt: string): AsyncGenerator<ThinkEvent> {
  const res = await fetch(`${BASE}/think`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  })
  if (!res.ok) throw new Error(await res.text())
  yield* _sseStream<ThinkEvent>(res)
}

export async function fetchThinkSessions(): Promise<ThinkSessionSummary[]> {
  const res = await fetch(`${BASE}/think/sessions`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchThinkSession(sessionId: string): Promise<ThinkSessionDetail> {
  const res = await fetch(`${BASE}/think/${sessionId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchThinkFiles(sessionId: string): Promise<ThinkFile[]> {
  const res = await fetch(`${BASE}/think/${sessionId}/files`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchThinkFile(sessionId: string, filename: string): Promise<string> {
  const res = await fetch(`${BASE}/think/${sessionId}/files/${encodeURIComponent(filename)}`)
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()).content as string
}

export async function fetchChatThreads(): Promise<ChatThread[]> {
  const res = await fetch(`${BASE}/chat/threads`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function createChatThread(): Promise<ChatThread> {
  const res = await fetch(`${BASE}/chat/threads`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchChatHistory(threadId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${BASE}/chat/history?thread_id=${encodeURIComponent(threadId)}`)
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()).messages as ChatMessage[]
}

export async function* sendChatMessage(message: string, threadId: string): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id: threadId }),
  })
  if (!res.ok) throw new Error(await res.text())
  yield* _sseStream<ChatEvent>(res)
}

export async function applyThinkToVault(sessionId: string): Promise<{ applied: string[]; destination: string }> {
  const res = await fetch(`${BASE}/think/${sessionId}/apply`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function deleteChatThread(threadId: string): Promise<void> {
  const res = await fetch(`${BASE}/chat/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
}

export async function fetchWeeklySummaries(): Promise<WeeklySummary[]> {
  const res = await fetch(`${BASE}/weekly`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function* continueThinking(sessionId: string, input: string): AsyncGenerator<ThinkEvent> {
  const res = await fetch(`${BASE}/think/${sessionId}/continue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input }),
  })
  if (!res.ok) throw new Error(await res.text())
  yield* _sseStream<ThinkEvent>(res)
}

// Brain
export async function fetchBrainSurfaced(limit = 200): Promise<BrainSurfacedRow[]> {
  const res = await fetch(`${BASE}/brain/surfaced?limit=${limit}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchBrainStats(): Promise<BrainStats> {
  const res = await fetch(`${BASE}/brain/stats`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchBrainTrend(days = 30): Promise<BrainTrendPoint[]> {
  const res = await fetch(`${BASE}/brain/trend?days=${days}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchBrainNote(path: string): Promise<BrainNote> {
  const res = await fetch(`${BASE}/brain/note?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function runEngagementCheck(): Promise<BrainEngagementRunResult> {
  const res = await fetch(`${BASE}/brain/run/engagement`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function* runSurfacing(): AsyncGenerator<BrainSurfaceEvent> {
  const res = await fetch(`${BASE}/brain/run/surface`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  yield* _sseStream<BrainSurfaceEvent>(res)
}
