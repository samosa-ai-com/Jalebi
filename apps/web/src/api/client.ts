import type {
  BackupInfo,
  BackendsResponse,
  CatalogAgent,
  CatalogSkill,
  DataUsage,
  EnvVar,
  EventDelivery,
  FileEntry,
  GithubContext,
  GithubRepo,
  Health,
  IdeDetectResponse,
  PrunePreview,
  PublishCheck,
  Repo,
  Run,
  Screen,
  ScreeningRun,
  ScreenTemplate,
  SettingsMap,
  SseEvent,
  Task,
  RestorePreview,
  TimezoneList,
  TokensResponse,
  TriggerRule,
  WebhookStatus,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.error) detail = body.error;
      else if (body?.detail?.error) detail = body.detail.error;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export interface CreateTaskInput {
  repo_id: number;
  type: string;
  prompt: string;
  source_branch?: string;
  target_branch?: string;
  agent_id?: string;
  model?: string;
  cli?: string;
  pat_name?: string;
  issue_number?: number;
  pr_number?: number;
  publish_mode?: "auto" | "manual";
  reviewers?: string[];
  env_vars?: string[];
}

export const api = {
  getHealth: () => request<Health>("/api/health"),
  getSettings: () => request<SettingsMap>("/api/settings"),
  getBackends: () => request<BackendsResponse>("/api/backends"),
  getTimezones: () => request<TimezoneList>("/api/timezones"),
  getModels: (cli?: string) =>
    request<{ cli: string; models: string[] }>(
      cli ? `/api/models?cli=${encodeURIComponent(cli)}` : "/api/models"
    ),
  getAgents: (enabledOnly = false) =>
    request<CatalogAgent[]>(`/api/agents${enabledOnly ? "?enabled=1" : ""}`),
  createAgent: (input: {
    id: string;
    name: string;
    kind: string;
    cli?: string | null;
    model?: string | null;
    personality_md?: string;
    skills?: CatalogSkill[];
    custom_instructions?: string;
    enabled?: boolean;
  }) => request<CatalogAgent>("/api/agents", { method: "POST", body: JSON.stringify(input) }),
  updateAgent: (
    slug: string,
    input: {
      name?: string;
      kind?: string;
      cli?: string | null;
      model?: string | null;
      personality_md?: string;
      skills?: CatalogSkill[];
      custom_instructions?: string;
      enabled?: boolean;
    }
  ) =>
    request<CatalogAgent>(`/api/agents/${encodeURIComponent(slug)}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  deleteAgent: (slug: string) =>
    request<{ deleted: string }>(`/api/agents/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }),
  getWebhookStatus: () => request<WebhookStatus>("/api/webhook/status"),
  getTriggerRules: (repoId?: number) =>
    request<TriggerRule[]>(`/api/triggers${repoId ? `?repo_id=${repoId}` : ""}`),
  createTriggerRule: (input: {
    repo_id: number;
    event: string;
    action: string;
    branch_filter?: string;
    label_filter?: string[];
    author_filter?: string;
    agent_ids?: string[];
    custom_instructions?: string;
    enabled?: boolean;
  }) =>
    request<TriggerRule>("/api/triggers", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateTriggerRule: (
    id: number,
    input: {
      event?: string;
      action?: string;
      branch_filter?: string | null;
      label_filter?: string[];
      author_filter?: string | null;
      agent_ids?: string[];
      custom_instructions?: string | null;
      enabled?: boolean;
    }
  ) =>
    request<TriggerRule>(`/api/triggers/${id}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  deleteTriggerRule: (id: number) =>
    request<{ deleted: number }>(`/api/triggers/${id}`, { method: "DELETE" }),
  getDeliveries: () => request<EventDelivery[]>("/api/webhooks/deliveries"),
  replayDelivery: (id: number) =>
    request<{ matched: number; results: unknown[] }>(`/api/webhooks/deliveries/${id}/replay`, {
      method: "POST",
    }),
  getScreenTemplates: () => request<ScreenTemplate[]>("/api/screenings/templates"),
  getScreens: () => request<Screen[]>("/api/screenings"),
  createScreen: (input: {
    repo_id: number;
    name: string;
    system_prompt: string;
    cadence_cron: string;
    scope_branch?: string | null;
    cli?: string | null;
    model?: string | null;
    enabled?: boolean;
    notify_ntfy?: boolean;
  }) => request<Screen>("/api/screenings", { method: "POST", body: JSON.stringify(input) }),
  updateScreen: (
    id: number,
    input: Partial<{
      name: string;
      system_prompt: string;
      cadence_cron: string;
      scope_branch: string | null;
      cli: string | null;
      model: string | null;
      enabled: boolean;
      notify_ntfy: boolean;
    }>
  ) => request<Screen>(`/api/screenings/${id}`, { method: "PUT", body: JSON.stringify(input) }),
  deleteScreen: (id: number) =>
    request<{ ok: boolean }>(`/api/screenings/${id}`, { method: "DELETE" }),
  runScreen: (id: number) =>
    request<{ ok: boolean; screening_id: number }>(`/api/screenings/${id}/run`, {
      method: "POST",
    }),
  getScreenRuns: (id: number) => request<ScreeningRun[]>(`/api/screenings/${id}/runs`),
  getRepoBranches: (repoId: number) =>
    request<{ full_name: string; branches: string[] }>(`/api/repos/${repoId}/branches`),
  registerWebhook: (repoId: number) =>
    request<{ full_name: string; webhook_url: string; registered: boolean }>(
      `/api/repos/${repoId}/webhook`,
      { method: "POST" }
    ),
  unregisterWebhook: (repoId: number) =>
    request<{ full_name: string; removed: number; registered: boolean }>(
      `/api/repos/${repoId}/webhook`,
      { method: "DELETE" }
    ),
  updateSetting: (key: string, value: unknown) =>
    request<SettingsMap>(`/api/settings`, { method: "POST", body: JSON.stringify({ key, value }) }),
  testNotification: () => request<{ ok: boolean }>(`/api/notify/test`, { method: "POST" }),
  getEnvVars: (repoId?: number) =>
    request<EnvVar[]>(`/api/envvars${repoId ? `?repo_id=${repoId}` : ""}`),
  upsertEnvVar: (name: string, value: string, repoId?: number | null) =>
    request<EnvVar>(`/api/envvars`, {
      method: "POST",
      body: JSON.stringify({ name, value, repo_id: repoId ?? null }),
    }),
  deleteEnvVar: (id: number) =>
    request<{ deleted: number }>(`/api/envvars/${id}`, { method: "DELETE" }),
  importEnvVars: (content: string, repoId?: number | null) =>
    request<{ imported: number; env_vars: EnvVar[] }>(`/api/envvars/import`, {
      method: "POST",
      body: JSON.stringify({ content, repo_id: repoId ?? null }),
    }),
  getGithubRepos: (account?: string) =>
    request<GithubRepo[]>(
      `/api/github/repos${account ? `?account=${encodeURIComponent(account)}` : ""}`
    ),
  getGithubContext: (fullName: string, account?: string) =>
    request<GithubContext>(
      `/api/github/context?repo=${encodeURIComponent(fullName)}${
        account ? `&account=${encodeURIComponent(account)}` : ""
      }`
    ),
  getTokens: () => request<TokensResponse>("/api/github/tokens"),
  addToken: (name: string, token: string) =>
    request<{ stored: boolean; name: string }>("/api/github/tokens", {
      method: "POST",
      body: JSON.stringify({ name, token }),
    }),
  updateToken: (name: string, token: string) =>
    request<{
      updated: string;
      previous_login?: string | null;
      login?: string | null;
    }>(`/api/github/tokens/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ token }),
    }),
  deleteToken: (name: string) =>
    request<{ removed: string; repos_affected: string[]; tasks_affected: number }>(
      `/api/github/tokens/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    ),
  getTasks: () => request<Task[]>("/api/tasks"),
  getTask: (id: number) => request<Task>(`/api/tasks/${id}`),
  getRuns: (id: number) => request<Run[]>(`/api/tasks/${id}/runs`),
  getRunDiff: (id: number, runId: number) =>
    request<{ diff: string }>(`/api/tasks/${id}/runs/${runId}/diff`),
  getLiveDiff: (id: number) =>
    request<{ diff: string; base: boolean; untracked: boolean }>(
      `/api/tasks/${id}/diff?base=1&untracked=1`
    ),
  getPublishCheck: (id: number) => request<PublishCheck>(`/api/tasks/${id}/publish-check`),
  getIdeStatus: () => request<{ command: string; name: string; found: boolean }>("/api/ide/status"),
  detectIde: () => request<IdeDetectResponse>("/api/ide/detect"),
  testIde: () => request<{ ok: boolean; error?: string }>("/api/ide/test", { method: "POST" }),
  openInIde: (id: number) =>
    request<{ ok: boolean; path: string }>(`/api/tasks/${id}/open-in-ide`, {
      method: "POST",
    }),
  getTaskFiles: (id: number, path = "") =>
    request<{ path: string; entries: FileEntry[] }>(
      `/api/tasks/${id}/files${path ? `?path=${encodeURIComponent(path)}` : ""}`
    ),
  getTaskFileContent: (id: number, path: string) =>
    request<{ path: string; content: string; binary: boolean }>(
      `/api/tasks/${id}/files/content?path=${encodeURIComponent(path)}`
    ),
  createTask: (input: CreateTaskInput) =>
    request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(input) }),
  cancelTask: (id: number) =>
    request<{ status: string }>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  dismissAttention: (id: number) =>
    request<Task>(`/api/tasks/${id}/dismiss-attention`, { method: "POST" }),
  rerunTask: (id: number) => request<Task>(`/api/tasks/${id}/rerun`, { method: "POST" }),
  deleteTask: (id: number) =>
    request<{ deleted: number }>(`/api/tasks/${id}`, { method: "DELETE" }),
  publishTask: (
    id: number,
    opts: {
      mode?: "new_pr" | "update_pr" | "push_branch";
      branch?: string;
      pr_number?: number;
    } = {}
  ) => {
    const body: Record<string, unknown> = {};
    if (opts.mode) body.mode = opts.mode;
    if (opts.branch) body.branch = opts.branch;
    if (opts.pr_number !== undefined) body.pr_number = opts.pr_number;
    return request<{ status: string; mode: string; pr_number?: number; branch?: string }>(
      `/api/tasks/${id}/publish`,
      {
        method: "POST",
        body: Object.keys(body).length ? JSON.stringify(body) : undefined,
      }
    );
  },
  postFollowup: (
    id: number,
    prompt: string,
    opts?: { pat_name?: string; model?: string; cli?: string; include_reviews?: boolean }
  ) =>
    request<Task>(`/api/tasks/${id}/followup`, {
      method: "POST",
      body: JSON.stringify({ prompt, ...opts }),
    }),
  assignReviewers: (id: number, reviewers: string[]) =>
    request<Task[]>(`/api/tasks/${id}/reviewers`, {
      method: "POST",
      body: JSON.stringify({ reviewers }),
    }),
  getRepos: () => request<Repo[]>("/api/repos"),
  connectRepo: (fullName: string, patName?: string) =>
    request<Repo>("/api/repos", {
      method: "POST",
      body: JSON.stringify({ full_name: fullName, pat_name: patName }),
    }),
  disconnectRepo: (id: number) =>
    request<{ disconnected: string }>(`/api/repos/${id}`, { method: "DELETE" }),
  updateRepo: (id: number, input: { check_runs_enabled?: boolean }) =>
    request<Repo>(`/api/repos/${id}`, { method: "PATCH", body: JSON.stringify(input) }),
  pruneRepos: () => request<{ removed: string[] }>("/api/repos/prune", { method: "POST" }),
  getDataUsage: () => request<DataUsage>("/api/data/usage"),
  getBackups: () => request<BackupInfo[]>("/api/data/backups"),
  createBackup: () => request<BackupInfo>("/api/data/backups", { method: "POST" }),
  deleteBackup: (name: string) =>
    request<{ deleted: string }>(`/api/data/backups/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  backupDownloadUrl: (name: string) => `/api/data/backups/${encodeURIComponent(name)}/download`,
  restoreBackup: (name: string, input: { dry_run: boolean; confirm?: string }) =>
    request<{
      dry_run: boolean;
      preview: RestorePreview;
      restored?: string;
      safety_backup?: string;
    }>(`/api/data/backups/${encodeURIComponent(name)}/restore`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  vacuumData: () =>
    request<{ before: number; after: number }>("/api/data/vacuum", { method: "POST" }),
  pruneData: (input: {
    older_than_days: number;
    scopes: string[];
    dry_run: boolean;
    confirm?: string;
  }) =>
    request<{ dry_run: boolean; removed?: Record<string, number>; preview: PrunePreview }>(
      "/api/data/prune",
      { method: "POST", body: JSON.stringify(input) }
    ),
  artifactUrl: (taskId: number, artifactId: number) =>
    `/api/tasks/${taskId}/artifacts/${artifactId}/download`,
  artifactContentUrl: (taskId: number, artifactId: number) =>
    `/api/tasks/${taskId}/artifacts/${artifactId}/content`,
};

/** Subscribe to a task's live SSE stream. Returns an unsubscribe function. */
export function taskEvents(
  taskId: number,
  onEvent: (event: SseEvent) => void,
  onEnd: () => void,
  afterSeq?: number
): () => void {
  // Native EventSource cannot send custom headers: when JALEBI_PASSWORD is set,
  // this relies on the browser's cached Basic credentials (from the initial
  // prompt) being attached to the same-origin request. If creds are missing, the
  // stream 401s and closes — reload the page to re-prompt.
  const url = `/api/tasks/${taskId}/events${
    typeof afterSeq === "number" ? `?after_seq=${afterSeq}` : ""
  }`;
  const source = new EventSource(url);
  source.onmessage = (message) => {
    let event: SseEvent;
    try {
      event = JSON.parse(message.data) as SseEvent;
    } catch {
      return; // ignore malformed/keepalive lines
    }
    if (event.type === "connected") return;
    if (event.type === "stream_end") {
      source.close();
      onEnd();
      return;
    }
    onEvent(event);
  };
  // Transient errors: leave the EventSource open so the browser auto-reconnects
  // instead of killing the stream and losing buffered events.
  return () => source.close();
}
