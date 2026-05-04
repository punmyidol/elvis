import type { Run, RunSummary, CadStatusEvent, CadOutput } from './types'

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
