import React from "react";
import { RefreshCw, Moon, Sun, Monitor, Wifi, WifiOff, Sparkles, Download } from "lucide-react";
import { useApp, TabId } from "../../context/AppContext";
import { Button } from "../common/Button";
import { systemApi } from "../../services/api";
import { ModelStatusInfo } from "../../types/api";

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
  const [modelStatus, setModelStatus] = React.useState<ModelStatusInfo | null>(null);
  const [isDownloadingModel, setIsDownloadingModel] = React.useState(false);

  const fetchModelStatus = React.useCallback(async () => {
    if (!isOnline) return;
    try {
      const status = await systemApi.getSemanticModelStatus();
      setModelStatus(status);
    } catch {
      // Non-fatal
    }
  }, [isOnline]);

  React.useEffect(() => {
    fetchModelStatus();
  }, [fetchModelStatus]);

  const handleDownloadModel = async () => {
    setIsDownloadingModel(true);
    try {
      const res = await systemApi.downloadSemanticModel();
      setModelStatus(res);
    } catch (err) {
      console.error("Failed to trigger model download:", err);
    } finally {
      setIsDownloadingModel(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([refreshAll(), fetchModelStatus()]);
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

        {/* AI Model Status Pill */}
        {isOnline && modelStatus && (
          <div
            data-testid="ai-model-status-pill"
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
              modelStatus.status === "READY"
                ? "bg-violet-50 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-800"
                : modelStatus.status === "DOWNLOADING"
                ? "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800"
                : "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 text-violet-600 dark:text-violet-400" />
            <span>
              {modelStatus.status === "READY"
                ? `Local AI: ONNX (${modelStatus.model_name})`
                : modelStatus.status === "DOWNLOADING"
                ? `Downloading AI Model (${Math.round(modelStatus.download_progress * 100)}%)`
                : "Local AI: Fallback (Hash)"}
            </span>
            {modelStatus.status === "NOT_DOWNLOADED" && (
              <button
                onClick={handleDownloadModel}
                disabled={isDownloadingModel}
                className="ml-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-violet-600 text-white text-[10px] font-semibold hover:bg-violet-700 transition-colors"
                title="Download 22MB ONNX model for high-precision semantic matching"
              >
                <Download className="w-2.5 h-2.5" />
                {isDownloadingModel ? "Fetching..." : "Download ONNX"}
              </button>
            )}
          </div>
        )}

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
