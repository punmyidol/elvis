import type { Run, RunSummary, CadStatusEvent, CadOutput, ChatThread, ChatMessage, ChatEvent, WeeklySummary, BrainSurfacedRow, BrainStats, BrainTrendPoint, BrainNote, BrainEngagementRunResult, BrainSurfaceEvent, SurfacedNote, NotesChatEvent, IntakeProject, IntakeFinishResponse, IntakeProjectDetail, BuildPlanEvent } from './types'

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

export async function fetchChatThreads(): Promise<ChatThread[]> {
  const res = await fetch(`${BASE}/chat/threads`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function createChatThread(projectName?: string): Promise<ChatThread> {
  const res = await fetch(`${BASE}/chat/threads`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(projectName ? { project_name: projectName } : {}),
  })
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

export async function deleteChatThread(threadId: string): Promise<void> {
  const res = await fetch(`${BASE}/chat/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
}

export async function fetchWeeklySummaries(): Promise<WeeklySummary[]> {
  const res = await fetch(`${BASE}/weekly`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
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

// Notes
export async function listNotes(): Promise<SurfacedNote[]> {
  const res = await fetch(`${BASE}/notes/list`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveNote(path: string, content: string): Promise<void> {
  const res = await fetch(`${BASE}/notes/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function* sendNotesChatMessage(
  message: string,
  notePath: string,
  noteContent: string,
  history: Array<{ role: string; content: string }>,
): AsyncGenerator<NotesChatEvent> {
  const res = await fetch(`${BASE}/notes/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, note_path: notePath, note_content: noteContent, history }),
  })
  if (!res.ok) throw new Error(await res.text())
  yield* _sseStream<NotesChatEvent>(res)
}

export async function listProjects(): Promise<IntakeProject[]> {
  const res = await fetch(`${BASE}/intake/projects`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getProject(name: string): Promise<IntakeProjectDetail> {
  const res = await fetch(`${BASE}/intake/project/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function* runBuildPlan(projectName: string): AsyncGenerator<BuildPlanEvent> {
  const res = await fetch(`${BASE}/intake/run-build-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_name: projectName }),
  })
  if (!res.ok) throw new Error(await res.text())
  yield* _sseStream<BuildPlanEvent>(res)
}

export async function finishIntake(projectName: string, messages: string[]): Promise<IntakeFinishResponse> {
  const res = await fetch(`${BASE}/intake/finish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_name: projectName, messages }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
