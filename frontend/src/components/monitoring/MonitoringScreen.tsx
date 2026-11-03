import React, { useEffect, useState, useCallback } from "react";
import {
  Activity,
  Play,
  RotateCw,
  CheckCircle2,
  Building2,
  Pause,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import {
  MonitoringStatusResponse,
  WorkerTriggerResponse,
  WorkerScheduleInfo,
} from "../../types/api";
import { monitoringApi } from "../../services/api";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";

export const MonitoringScreen: React.FC = () => {
  const { showToast, refreshAll } = useApp();

  const [status, setStatus] = useState<MonitoringStatusResponse | null>(null);
  const [, setIsLoading] = useState<boolean>(true);

  // Trigger loading states
  const [isTriggeringDiscovery, setIsTriggeringDiscovery] = useState<boolean>(false);
  const [isTriggeringReevaluation, setIsTriggeringReevaluation] = useState<boolean>(false);
  const [isTriggeringDrain, setIsTriggeringDrain] = useState<boolean>(false);

  // Latest execution result output card
  const [lastExecutionLog, setLastExecutionLog] = useState<{
    name: string;
    timestamp: Date;
    details: Record<string, unknown>;
  } | null>(null);

  const fetchStatus = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await monitoringApi.getStatus();
      setStatus(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load monitoring status";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleTriggerDiscovery = async () => {
    setIsTriggeringDiscovery(true);
    try {
      const res: WorkerTriggerResponse = await monitoringApi.triggerDiscovery();
      setLastExecutionLog({
        name: "Job Discovery Worker",
        timestamp: new Date(),
        details: res.result || {},
      });
      const claimed = res.result?.claimed_count ?? 0;
      const promoted = res.result?.promoted_count ?? 0;
      showToast(
        `Job discovery completed: claimed ${claimed}, promoted ${promoted} events.`,
        "success"
      );
      await fetchStatus();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to trigger discovery worker";
      showToast(msg, "error");
    } finally {
      setIsTriggeringDiscovery(false);
    }
  };

  const handleTriggerReevaluation = async () => {
    setIsTriggeringReevaluation(true);
    try {
      const res: WorkerTriggerResponse = await monitoringApi.triggerReevaluation();
      setLastExecutionLog({
        name: "Profile Reevaluation Worker",
        timestamp: new Date(),
        details: res.result || {},
      });
      const claimed = res.result?.claimed_count ?? 0;
      const promoted = res.result?.promoted_count ?? 0;
      showToast(
        `Reevaluation completed: claimed ${claimed}, promoted ${promoted} backlog items.`,
        "success"
      );
      await fetchStatus();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to trigger reevaluation worker";
      showToast(msg, "error");
    } finally {
      setIsTriggeringReevaluation(false);
    }
  };

  const handleTriggerDrain = async () => {
    setIsTriggeringDrain(true);
    try {
      const res: WorkerTriggerResponse = await monitoringApi.triggerDrain();
      setLastExecutionLog({
        name: "Backlog Drain Worker",
        timestamp: new Date(),
        details: res.result || {},
      });
      const slots = res.result?.allocated_slots ?? 0;
      const promoted = res.result?.promoted_count ?? 0;
      showToast(
        `Backlog drain completed: allocated ${slots} slots, promoted ${promoted} notifications.`,
        "success"
      );
      await fetchStatus();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to trigger drain worker";
      showToast(msg, "error");
    } finally {
      setIsTriggeringDrain(false);
    }
  };

  const handleToggleJob = async (jobName: string, isCurrentlyPaused: boolean) => {
    try {
      if (isCurrentlyPaused) {
        await monitoringApi.resume({ job_name: jobName });
        showToast(`Resumed "${jobName}".`, "info");
      } else {
        await monitoringApi.pause({ job_name: jobName });
        showToast(`Paused "${jobName}".`, "info");
      }
      await fetchStatus();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to toggle job";
      showToast(msg, "error");
    }
  };

  const pipeline = status?.pipeline_status || {};
  const discoveryInfo: WorkerScheduleInfo | undefined = pipeline["job_discovery_worker"];
  const reevaluationInfo: WorkerScheduleInfo | undefined = pipeline["profile_reevaluation_worker"];
  const drainInfo: WorkerScheduleInfo | undefined = pipeline["backlog_drain_worker"];

  // Find dynamic company scan jobs (names starting with 'company_scan_')
  const companyScanJobs = Object.entries(pipeline).filter(([key]) =>
    key.startsWith("company_scan_")
  );

  return (
    <div className="space-y-6">
      {/* Engine Status Hero */}
      <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                <Activity className="w-5 h-5 text-teal-600 dark:text-teal-400" />
                Scheduler &amp; Pipeline Engine
              </h3>
              <Badge
                variant={status?.is_running ? "success" : "neutral"}
                size="md"
              >
                {status?.is_running ? "APScheduler RUNNING" : "STOPPED"}
              </Badge>
              <Badge
                variant={status?.is_lifecycle_gate_open ? "brand" : "warning"}
                size="md"
              >
                {status?.is_lifecycle_gate_open ? "Gate OPEN" : "Gate CLOSED"}
              </Badge>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              The lifecycle gate synchronizes scheduler startup, active scans, and graceful shutdown.
            </p>
          </div>

          <Button
            variant="secondary"
            size="sm"
            onClick={fetchStatus}
            leftIcon={<RotateCw className="w-3.5 h-3.5" />}
          >
            Refresh State
          </Button>
        </div>
      </div>

      {/* Workers Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* Job Discovery Card */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 space-y-4 shadow-xs flex flex-col justify-between">
          <div className="space-y-3">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="font-bold text-sm text-slate-900 dark:text-slate-100">
                  Job Discovery Worker
                </h4>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Claims unhandled outbox events and matches against active profiles.
                </p>
              </div>
              <Badge
                variant={discoveryInfo?.scheduled ? "success" : "neutral"}
                size="sm"
              >
                {discoveryInfo?.scheduled ? (discoveryInfo.paused ? "Paused" : "Active") : "Idle"}
              </Badge>
            </div>

            <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg space-y-1.5 text-xs">
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Job ID:</span>
                <span className="font-mono text-slate-900 dark:text-slate-200">
                  job_discovery_worker
                </span>
              </div>
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Next Scheduled:</span>
                <span className="font-mono text-slate-900 dark:text-slate-200">
                  {discoveryInfo?.next_run_time
                    ? new Date(discoveryInfo.next_run_time).toLocaleTimeString()
                    : "Not scheduled"}
                </span>
              </div>
            </div>
          </div>

          <div className="pt-2 flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              className="flex-1"
              onClick={handleTriggerDiscovery}
              isLoading={isTriggeringDiscovery}
              leftIcon={<Play className="w-3.5 h-3.5" />}
              data-testid="btn-trigger-discovery"
            >
              Run Now
            </Button>
            {discoveryInfo && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleToggleJob("discovery", Boolean(discoveryInfo.paused))}
                title={discoveryInfo.paused ? "Resume" : "Pause"}
              >
                {discoveryInfo.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              </Button>
            )}
          </div>
        </div>

        {/* Profile Reevaluation Card */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 space-y-4 shadow-xs flex flex-col justify-between">
          <div className="space-y-3">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="font-bold text-sm text-slate-900 dark:text-slate-100">
                  Profile Reevaluation
                </h4>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Reevaluates modified profile revisions across all past indexed jobs.
                </p>
              </div>
              <Badge
                variant={reevaluationInfo?.scheduled ? "success" : "neutral"}
                size="sm"
              >
                {reevaluationInfo?.scheduled ? (reevaluationInfo.paused ? "Paused" : "Active") : "Idle"}
              </Badge>
            </div>

            <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg space-y-1.5 text-xs">
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Job ID:</span>
                <span className="font-mono text-slate-900 dark:text-slate-200">
                  profile_reevaluation_worker
                </span>
              </div>
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Next Scheduled:</span>
                <span className="font-mono text-slate-900 dark:text-slate-200">
                  {reevaluationInfo?.next_run_time
                    ? new Date(reevaluationInfo.next_run_time).toLocaleTimeString()
                    : "Not scheduled"}
                </span>
              </div>
            </div>
          </div>

          <div className="pt-2 flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              className="flex-1"
              onClick={handleTriggerReevaluation}
              isLoading={isTriggeringReevaluation}
              leftIcon={<Play className="w-3.5 h-3.5" />}
              data-testid="btn-trigger-reevaluation"
            >
              Run Now
            </Button>
            {reevaluationInfo && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleToggleJob("reevaluation", Boolean(reevaluationInfo.paused))}
                title={reevaluationInfo.paused ? "Resume" : "Pause"}
              >
                {reevaluationInfo.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              </Button>
            )}
          </div>
        </div>

        {/* Backlog Drain Card */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 space-y-4 shadow-xs flex flex-col justify-between">
          <div className="space-y-3">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="font-bold text-sm text-slate-900 dark:text-slate-100">
                  Backlog Drain Worker
                </h4>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Fairness-aware sequential slot allocator promoting candidates to notifications.
                </p>
              </div>
              <Badge
                variant={drainInfo?.scheduled ? "success" : "neutral"}
                size="sm"
              >
                {drainInfo?.scheduled ? (drainInfo.paused ? "Paused" : "Active") : "Idle"}
              </Badge>
            </div>

            <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg space-y-1.5 text-xs">
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Job ID:</span>
                <span className="font-mono text-slate-900 dark:text-slate-200">
                  backlog_drain_worker
                </span>
              </div>
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Next Scheduled:</span>
                <span className="font-mono text-slate-900 dark:text-slate-200">
                  {drainInfo?.next_run_time
                    ? new Date(drainInfo.next_run_time).toLocaleTimeString()
                    : "Not scheduled"}
                </span>
              </div>
            </div>
          </div>

          <div className="pt-2 flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              className="flex-1"
              onClick={handleTriggerDrain}
              isLoading={isTriggeringDrain}
              leftIcon={<Play className="w-3.5 h-3.5" />}
              data-testid="btn-trigger-drain"
            >
              Run Now
            </Button>
            {drainInfo && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleToggleJob("drain", Boolean(drainInfo.paused))}
                title={drainInfo.paused ? "Resume" : "Pause"}
              >
                {drainInfo.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Latest Execution Log Output */}
      {lastExecutionLog && (
        <div
          className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 space-y-2 shadow-xs"
          data-testid="execution-result-card"
        >
          <div className="flex items-center justify-between text-xs font-semibold text-slate-700 dark:text-slate-300">
            <span className="flex items-center gap-1.5 text-teal-600 dark:text-teal-400">
              <CheckCircle2 className="w-4 h-4" />
              Latest Execution: {lastExecutionLog.name}
            </span>
            <span className="text-slate-400">
              {lastExecutionLog.timestamp.toLocaleTimeString()}
            </span>
          </div>

          <pre className="p-3 bg-slate-900 text-teal-300 rounded-lg font-mono text-xs overflow-x-auto">
            {JSON.stringify(lastExecutionLog.details, null, 2)}
          </pre>
        </div>
      )}

      {/* Dynamic Company Schedules List */}
      {companyScanJobs.length > 0 && (
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden shadow-xs">
          <div className="p-4 bg-slate-50 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <Building2 className="w-4 h-4 text-teal-600 dark:text-teal-400" />
              Scheduled Company Scans
            </h4>
            <Badge variant="neutral" size="sm">
              {companyScanJobs.length} active schedules
            </Badge>
          </div>

          <div className="divide-y divide-slate-100 dark:divide-slate-800 text-xs">
            {companyScanJobs.map(([jobKey, info]) => {
              const scheduleInfo = info as WorkerScheduleInfo;
              return (
                <div
                  key={jobKey}
                  className="p-3.5 flex items-center justify-between gap-4"
                >
                  <div className="space-y-0.5">
                    <span className="font-semibold text-slate-900 dark:text-slate-200">
                      {jobKey}
                    </span>
                    <div className="text-[11px] text-slate-400">
                      Scheduled: {scheduleInfo.scheduled ? "Yes" : "No"} &bull; Paused: {scheduleInfo.paused ? "Yes" : "No"}
                    </div>
                  </div>

                  <div className="text-right">
                    <div className="font-mono text-slate-700 dark:text-slate-300">
                      Next: {scheduleInfo.next_run_time ? new Date(scheduleInfo.next_run_time).toLocaleTimeString() : "—"}
                    </div>
                    <Badge variant={scheduleInfo.paused ? "warning" : "success"} size="sm">
                      {scheduleInfo.paused ? "PAUSED" : "SCHEDULED"}
                    </Badge>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
