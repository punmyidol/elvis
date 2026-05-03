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
