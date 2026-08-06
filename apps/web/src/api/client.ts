import type {
  GithubRepo,
  Health,
  Repo,
  SettingsMap,
  SseEvent,
  Task,
  TokenInfo,
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
}

export const api = {
  getHealth: () => request<Health>("/api/health"),
  getSettings: () => request<SettingsMap>("/api/settings"),
  updateSetting: (key: string, value: unknown) =>
    request<SettingsMap>(`/api/settings`, { method: "POST", body: JSON.stringify({ key, value }) }),
  getGithubStatus: () => request<TokenInfo>("/api/github/status"),
  putGithubToken: (token: string) =>
    request<{ stored: boolean; detail: TokenInfo }>("/api/github/token", {
      method: "PUT",
      body: JSON.stringify({ token }),
    }),
  getGithubRepos: () => request<GithubRepo[]>("/api/github/repos"),
  getTasks: () => request<Task[]>("/api/tasks"),
  getTask: (id: number) => request<Task>(`/api/tasks/${id}`),
  createTask: (input: CreateTaskInput) =>
    request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(input) }),
  cancelTask: (id: number) =>
    request<{ status: string }>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  rerunTask: (id: number) => request<Task>(`/api/tasks/${id}/rerun`, { method: "POST" }),
  publishTask: (id: number) =>
    request<{ pr_number: number }>(`/api/tasks/${id}/publish`, { method: "POST" }),
  postFollowup: (id: number, prompt: string) =>
    request<Task>(`/api/tasks/${id}/followup`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  getRepos: () => request<Repo[]>("/api/repos"),
  connectRepo: (fullName: string) =>
    request<Repo>("/api/repos", { method: "POST", body: JSON.stringify({ full_name: fullName }) }),
  artifactUrl: (taskId: number, artifactId: number) =>
    `/api/tasks/${taskId}/artifacts/${artifactId}/download`,
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
