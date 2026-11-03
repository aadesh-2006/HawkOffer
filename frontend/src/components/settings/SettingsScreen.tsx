import React, { useState } from "react";
import {
  Server,
  Database,
  Moon,
  Sun,
  Monitor,
  Terminal,
  RotateCcw,
  Copy,
  Check,
} from "lucide-react";
import { useApp, Theme } from "../../context/AppContext";
import { getApiBaseUrl, setApiBaseUrl } from "../../services/api";
import { Button } from "../common/Button";
import { Badge } from "../common/Badge";

export const SettingsScreen: React.FC = () => {
  const {
    theme,
    setTheme,
    isOnline,
    health,
    systemStatus,
    checkConnection,
    showToast,
  } = useApp();

  const [baseUrlInput, setBaseUrlInput] = useState<string>(getApiBaseUrl());
  const [isTestingUrl, setIsTestingUrl] = useState<boolean>(false);
  const [copiedSnippet, setCopiedSnippet] = useState<boolean>(false);

  const handleSaveBaseUrl = async () => {
    try {
      setApiBaseUrl(baseUrlInput.trim());
      showToast("API Base URL saved.", "success");
      await checkConnection();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update API URL";
      showToast(msg, "error");
    }
  };

  const handleResetBaseUrl = async () => {
    const defaultUrl = "http://127.0.0.1:8765/api/v1";
    setBaseUrlInput(defaultUrl);
    setApiBaseUrl(defaultUrl);
    showToast("API Base URL reset to default.", "info");
    await checkConnection();
  };

  const handleTestConnection = async () => {
    setIsTestingUrl(true);
    try {
      await checkConnection();
      showToast("Backend connection verified successfully.", "success");
    } catch {
      showToast("Could not reach backend API at specified endpoint.", "error");
    } finally {
      setIsTestingUrl(false);
    }
  };

  const runCommand = "cd backend\n.\\.venv\\Scripts\\python.exe -m app.desktop_runtime start";

  const copyDaemonCommand = () => {
    navigator.clipboard.writeText(runCommand);
    setCopiedSnippet(true);
    showToast("Command copied to clipboard", "info");
    setTimeout(() => setCopiedSnippet(false), 2000);
  };

  const isDbConnected = health?.database === "connected";
  const isSchedulerRunning = systemStatus?.is_running ?? (health?.scheduler === "running");

  return (
    <div className="space-y-6 max-w-4xl">
      {/* API Endpoint Configuration */}
      <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4 shadow-xs">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Server className="w-5 h-5 text-teal-600 dark:text-teal-400" />
            Backend API Endpoint
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Configure the local REST service URL for HawkOffer desktop connectivity.
          </p>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              API Base URL
            </label>
            <input
              type="text"
              value={baseUrlInput}
              onChange={(e) => setBaseUrlInput(e.target.value)}
              className="w-full text-sm font-mono px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="input-base-url"
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <div className="flex items-center gap-2">
              <Button
                variant="primary"
                size="sm"
                onClick={handleSaveBaseUrl}
                data-testid="btn-save-base-url"
              >
                Save Endpoint
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleTestConnection}
                isLoading={isTestingUrl}
                data-testid="btn-test-connection"
              >
                Test Connection
              </Button>
            </div>

            <Button
              variant="ghost"
              size="sm"
              onClick={handleResetBaseUrl}
              leftIcon={<RotateCcw className="w-3.5 h-3.5" />}
            >
              Reset to Default (127.0.0.1:8765)
            </Button>
          </div>
        </div>
      </div>

      {/* Backend Diagnostics Card */}
      <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4 shadow-xs">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Database className="w-5 h-5 text-teal-600 dark:text-teal-400" />
            Runtime Diagnostics &amp; Health
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Live health checks and state verification from the local application instance.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
          <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg flex items-center justify-between">
            <span className="text-slate-600 dark:text-slate-400 font-medium">Service Status:</span>
            <Badge variant={isOnline ? "success" : "danger"} size="sm">
              {isOnline ? "ONLINE" : "OFFLINE"}
            </Badge>
          </div>

          <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg flex items-center justify-between">
            <span className="text-slate-600 dark:text-slate-400 font-medium">Database Connected:</span>
            <Badge
              variant={isDbConnected ? "success" : "warning"}
              size="sm"
            >
              {isDbConnected ? "SQLITE CONNECTED" : "DISCONNECTED"}
            </Badge>
          </div>

          <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg flex items-center justify-between">
            <span className="text-slate-600 dark:text-slate-400 font-medium">Overall Health:</span>
            <Badge
              variant={health?.status === "healthy" ? "success" : "warning"}
              size="sm"
            >
              {health?.status ? health.status.toUpperCase() : "UNKNOWN"}
            </Badge>
          </div>

          <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg flex items-center justify-between">
            <span className="text-slate-600 dark:text-slate-400 font-medium">APScheduler Engine:</span>
            <Badge
              variant={isSchedulerRunning ? "success" : "neutral"}
              size="sm"
            >
              {isSchedulerRunning ? "RUNNING" : "STOPPED"}
            </Badge>
          </div>
        </div>
      </div>

      {/* Theme Appearance Preferences */}
      <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4 shadow-xs">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Sun className="w-5 h-5 text-amber-500" />
            Appearance &amp; Theme
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Select desktop color theme or synchronize with operating system settings.
          </p>
        </div>

        <div className="grid grid-cols-3 gap-3">
          {(
            [
              { id: "light", label: "Light", icon: Sun },
              { id: "dark", label: "Dark", icon: Moon },
              { id: "system", label: "System Sync", icon: Monitor },
            ] as { id: Theme; label: string; icon: React.ComponentType<{ className?: string }> }[]
          ).map((t) => {
            const Icon = t.icon;
            const isSelected = theme === t.id;

            return (
              <button
                key={t.id}
                onClick={() => setTheme(t.id)}
                className={`p-4 rounded-xl border flex flex-col items-center justify-center gap-2 transition-all ${
                  isSelected
                    ? "border-teal-600 bg-teal-50 dark:bg-teal-950/40 text-teal-700 dark:text-teal-300 font-semibold shadow-xs"
                    : "border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:border-slate-300 dark:hover:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800/40"
                }`}
                data-testid={`theme-option-${t.id}`}
              >
                <Icon className={`w-5 h-5 ${isSelected ? "text-teal-600" : ""}`} />
                <span className="text-xs">{t.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Local Backend Daemon Instructions */}
      <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-3 shadow-xs">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Terminal className="w-5 h-5 text-teal-600 dark:text-teal-400" />
            Desktop Runtime Supervisor
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            The desktop supervisor manages single-instance PID locking, database readiness verification, and graceful shutdown:
          </p>
        </div>

        <div className="relative group">
          <pre className="p-3.5 bg-slate-950 text-slate-200 rounded-lg text-xs font-mono overflow-x-auto selection:bg-teal-900 leading-relaxed border border-slate-800">
            {runCommand}
          </pre>

          <button
            onClick={copyDaemonCommand}
            className="absolute top-2.5 right-2.5 px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-md text-xs font-medium flex items-center gap-1.5 transition-colors border border-slate-700"
            title="Copy command to clipboard"
          >
            {copiedSnippet ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                <span>Copied!</span>
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
