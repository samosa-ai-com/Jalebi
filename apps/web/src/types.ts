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

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  extension: string;
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
  waiting_input?: boolean;
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

export interface ReviewAssignment {
  id: number;
  task_id: number;
  agent_id: string;
  agent_name: string;
  run_id: number | null;
  pr_number: number;
  repo_id: number;
  status: string;
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
  check_run_id: number | null;
  issues: number[];
  prs: number[];
  env_vars: string[];
  created_at: string;
  updated_at: string;
  run: Run | null;
  waiting_input?: boolean;
  attention: AttentionValue;
  followups?: Followup[];
  reviewers?: ReviewAssignment[];
}

export type AttentionValue =
  | "working"
  | "needs_you"
  | "in_review"
  | "ready_to_merge"
  | "done";

export type PublishCheckStatus = "ready" | "attention" | "blocked";

export interface PublishCheckItem {
  name: "branch" | "commits" | "conflict" | "ci" | "review" | "mergeable";
  ok: boolean;
  message: string;
  ahead?: number;
  conflicts?: { kind: string; path: string }[];
  state?: string | null;
  decision?: string | null;
  mergeable?: boolean | null;
}

export interface PublishCheck {
  status: PublishCheckStatus;
  base_ref: string;
  checks: PublishCheckItem[];
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

export interface RetryPolicy {
  auto_retry: boolean;
  continue_prompt?: string;
  timeout_multiplier?: number;
  max_timeout_minutes?: number;
}

export interface SettingsMap {
  concurrency: number;
  auto_publish: boolean;
  ntfy_topic: string;
  default_timeout_minutes: number;
  retry_policy: RetryPolicy;
  stall_timeout_seconds: number;
  secret_patterns: string[];
  artifact_ttl_days: number;
  default_backend: string;
  default_model: string;
  notify_on_done: boolean;
  notify_on_failed: boolean;
  notify_on_progress: boolean;
  notify_on_needs_approval: boolean;
  notify_progress_interval_minutes: number;
  webhook_url: string;
  webhook_secret: string;
  timezone: string;
  ide_command: string;
  ide_name: string;
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

export interface TriggerRule {
  id: number;
  repo_id: number;
  event: string;
  action: string;
  branch_filter: string | null;
  label_filter: string[];
  author_filter: string | null;
  agent_ids: string[];
  custom_instructions: string | null;
  enabled: boolean;
  created_at: string;
}

export interface EventDelivery {
  id: number;
  github_delivery_id: string;
  event: string;
  action: string | null;
  repo_id: number | null;
  repo_full_name: string | null;
  received_at: string;
  status: string;
  result: unknown;
}

export interface WebhookStatus {
  url: string;
  secret_set: boolean;
  reachable: boolean;
  repos: {
    id: number;    full_name: string;
    webhook_registered: boolean;
    poll_fallback: boolean;
  }[];
}

export interface Finding {
  severity: "critical" | "high" | "medium" | "low";
  title: string;
  file: string | null;
  line: number | null;
  detail: string | null;
  recommendation: string | null;
}

export interface ScreeningRun {
  id: number;
  screening_id: number;
  head_sha: string | null;
  status: "queued" | "running" | "done" | "failed";
  started_at: string | null;
  finished_at: string | null;
  findings: Finding[];
  output: { message?: string } | null;
  error: string | null;
}

export interface Screen {
  id: number;
  repo_id: number;
  name: string;
  system_prompt: string;
  cadence_cron: string;
  scope_branch: string | null;
  cli: string | null;
  model: string | null;
  enabled: boolean;
  notify_ntfy: boolean;
  created_at: string;
  updated_at: string;
  latest_run?: ScreeningRun | null;
}

export interface ScreenTemplate {
  name: string;
  cadence_cron: string;
  system_prompt: string;
}
