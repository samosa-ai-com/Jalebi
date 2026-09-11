import { useCallback, useState } from "react";

const STORAGE_KEY = "jalebi-browser-notifications-v1";

export interface BrowserNotificationsHook {
  supported: boolean;
  enabled: boolean;
  enable: () => Promise<boolean>;
  disable: () => void;
}

export function useBrowserNotifications(): BrowserNotificationsHook {
  const supported =
    typeof window !== "undefined" &&
    typeof Notification !== "undefined" &&
    "Notification" in window;

  const [enabled, setEnabled] = useState<boolean>(() => {
    if (!supported) return false;
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  const enable = useCallback(async (): Promise<boolean> => {
    if (
      typeof window === "undefined" ||
      typeof Notification === "undefined" ||
      !("Notification" in window)
    ) {
      return false;
    }
    try {
      const perm = await Notification.requestPermission();
      if (perm === "granted") {
        try {
          localStorage.setItem(STORAGE_KEY, "1");
        } catch {
          return false;
        }
        setEnabled(true);
        return true;
      } else {
        try {
          localStorage.setItem(STORAGE_KEY, "0");
        } catch {
          // ignore storage failure
        }
        setEnabled(false);
        return false;
      }
    } catch {
      return false;
    }
  }, []);

  const disable = useCallback(() => {
    setEnabled(false);
    try {
      localStorage.setItem(STORAGE_KEY, "0");
    } catch {
      // ignore storage failure
    }
  }, []);

  return { supported, enabled, enable, disable };
}
