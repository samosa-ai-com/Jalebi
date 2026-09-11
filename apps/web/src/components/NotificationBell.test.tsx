import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { TaskNotification } from "../types";
import NotificationBell from "./NotificationBell";

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => {
  const mod = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...mod,
    useNavigate: () => navigateMock,
  };
});

const NOTIFICATIONS: TaskNotification[] = [
  {
    id: 1,
    task_id: 101,
    run_id: 11,
    kind: "task_done",
    title: "Build succeeded",
    body: "Task completed without errors",
    read_at: null,
    created_at: new Date().toISOString(),
    repo_id: 1,
    type: "build",
    status: "done",
    pr_number: 42,
  },
  {
    id: 2,
    task_id: 102,
    run_id: 12,
    kind: "task_failed",
    title: "Test suite failed",
    body: "3 tests failed in CI",
    read_at: "2026-09-10T12:00:00Z",
    created_at: new Date(Date.now() - 3600000).toISOString(),
    repo_id: 1,
    type: "test",
    status: "failed",
    pr_number: null,
  },
  {
    id: 3,
    task_id: 103,
    run_id: 13,
    kind: "needs_input",
    title: "Confirmation required",
    body: "Please approve deployment",
    read_at: null,
    created_at: new Date(Date.now() - 7200000).toISOString(),
    repo_id: 2,
    type: "deploy",
    status: "waiting",
    pr_number: null,
  },
];

