import type {
  GithubContext,
  GithubRepo,
  Health,
  Repo,
  Run,
  SettingsMap,
  SseEvent,
  Task,
  TokenInfo,
  TokensResponse,
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
  model?: string;
  cli?: string;
  pat_name?: string;
  issue_number?: number;
  pr_number?: number;
}

export const api = {
  getHealth: () => request<Health>("/api/health"),
  getSettings: () => request<SettingsMap>("/api/settings"),
  getModels: () => request<{ cli: string; models: string[] }>("/api/models"),
  updateSetting: (key: string, value: unknown) =>
    request<SettingsMap>(`/api/settings`, { method: "POST", body: JSON.stringify({ key, value }) }),
  getGithubStatus: () => request<TokenInfo>("/api/github/status"),
  putGithubToken: (token: string) =>
    request<{ stored: boolean; detail: TokenInfo }>("/api/github/token", {
      method: "PUT",
      body: JSON.stringify({ token }),
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
  createTask: (input: CreateTaskInput) =>
    request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(input) }),
  cancelTask: (id: number) =>
    request<{ status: string }>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  rerunTask: (id: number) => request<Task>(`/api/tasks/${id}/rerun`, { method: "POST" }),
  publishTask: (id: number) =>
    request<{ pr_number: number }>(`/api/tasks/${id}/publish`, { method: "POST" }),
  postFollowup: (id: number, prompt: string, opts?: { pat_name?: string; model?: string }) =>
    request<Task>(`/api/tasks/${id}/followup`, {
      method: "POST",
      body: JSON.stringify({ prompt, ...opts }),
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
  getBranches: (id: number) =>
    request<{ full_name: string; branches: string[] }>(`/api/repos/${id}/branches`),
  artifactUrl: (taskId: number, artifactId: number) =>
    `/api/tasks/${taskId}/artifacts/${artifactId}/download`,
  artifactContentUrl: (taskId: number, artifactId: number) =>
    `/api/tasks/${taskId}/artifacts/${artifactId}/content`,
};

/** Subscribe to a task's live SSE stream. Returns an unsubscribe function. */
export function taskEvents(
  taskId: number,
  onEvent: (event: SseEvent) => void,
  onEnd: () => void
): () => void {
  const source = new EventSource(`/api/tasks/${taskId}/events`);
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
