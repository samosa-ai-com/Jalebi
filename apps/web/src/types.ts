export interface Step {
  type: string;
  phase?: string | null;
  text?: string | null;
  ts: string;
}

export interface Artifact {
  id: number;
  path: string;
  size: number;
  created_at: string;
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
  artifacts?: Artifact[];
}

export interface Followup {
  id: number;
  body: string;
  created_at: string;
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
  followups?: Followup[];
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

export interface Health {
  status: string;
}

export interface TokenInfo {
  valid: boolean;
  login: string | null;
  token_type: string | null;
  granted_scopes: string[];
  missing_scopes: string[];
  note: string | null;
  error: string | null;
}

export interface GithubRepo {
  full_name: string;
  private: boolean;
  default_branch: string | null;
  clone_url: string;
  html_url: string;
}

export interface SettingsMap {
  concurrency: number;
  auto_publish: boolean;
  ntfy_topic: string;
  default_timeout_minutes: number;
  retry_policy: { auto_retry: boolean };
  secret_patterns: string[];
  artifact_ttl_days: number;
  agent_cli: string;
}
