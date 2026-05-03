import type { Run, RunSummary } from './types'

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
