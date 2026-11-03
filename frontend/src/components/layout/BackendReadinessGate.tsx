import React from "react";
import {
  RotateCw,
  Terminal,
  ServerCrash,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { Button } from "../common/Button";
import { LoadingSpinner } from "../common/LoadingSpinner";

export const BackendReadinessGate: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const {
    readinessState,
    readinessMessage,
    retryReadiness,
    skipReadinessGate,
    isConnecting,
  } = useApp();

  if (readinessState === "ready" || readinessState === "skipped") {
    return <>{children}</>;
  }

  return (
    <div
      className="min-h-screen w-screen bg-slate-900 text-slate-100 flex flex-col items-center justify-center p-6 select-none font-sans"
      data-testid="readiness-gate"
    >
      <div className="max-w-md w-full bg-slate-800/90 border border-slate-700/80 rounded-2xl p-8 shadow-2xl backdrop-blur-md space-y-6 text-center">
        {/* Brand Icon */}
        <div className="flex justify-center">
          <div className="w-16 h-16 rounded-2xl bg-teal-600 flex items-center justify-center text-white shadow-lg text-2xl font-bold ring-4 ring-teal-500/20">
            H
          </div>
        </div>

        {/* Title */}
        <div className="space-y-1">
          <h2 className="text-xl font-bold text-white tracking-tight">
            HawkOffer Desktop
          </h2>
          <p className="text-xs text-slate-400">
            Local Career Monitoring &amp; Ingestion Engine
          </p>
        </div>

        {/* Checking State */}
        {readinessState === "checking" && (
          <div className="space-y-4 py-4" data-testid="readiness-checking">
            <LoadingSpinner size="lg" label="" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-teal-300">
                Verifying Local Runtime...
              </p>
              <p className="text-xs text-slate-400 font-mono text-center">
                {readinessMessage}
              </p>
            </div>
            <div className="w-full bg-slate-700 h-1.5 rounded-full overflow-hidden">
              <div className="bg-teal-500 h-full w-2/3 animate-pulse rounded-full" />
            </div>
          </div>
        )}

        {/* Failed State */}
        {readinessState === "failed" && (
          <div className="space-y-5 text-left" data-testid="readiness-failed">
            <div className="flex items-center gap-3 p-3.5 bg-rose-950/50 border border-rose-800/80 rounded-xl text-rose-200 text-xs">
              <ServerCrash className="w-5 h-5 text-rose-400 flex-shrink-0" />
              <div>
                <p className="font-semibold text-rose-300">
                  Backend Daemon Offline
                </p>
                <p className="mt-0.5 text-slate-300">
                  Could not establish readiness on{" "}
                  <code className="text-rose-300 font-mono">127.0.0.1:8765</code>
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
                <Terminal className="w-3.5 h-3.5 text-teal-400" />
                Launch Daemon in Terminal:
              </p>
              <pre className="p-3 bg-slate-950 text-teal-300 rounded-lg text-xs font-mono overflow-x-auto border border-slate-800">
                cd backend
.\.venv\Scripts\python.exe -m app.desktop_runtime start
              </pre>
            </div>

            <div className="pt-2 flex flex-col sm:flex-row items-center gap-2.5">
              <Button
                variant="primary"
                size="md"
                className="w-full sm:flex-1"
                onClick={retryReadiness}
                isLoading={isConnecting}
                leftIcon={<RotateCw className="w-4 h-4" />}
                data-testid="btn-retry-readiness"
              >
                Retry Connection
              </Button>
              <Button
                variant="ghost"
                size="md"
                className="w-full sm:w-auto text-slate-400 hover:text-white"
                onClick={skipReadinessGate}
                data-testid="btn-skip-readiness"
              >
                Offline View
              </Button>
            </div>
          </div>
        )}

        <div className="text-[11px] text-slate-500 text-center border-t border-slate-700/60 pt-4">
          Localhost Only &bull; SQLite Persistence &bull; Zero Cloud Leakage
        </div>
      </div>
    </div>
  );
};
