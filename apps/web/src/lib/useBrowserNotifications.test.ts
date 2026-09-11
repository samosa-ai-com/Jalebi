import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBrowserNotifications } from "./useBrowserNotifications";

describe("useBrowserNotifications", () => {
  const originalNotification = window.Notification;

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    if (originalNotification) {
      window.Notification = originalNotification;
    } else {
      delete (window as { Notification?: unknown }).Notification;
    }
    localStorage.clear();
  });

  it("handles unsupported browser when Notification is undefined", async () => {
    delete (window as { Notification?: unknown }).Notification;

    const { result } = renderHook(() => useBrowserNotifications());
    expect(result.current.supported).toBe(false);
    expect(result.current.enabled).toBe(false);

    let ok = true;
    await act(async () => {
      ok = await result.current.enable();
    });
    expect(ok).toBe(false);
    expect(result.current.enabled).toBe(false);
    expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBeNull();

    act(() => {
      result.current.disable();
    });
    expect(result.current.enabled).toBe(false);
    expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBe("0");
  });

  it("initializes enabled state from localStorage", () => {
    const mockNotification = {
      permission: "granted" as NotificationPermission,
      requestPermission: vi.fn().mockResolvedValue("granted"),
    };
    (window as unknown as { Notification: typeof mockNotification }).Notification = mockNotification;

    localStorage.setItem("jalebi-browser-notifications-v1", "1");
    const { result } = renderHook(() => useBrowserNotifications());
    expect(result.current.supported).toBe(true);
    expect(result.current.enabled).toBe(true);
  });

  it("enables when permission is granted", async () => {
    const mockNotification = {
      permission: "default" as NotificationPermission,
      requestPermission: vi.fn().mockResolvedValue("granted"),
    };
    (window as unknown as { Notification: typeof mockNotification }).Notification = mockNotification;

    const { result } = renderHook(() => useBrowserNotifications());
    expect(result.current.supported).toBe(true);
    expect(result.current.enabled).toBe(false);

    let ok = false;
    await act(async () => {
      ok = await result.current.enable();
    });
    expect(ok).toBe(true);
    expect(result.current.enabled).toBe(true);
    expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBe("1");
    expect(mockNotification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("does not enable when permission is denied", async () => {
    const mockNotification = {
      permission: "default" as NotificationPermission,
      requestPermission: vi.fn().mockResolvedValue("denied"),
    };
    (window as unknown as { Notification: typeof mockNotification }).Notification = mockNotification;

    const { result } = renderHook(() => useBrowserNotifications());
    let ok = true;
    await act(async () => {
      ok = await result.current.enable();
    });
    expect(ok).toBe(false);
    expect(result.current.enabled).toBe(false);
    expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBe("0");
  });

  it("disables notifications and sets localStorage to 0", () => {
    const mockNotification = {
      permission: "granted" as NotificationPermission,
      requestPermission: vi.fn().mockResolvedValue("granted"),
    };
    (window as unknown as { Notification: typeof mockNotification }).Notification = mockNotification;
    localStorage.setItem("jalebi-browser-notifications-v1", "1");

    const { result } = renderHook(() => useBrowserNotifications());
    expect(result.current.enabled).toBe(true);

    act(() => {
      result.current.disable();
    });
    expect(result.current.enabled).toBe(false);
    expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBe("0");
  });

  it("returns false and stays disabled when permission is granted but localStorage.setItem throws", async () => {
    const mockNotification = {
      permission: "default" as NotificationPermission,
      requestPermission: vi.fn().mockResolvedValue("granted"),
    };
    (window as unknown as { Notification: typeof mockNotification }).Notification = mockNotification;

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    const { result } = renderHook(() => useBrowserNotifications());
    expect(result.current.enabled).toBe(false);

    let ok = true;
    await act(async () => {
      ok = await result.current.enable();
    });

    expect(ok).toBe(false);
    expect(result.current.enabled).toBe(false);
  });

  it("disables state even if localStorage.setItem throws in disable", () => {
    const mockNotification = {
      permission: "granted" as NotificationPermission,
      requestPermission: vi.fn().mockResolvedValue("granted"),
    };
    (window as unknown as { Notification: typeof mockNotification }).Notification = mockNotification;
    localStorage.setItem("jalebi-browser-notifications-v1", "1");

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    const { result } = renderHook(() => useBrowserNotifications());
    expect(result.current.enabled).toBe(true);

    act(() => {
      result.current.disable();
    });
    expect(result.current.enabled).toBe(false);
  });
});
