import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { HealthResponse, SystemStatusResponse } from "../types/api";
import { notificationsApi, systemApi } from "../services/api";

export type TabId =
  | "dashboard"
  | "jobs"
  | "companies"
  | "applications"
  | "profiles"
  | "notifications"
  | "monitoring"
  | "settings";

export type Theme = "light" | "dark" | "system";

export type ReadinessState = "checking" | "ready" | "failed" | "skipped";

export interface ToastMessage {
  id: string;
  message: string;
  type: "success" | "error" | "info";
}

interface AppContextValue {
  // Navigation
  activeTab: TabId;
  setActiveTab: (tab: TabId) => void;

  // Theme
  theme: Theme;
  resolvedTheme: "light" | "dark";
  setTheme: (theme: Theme) => void;

  // Backend Connectivity & Readiness
  isOnline: boolean;
  isConnecting: boolean;
  readinessState: ReadinessState;
  readinessMessage: string;
  lastChecked: Date | null;
  health: HealthResponse | null;
  systemStatus: SystemStatusResponse | null;
  checkConnection: () => Promise<boolean>;
  retryReadiness: () => Promise<void>;
  skipReadinessGate: () => void;

  // Unread badge
  unreadCount: number;
  refreshUnreadCount: () => Promise<void>;

  // Global Job Detail Modal
  selectedJobId: number | null;
  setSelectedJobId: (id: number | null) => void;

  // Global Toasts
  toasts: ToastMessage[];
  showToast: (message: string, type?: "success" | "error" | "info") => void;
  dismissToast: (id: string) => void;

  // Global Refresh
  refreshAll: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeTab, setActiveTab] = useState<TabId>("dashboard");
  const [theme, setThemeState] = useState<Theme>(() => {
    return (localStorage.getItem("hawkoffer_theme") as Theme) || "system";
  });
  const [resolvedTheme, setResolvedTheme] = useState<"light" | "dark">("light");

  const [isOnline, setIsOnline] = useState<boolean>(false);
  const [isConnecting, setIsConnecting] = useState<boolean>(true);
  const [readinessState, setReadinessState] = useState<ReadinessState>("checking");
  const [readinessMessage, setReadinessMessage] = useState<string>(
    "Connecting to HawkOffer background daemon..."
  );
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatusResponse | null>(null);
  const [unreadCount, setUnreadCount] = useState<number>(0);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  // Theme resolution
  useEffect(() => {
    const root = document.documentElement;
    const media = window.matchMedia("(prefers-color-scheme: dark)");

    const applyTheme = () => {
      const isDark = theme === "dark" || (theme === "system" && media.matches);
      if (isDark) {
        root.classList.add("dark");
        setResolvedTheme("dark");
      } else {
        root.classList.remove("dark");
        setResolvedTheme("light");
      }
    };

    applyTheme();
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [theme]);

  const setTheme = (newTheme: Theme) => {
    localStorage.setItem("hawkoffer_theme", newTheme);
    setThemeState(newTheme);
  };

  const showToast = useCallback(
    (message: string, type: "success" | "error" | "info" = "info") => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setToasts((prev) => [...prev, { id, message, type }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 4000);
    },
    []
  );

  const dismissToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  const refreshUnreadCount = useCallback(async () => {
    try {
      const res = await notificationsApi.getUnreadCount();
      setUnreadCount(res.unread_count);
    } catch {
      // Ignored if offline
    }
  }, []);

  const checkConnection = useCallback(async (): Promise<boolean> => {
    setIsConnecting(true);
    try {
      const [h, s] = await Promise.all([systemApi.getHealth(), systemApi.getStatus()]);
      const healthy =
        h.database === "connected" && (h.status === "healthy" || h.status === "degraded");

      setHealth(h);
      setSystemStatus(s);
      setIsOnline(healthy);
      setUnreadCount(s.unread_notifications_count);
      setLastChecked(new Date());

      if (healthy) {
        setReadinessState("ready");
        setReadinessMessage("Backend daemon is healthy and connected.");
        return true;
      } else {
        setReadinessState("failed");
        setReadinessMessage(`Database reporting status: ${h.database}`);
        return false;
      }
    } catch (err: unknown) {
      setIsOnline(false);
      setHealth(null);
      setSystemStatus(null);
      setReadinessState("failed");
      const errorMsg =
        err instanceof Error ? err.message : "Unable to reach 127.0.0.1:8765";
      setReadinessMessage(errorMsg);
      return false;
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const retryReadiness = useCallback(async () => {
    setReadinessState("checking");
    setReadinessMessage("Probing backend service...");
    // Bounded retry loop (up to 4 attempts spaced by 750ms)
    for (let attempt = 1; attempt <= 4; attempt++) {
      setReadinessMessage(`Connecting to backend service (attempt ${attempt}/4)...`);
      const ready = await checkConnection();
      if (ready) {
        return;
      }
      if (attempt < 4) {
        await new Promise((resolve) => setTimeout(resolve, 750));
      }
    }
    setReadinessState("failed");
    setReadinessMessage("Could not connect to backend daemon at 127.0.0.1:8765.");
  }, [checkConnection]);

  const skipReadinessGate = useCallback(() => {
    setReadinessState("skipped");
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([checkConnection(), refreshUnreadCount()]);
  }, [checkConnection, refreshUnreadCount]);

  // Initial boot and periodic heartbeats
  useEffect(() => {
    let mounted = true;

    const initialBoot = async () => {
      const ready = await checkConnection();
      if (!ready && mounted) {
        // Automatically attempt a second probe after 1 second for slow startups
        setTimeout(async () => {
          if (mounted && readinessState === "failed") {
            await checkConnection();
          }
        }, 1200);
      }
    };

    initialBoot();

    // Heartbeat every 30s
    const interval = setInterval(() => {
      if (mounted) {
        refreshAll();
      }
    }, 30000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [checkConnection, refreshAll, readinessState]);

  return (
    <AppContext.Provider
      value={{
        activeTab,
        setActiveTab,
        theme,
        resolvedTheme,
        setTheme,
        isOnline,
        isConnecting,
        readinessState,
        readinessMessage,
        lastChecked,
        health,
        systemStatus,
        checkConnection,
        retryReadiness,
        skipReadinessGate,
        unreadCount,
        refreshUnreadCount,
        selectedJobId,
        setSelectedJobId,
        toasts,
        showToast,
        dismissToast,
        refreshAll,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useApp = (): AppContextValue => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return context;
};
