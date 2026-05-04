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