describe("NotificationBell", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hides badge when unread count is 0", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 0 });
    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(api.getUnreadCount).toHaveBeenCalledTimes(1);
    });

    expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument();
  });

  it("displays badge count when unread count is greater than 0", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 5 });
    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const badge = await screen.findByTestId("unread-badge");
    expect(badge).toHaveTextContent("5");
  });

  it("caps badge count display at 9+ when unread is greater than 9", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 15 });
    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const badge = await screen.findByTestId("unread-badge");
    expect(badge).toHaveTextContent("9+");
  });

  it("keeps the server unread count when the visible page shows fewer unread", async () => {
    // Server knows 5 unread; the newest page contains only the read row.
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 5 });
    vi.spyOn(api, "getNotifications").mockResolvedValue([NOTIFICATIONS[1]]);
    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );
    expect(await screen.findByTestId("unread-badge")).toHaveTextContent("5");
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    await screen.findByTestId("notification-panel");
    // The single visible row is read, but badge and bulk action persist.
    expect(await screen.findByTestId("unread-badge")).toHaveTextContent("5");
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeInTheDocument();
    expect(screen.queryByTestId("unread-dot")).not.toBeInTheDocument();
  });

  it("opens panel and displays notification items with kind icons, titles, sublines, and unread dots", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 2 });
    vi.spyOn(api, "getNotifications").mockResolvedValue(NOTIFICATIONS);

    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const bellBtn = screen.getByRole("button", { name: /notifications/i });
    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();

    await userEvent.click(bellBtn);

    const panel = await screen.findByTestId("notification-panel");
    expect(panel).toBeInTheDocument();
    expect(api.getNotifications).toHaveBeenCalledTimes(1);

    // Verify titles
    expect(screen.getByText("Build succeeded")).toBeInTheDocument();
    expect(screen.getByText("Test suite failed")).toBeInTheDocument();
    expect(screen.getByText("Confirmation required")).toBeInTheDocument();

    // Verify task IDs in sublines
    expect(screen.getByText(/#101/)).toBeInTheDocument();
    expect(screen.getByText(/#102/)).toBeInTheDocument();
    expect(screen.getByText(/#103/)).toBeInTheDocument();

    // Verify kind indicators
    const kindIcons = screen.getAllByTestId("kind-icon");
    expect(kindIcons).toHaveLength(3);
    expect(kindIcons[0]).toHaveAttribute("data-kind", "task_done");
    expect(kindIcons[1]).toHaveAttribute("data-kind", "task_failed");
    expect(kindIcons[2]).toHaveAttribute("data-kind", "needs_input");

    // Notifications 1 and 3 are unread, notification 2 is read
    const unreadDots = screen.getAllByTestId("unread-dot");
    expect(unreadDots).toHaveLength(2);
  });

  it("marks notification read and navigates to /tasks/:id on row click", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 2 });
    vi.spyOn(api, "getNotifications").mockResolvedValue(NOTIFICATIONS);
    const markReadSpy = vi.spyOn(api, "markNotificationRead").mockResolvedValue({
      ...NOTIFICATIONS[0],
      read_at: new Date().toISOString(),
    });

    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const bellBtn = screen.getByRole("button", { name: /notifications/i });
    await userEvent.click(bellBtn);

    const row = await screen.findByText("Build succeeded");
    await userEvent.click(row);

    expect(markReadSpy).toHaveBeenCalledWith(1);
    expect(navigateMock).toHaveBeenCalledWith("/tasks/101");
    // Panel closes after clicking row
    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();
  });

  it("marks all notifications read when clicking Mark all read", async () => {
    // The server count is authoritative: after mark-all it reports zero.
    let markedAll = false;
    vi.spyOn(api, "getUnreadCount").mockImplementation(async () => ({
      unread: markedAll ? 0 : 2,
    }));
    vi.spyOn(api, "getNotifications").mockResolvedValue(NOTIFICATIONS);
    const markAllSpy = vi
      .spyOn(api, "markAllNotificationsRead")
      .mockImplementation(async () => {
        markedAll = true;
        return { marked: 2 };
      });

    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const bellBtn = screen.getByRole("button", { name: /notifications/i });
    await userEvent.click(bellBtn);

    const markAllBtn = await screen.findByRole("button", { name: "Mark all read" });
    expect(markAllBtn).toBeInTheDocument();

    await userEvent.click(markAllBtn);

    expect(markAllSpy).toHaveBeenCalledTimes(1);

    // Unread dots should be gone
    await waitFor(() => {
      expect(screen.queryByTestId("unread-dot")).not.toBeInTheDocument();
      expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Mark all read" })).not.toBeInTheDocument();
    });
  });

  it("shows empty state when notifications list is empty", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 0 });
    vi.spyOn(api, "getNotifications").mockResolvedValue([]);

    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const bellBtn = screen.getByRole("button", { name: /notifications/i });
    await userEvent.click(bellBtn);

    expect(await screen.findByText("You are all caught up.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark all read" })).not.toBeInTheDocument();
  });

  it("closes panel on outside click and on Escape key", async () => {
    vi.spyOn(api, "getUnreadCount").mockResolvedValue({ unread: 0 });
    vi.spyOn(api, "getNotifications").mockResolvedValue([]);

    render(
      <MemoryRouter>
        <div>
          <NotificationBell />
          <div data-testid="outside-element">Outside</div>
        </div>
      </MemoryRouter>
    );

    const bellBtn = screen.getByRole("button", { name: /notifications/i });

    // Test Escape key
    await userEvent.click(bellBtn);
    expect(await screen.findByTestId("notification-panel")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();

    // Test outside click
    await userEvent.click(bellBtn);
    expect(await screen.findByTestId("notification-panel")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId("outside-element"));
    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();
  });

  it("does not crash on fetch errors (best effort)", async () => {
    vi.spyOn(api, "getUnreadCount").mockRejectedValue(new Error("Network failure"));
    vi.spyOn(api, "getNotifications").mockRejectedValue(new Error("Network failure"));

    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    );

    const bellBtn = screen.getByRole("button", { name: /notifications/i });
    expect(bellBtn).toBeInTheDocument();

    // Open panel despite error
    await userEvent.click(bellBtn);
    expect(await screen.findByTestId("notification-panel")).toBeInTheDocument();
  });
});

describe("Notification API client methods", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("getNotifications sends correct query parameters", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => [],
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.getNotifications({ unreadOnly: true, limit: 10 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/notifications?unread_only=true&limit=10",
      expect.anything()
    );

    await api.getNotifications();
    expect(fetchMock).toHaveBeenCalledWith("/api/notifications", expect.anything());
  });

  it("getUnreadCount calls /api/notifications/unread-count", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ unread: 3 }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await api.getUnreadCount();
    expect(res).toEqual({ unread: 3 });
    expect(fetchMock).toHaveBeenCalledWith("/api/notifications/unread-count", expect.anything());
  });

  it("markNotificationRead sends POST to /api/notifications/:id/read", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 42 }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.markNotificationRead(42);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/notifications/42/read",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("markAllNotificationsRead sends POST to /api/notifications/read-all", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ marked: 5 }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.markAllNotificationsRead();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/notifications/read-all",
      expect.objectContaining({ method: "POST" })
    );
  });
});
