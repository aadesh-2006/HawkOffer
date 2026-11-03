import React from "react";
import { RefreshCw, Moon, Sun, Monitor, Wifi, WifiOff } from "lucide-react";
import { useApp, TabId } from "../../context/AppContext";
import { Button } from "../common/Button";

const TAB_TITLES: Record<TabId, { title: string; subtitle: string }> = {
  dashboard: {
    title: "System Dashboard",
    subtitle: "Real-time overview of career monitoring, discovery, and notification status.",
  },
  jobs: {
    title: "Jobs Feed",
    subtitle: "Explore, filter, and inspect newly discovered and updated job postings.",
  },
  companies: {
    title: "Monitored Companies",
    subtitle: "Configure career sites, platforms, and trigger immediate career scans.",
  },
  profiles: {
    title: "Preference Profiles",
    subtitle: "Define matching criteria. Updating enqueues background historical reevaluation.",
  },
  notifications: {
    title: "Notification Inbox",
    subtitle: "Review promoted job opportunities matching your active preference profiles.",
  },
  monitoring: {
    title: "Monitoring Pipeline",
    subtitle: "Direct control over discovery, reevaluation, backlog drain, and scheduler status.",
  },
  settings: {
    title: "Settings & Connectivity",
    subtitle: "Configure backend API endpoints, theme preferences, and system diagnostics.",
  },
};

export const Header: React.FC = () => {
  const {
    activeTab,
    isOnline,
    isConnecting,
    checkConnection,
    theme,
    setTheme,
    refreshAll,
  } = useApp();

  const [refreshing, setRefreshing] = React.useState(false);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refreshAll();
    } finally {
      setRefreshing(false);
    }
  };

  const cycleTheme = () => {
    if (theme === "light") setTheme("dark");
    else if (theme === "dark") setTheme("system");
    else setTheme("light");
  };

  const { title, subtitle } = TAB_TITLES[activeTab] || {
    title: "HawkOffer",
    subtitle: "Desktop Career Monitor",
  };

  return (
    <header className="h-16 px-6 bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
      <div>
        <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100 tracking-tight leading-tight">
          {title}
        </h1>
        <p className="text-xs text-slate-600 dark:text-slate-400 hidden sm:block">{subtitle}</p>
      </div>

      <div className="flex items-center gap-3">
        {/* Backend Connection Pill */}
        <div
          data-testid="connection-status-pill"
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
            isOnline
              ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800"
              : "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-800"
          }`}
        >
          {isOnline ? (
            <Wifi className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <WifiOff className="w-3.5 h-3.5 text-rose-600 dark:text-rose-400" />
          )}
          <span>{isOnline ? "Connected" : "API Offline"}</span>
          <button
            onClick={() => checkConnection()}
            disabled={isConnecting}
            title="Ping backend"
            className="ml-1 hover:text-slate-900 dark:hover:text-white"
            aria-label="Ping backend"
          >
            <RefreshCw className={`w-3 h-3 ${isConnecting ? "animate-spin" : ""}`} />
          </button>
        </div>

        {/* Global Refresh Button */}
        <Button
          variant="secondary"
          size="sm"
          onClick={handleRefresh}
          isLoading={refreshing}
          leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          data-testid="header-refresh-button"
        >
          Refresh
        </Button>

        {/* Theme Cycle Button */}
        <button
          onClick={cycleTheme}
          title={`Current theme: ${theme}. Click to switch.`}
          className="p-2 rounded-lg text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 border border-slate-200 dark:border-slate-800 transition-colors"
          aria-label="Toggle theme"
          data-testid="theme-toggle-button"
        >
          {theme === "light" && <Sun className="w-4 h-4 text-amber-500" />}
          {theme === "dark" && <Moon className="w-4 h-4 text-teal-400" />}
          {theme === "system" && <Monitor className="w-4 h-4 text-slate-500" />}
        </button>
      </div>
    </header>
  );
};
