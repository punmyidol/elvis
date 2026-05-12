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

// Thinking agent
export type ThinkEventType = 'layer' | 'tasks' | 'checkpoint' | 'intent' | 'done' | 'error'

export interface ThinkTask {
  id: string
  description: string
  type: string
  status: string
  iteration_created: number
  depends_on: string[]
}

export interface ThinkEvent {
  type: ThinkEventType
  message?: string
  text?: string
  session_id?: string
  is_done?: boolean
  intent?: string
  tasks?: ThinkTask[]
}

export interface ThinkSessionSummary {
  session_id: string
  prompt: string
  status: string
  iteration: number
  created_at: string
}

export interface ThinkSessionDetail extends ThinkSessionSummary {
  tasks: ThinkTask[]
  latest_checkpoint: string | null
}

export interface ThinkFile {
  filename: string
  size: number
  is_checkpoint: boolean
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
