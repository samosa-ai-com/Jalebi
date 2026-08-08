export interface Step {
  type: string;
  phase?: string | null;
  text?: string | null;
  ts: string;
  seq?: number;
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
  pat_name: string | null;
  status: string | null;
  started_at: string | null;
  finished_at: string | null;
  has_diff: boolean;
  steps: Step[];
  artifacts?: Artifact[];
}

export interface Followup {
  id: number;
  body: string;
  pat_name: string | null;
  model: string | null;
  created_at: string;
}

export interface Task {
  id: number;
  type: string;
  repo_id: number;
  repo_full_name: string | null;
  source_branch: string;
  target_branch: string;
  agent_id: string | null;
  model: string | null;
  cli: string | null;
  pat_name: string | null;
  prompt: string;
  status: string;
  timeout_minutes: number;
  retry_count: number;
  pr_number: number | null;
  publish_mode: "auto" | "manual" | null;
  issues: number[];
  prs: number[];
  env_vars: string[];
  created_at: string;
  updated_at: string;
  run: Run | null;
  followups?: Followup[];
}

export interface Repo {
  id: number;
  full_name: string;
  default_branch: string;
  connected: boolean;
  pat_name: string | null;
  webhook_registered: boolean;
  poll_fallback: boolean;
  check_runs_enabled: boolean;
  last_checked_at: string | null;
}

export interface SseEvent {
  type: string;
  phase?: string | null;
  text?: string | null;
  ts?: string;
  seq?: number;
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
  html_url: string;
  account?: string | null;
  error?: string;
}

export interface GithubIssue {
  number: number;
  title: string;
  html_url: string;
  state: string;
}

export interface GithubPr {
  number: number;
  title: string;
  html_url: string;
  state: string;
  base: string | null;
  head: string | null;
  author: string | null;
}

export interface GithubContext {
  issues: GithubIssue[];
  prs: GithubPr[];
  branches: string[];
}

export interface Account {
  name: string;
  login: string | null;
  masked: string;
  token_type: string | null;
  granted_scopes: string[];
  missing_scopes: string[];
  note: string | null;
  valid: boolean;
  error: string | null;
}

export interface TokensResponse {
  accounts: Account[];
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
  notify_on_done: boolean;
  notify_on_failed: boolean;
  notify_on_progress: boolean;
  notify_on_needs_approval: boolean;
  notify_progress_interval_minutes: number;
}

export interface EnvVar {
  id: number;
  name: string;
  masked: string;
  repo_id: number | null;
  repo_full_name: string | null;
  created_at: string;
}

export interface CatalogSkill {
  name: string;
  content: string;
}

export interface CatalogAgent {
  id: string;
  name: string;
  kind: "general" | "reviewer";
  cli: string | null;
  model: string | null;
  personality_md: string;
  skills: CatalogSkill[];
  custom_instructions: string;
  enabled: boolean;
  created_at: string;
}
