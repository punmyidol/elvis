export type TaskStatus = 'pending' | 'done' | 'failed'

export interface Task {
  index: number
  task: string
  status: TaskStatus
  output_file: string | null
  error: string | null
}

export interface Run {
  run_id: string
  created_at: string
  tasks: Task[]
}

export interface RunSummary {
  run_id: string
  created_at: string
  total: number
  done: number
  failed: number
}

export type CadEventStatus = 'context' | 'generating' | 'executing' | 'validating' | 'retry' | 'done'

export interface CadStatusEvent {
  status: CadEventStatus
  message: string
  success?: boolean
  basename?: string
  attempts?: number
  error?: string
  script?: string
}

export interface CadOutput {
  id: number
  prompt: string
  attempts: number
  success: boolean
  basename: string | null
  created_at: string
}

// Chat
export interface ChatThread {
  thread_id: string
  title: string
  created_at: string
  updated_at: string
}

export type ChatRole = 'user' | 'assistant'

export interface ChatMessage {
  role: ChatRole
  content: string
}

export interface ChatEvent {
  type: 'chunk' | 'done' | 'error'
  text?: string
  message?: string
}

export interface WeeklySummary {
  id: number
  source: string
  period: string
  summary: string
  created_at: string
}

// Brain (second-brain surfacing)
export interface BrainSurfacedRow {
  id: number
  topic: string
  source_signals: string[]
  reason: string
  obsidian_note_path: string
  engaged: boolean
  created_at: string
}

export interface BrainBucket {
  total: number
  engaged: number
  rate: number
}

export interface BrainStats {
  all_time: BrainBucket
  last_30d: BrainBucket
  last_7d: BrainBucket
  by_signal: Record<string, BrainBucket>
}

export interface BrainTrendPoint {
  day: string
  surfaced: number
  engaged: number
}

export interface BrainNote {
  path: string
  content: string
}

export interface BrainEngagementRunResult {
  newly_engaged: number
  total_engaged: number
}

export type BrainSurfaceEvent =
  | { type: 'log'; message: string }
  | { type: 'done'; written: number }
  | { type: 'error'; message: string }

// Notes (elvis-surfaced editor)
export interface SurfacedNote {
  filename: string
  path: string
  title: string
  created: string
  source_signals: string[]
}

export interface NotesChatEvent {
  type: 'chunk' | 'done' | 'error'
  text?: string
  message?: string
}
