import type {
  CatalogAgent,
  CatalogSkill,
  EnvVar,
  EventDelivery,
  GithubContext,
  GithubRepo,
  Health,
  Repo,
  Run,
  SettingsMap,
  SseEvent,
  Task,
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
  getModels: () => request<{ cli: string; models: string[] }>("/api/models"),
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
  }) =>
    request<CatalogAgent>("/api/agents", { method: "POST", body: JSON.stringify(input) }),
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
    request<TriggerRule[]>(
      `/api/triggers${repoId ? `?repo_id=${repoId}` : ""}`
    ),
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
    request<{ matched: number; results: unknown[] }>(
      `/api/webhooks/deliveries/${id}/replay`,
      { method: "POST" }
    ),
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
  testNotification: () =>
    request<{ ok: boolean }>(`/api/notify/test`, { method: "POST" }),
  getEnvVars: (repoId?: number) =>
    request<EnvVar[]>(
      `/api/envvars${repoId ? `?repo_id=${repoId}` : ""}`
    ),
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
  createTask: (input: CreateTaskInput) =>
    request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(input) }),
  cancelTask: (id: number) =>
    request<{ status: string }>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  rerunTask: (id: number) => request<Task>(`/api/tasks/${id}/rerun`, { method: "POST" }),
  deleteTask: (id: number) =>
    request<{ deleted: number }>(`/api/tasks/${id}`, { method: "DELETE" }),
  publishTask: (id: number) =>
    request<{ pr_number: number }>(`/api/tasks/${id}/publish`, { method: "POST" }),
  postFollowup: (id: number, prompt: string, opts?: { pat_name?: string; model?: string; include_reviews?: boolean }) =>
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
  pruneRepos: () =>
    request<{ removed: string[] }>("/api/repos/prune", { method: "POST" }),
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
