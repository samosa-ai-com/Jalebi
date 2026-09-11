import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { type TaskStatusSnapshot, useStatusAnnouncer } from "./useStatusAnnouncer";

describe("useStatusAnnouncer", () => {
  it("does not announce on initial mount (steady state)", () => {
    const initialTasks: TaskStatusSnapshot[] = [
      { id: 1, status: "done", attention: "done" },
      { id: 2, status: "running", attention: "working" },
    ];
    const { result } = renderHook(({ tasks }) => useStatusAnnouncer(tasks), {
      initialProps: { tasks: initialTasks },
    });
    expect(result.current).toBe("");
  });

  it("announces once when a task transitions to done", () => {
    const initialTasks: TaskStatusSnapshot[] = [
      { id: 7, status: "running", attention: "working" },
    ];
    const { result, rerender } = renderHook(
      ({ tasks }) => useStatusAnnouncer(tasks),
      {
        initialProps: { tasks: initialTasks },
      }
    );
    expect(result.current).toBe("");

    // Transition to done
    rerender({
      tasks: [{ id: 7, status: "done", attention: "done" }],
    });
    expect(result.current).toBe("Task 7 done");
  });

  it("does not repeat announcement on re-render without change", () => {
    const initialTasks: TaskStatusSnapshot[] = [
      { id: 7, status: "running", attention: "working" },
    ];
    const { result, rerender } = renderHook(
      ({ tasks }) => useStatusAnnouncer(tasks),
      {
        initialProps: { tasks: initialTasks },
      }
    );

    rerender({
      tasks: [{ id: 7, status: "done", attention: "done" }],
    });
    expect(result.current).toBe("Task 7 done");

    // Re-render without change
    rerender({
      tasks: [{ id: 7, status: "done", attention: "done" }],
    });
    expect(result.current).toBe("");
  });

  it("announces when attention becomes needs_you", () => {
    const initialTasks: TaskStatusSnapshot[] = [
      { id: 7, status: "running", attention: "working" },
    ];
    const { result, rerender } = renderHook(
      ({ tasks }) => useStatusAnnouncer(tasks),
      {
        initialProps: { tasks: initialTasks },
      }
    );

    rerender({
      tasks: [{ id: 7, status: "running", attention: "needs_you" }],
    });
    expect(result.current).toBe("Task 7 needs your input");
  });

  it("caps announcements at 3 joined with '; ' when multiple tasks transition", () => {
    const initialTasks: TaskStatusSnapshot[] = [
      { id: 1, status: "running", attention: "working" },
      { id: 2, status: "running", attention: "working" },
      { id: 3, status: "running", attention: "working" },
      { id: 4, status: "running", attention: "working" },
      { id: 5, status: "running", attention: "working" },
    ];
    const { result, rerender } = renderHook(
      ({ tasks }) => useStatusAnnouncer(tasks),
      {
        initialProps: { tasks: initialTasks },
      }
    );

    rerender({
      tasks: [
        { id: 1, status: "done", attention: "done" },
        { id: 2, status: "failed", attention: "done" },
        { id: 3, status: "timed_out", attention: "done" },
        { id: 4, status: "interrupted", attention: "done" },
        { id: 5, status: "running", attention: "needs_you" },
      ],
    });

    expect(result.current).toBe(
      "Task 1 done; Task 2 failed; Task 3 timed out"
    );
  });
});
