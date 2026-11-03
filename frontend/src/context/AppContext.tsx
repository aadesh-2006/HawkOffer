import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { HealthResponse, SystemStatusResponse } from "../types/api";
import { notificationsApi, systemApi } from "../services/api";

export type TabId =
  | "dashboard"
  | "jobs"
  | "companies"
  | "profiles"
  | "notifications"
  | "monitoring"
  | "settings";

export type Theme = "light" | "dark" | "system";

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

  // Backend Connectivity
  isOnline: boolean;
  isConnecting: boolean;
  lastChecked: Date | null;
  health: HealthResponse | null;
  systemStatus: SystemStatusResponse | null;
  checkConnection: () => Promise<void>;

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

  const [isOnline, setIsOnline] = useState<boolean>(true);
  const [isConnecting, setIsConnecting] = useState<boolean>(true);
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

  const checkConnection = useCallback(async () => {
    setIsConnecting(true);
    try {
      const [h, s] = await Promise.all([systemApi.getHealth(), systemApi.getStatus()]);
      setHealth(h);
      setSystemStatus(s);
      setIsOnline(true);
      setUnreadCount(s.unread_notifications_count);
      setLastChecked(new Date());
    } catch {
      setIsOnline(false);
      setHealth(null);
      setSystemStatus(null);
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([checkConnection(), refreshUnreadCount()]);
  }, [checkConnection, refreshUnreadCount]);

  // Initial load and periodic heartbeats (every 30s)
  useEffect(() => {
    refreshAll();
    const interval = setInterval(refreshAll, 30000);
    return () => clearInterval(interval);
  }, [refreshAll]);

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
        lastChecked,
        health,
        systemStatus,
        checkConnection,
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
