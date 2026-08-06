export interface Step {
  type: string;
  phase?: string | null;
  text?: string | null;
  ts: string;
}

export interface Run {
  id: number;
  seq: number;
  session_id: string | null;
  cli: string | null;
  model: string | null;
  status: string | null;
  started_at: string | null;
  finished_at: string | null;
  steps: Step[];
}

export interface Task {
  id: number;
  type: string;
  repo_id: number;
  source_branch: string;
  target_branch: string;
  model: string | null;
  cli: string | null;
  prompt: string;
  status: string;
  timeout_minutes: number;
  retry_count: number;
  pr_number: number | null;
  created_at: string;
  updated_at: string;
  run: Run | null;
}

export interface Repo {
  id: number;
  full_name: string;
  default_branch: string;
  clone_url: string;
}

export interface SseEvent {
  type: string;
  phase?: string | null;
  text?: string | null;
  ts?: string;
}
