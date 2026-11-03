import React, { useEffect, useState, useMemo } from "react";
import {
  Building2,
  Briefcase,
  Bell,
  ArrowRight,
  Play,
  RotateCw,
  ExternalLink,
  ShieldCheck,
  Clock,
  Sparkles,
  Bookmark,
  TrendingUp,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { useAuth } from "../../context/AuthContext";
import {
  ApplicationRead,
  CompanyRead,
  JobRead,
  MonitoringStatusResponse,
} from "../../types/api";
import {
  applicationsApi,
  companiesApi,
  jobsApi,
  monitoringApi,
  subscriptionsApi,
} from "../../services/api";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EmptyState } from "../common/EmptyState";
import { FirstRunGuide } from "./FirstRunGuide";
import { PushNotificationBanner } from "./PushNotificationBanner";

export const DashboardScreen: React.FC = () => {
  const { systemStatus, setActiveTab, setSelectedJobId, showToast, refreshAll, isOnline } =
    useApp();
  const { user, isAuthenticated, openAuthModal } = useAuth();

  const [recentJobs, setRecentJobs] = useState<JobRead[]>([]);
  const [companies, setCompanies] = useState<CompanyRead[]>([]);
  const [followedCount, setFollowedCount] = useState<number>(0);
  const [applications, setApplications] = useState<ApplicationRead[]>([]);
  const [monitoringDetails, setMonitoringDetails] = useState<MonitoringStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isTriggeringDiscovery, setIsTriggeringDiscovery] = useState<boolean>(false);
  const [isTriggeringDrain, setIsTriggeringDrain] = useState<boolean>(false);

  useEffect(() => {
    let isMounted = true;

    const loadDashboardData = async () => {
      setIsLoading(true);
      try {
        const [jobsRes, monRes, compsRes, subsRes, appsRes] = await Promise.allSettled([
          jobsApi?.list ? jobsApi.list({ limit: 6 }) : Promise.resolve({ items: [], total: 0 }),
          monitoringApi?.getStatus ? monitoringApi.getStatus() : Promise.resolve(null),
          companiesApi?.list ? companiesApi.list({ limit: 100 }) : Promise.resolve({ items: [], total: 0 }),
          isAuthenticated && subscriptionsApi?.list ? subscriptionsApi.list() : Promise.resolve([]),
          isAuthenticated && applicationsApi?.list ? applicationsApi.list() : Promise.resolve([]),
        ]);

        if (!isMounted) return;

        if (jobsRes.status === "fulfilled" && jobsRes.value?.items) {
          setRecentJobs(jobsRes.value.items);
        }
        if (monRes.status === "fulfilled" && monRes.value) {
          setMonitoringDetails(monRes.value);
        }
        if (compsRes.status === "fulfilled" && compsRes.value?.items) {
          setCompanies(compsRes.value.items);
        }
        if (subsRes.status === "fulfilled" && Array.isArray(subsRes.value)) {
          setFollowedCount(subsRes.value.length);
        }
        if (appsRes.status === "fulfilled" && Array.isArray(appsRes.value)) {
          setApplications(appsRes.value);
        }
      } catch (err: unknown) {
        if (isMounted) {
          const msg = err instanceof Error ? err.message : "Failed to load dashboard data";
          showToast(msg, "error");
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    loadDashboardData();

    return () => {
      isMounted = false;
    };
  }, [isAuthenticated, showToast]);

  const companyMap = useMemo(() => {
    const map = new Map<number, string>();
    companies.forEach((c) => map.set(c.id, c.name));
    return map;
  }, [companies]);

  // Stage counts for application pipeline
  const appStageCounts = useMemo(() => {
    const counts = { saved: 0, applied: 0, interviewing: 0, offer: 0 };
    applications.forEach((a) => {
      const s = a.status.toUpperCase();
      if (s === "SAVED") counts.saved++;
      else if (s === "APPLIED") counts.applied++;
      else if (s === "INTERVIEWING") counts.interviewing++;
      else if (s === "OFFER") counts.offer++;
    });
    return counts;
  }, [applications]);

  const handleTriggerDiscovery = async () => {
    setIsTriggeringDiscovery(true);
    try {
      const res = await monitoringApi.triggerDiscovery();
      const claimed = res.result?.claimed_count ?? 0;
      const promoted = res.result?.promoted_count ?? 0;
      showToast(
        `Job discovery completed: claimed ${claimed}, promoted ${promoted} events.`,
        "success"
      );
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Discovery trigger failed";
      showToast(msg, "error");
    } finally {
      setIsTriggeringDiscovery(false);
    }
  };

  const handleTriggerDrain = async () => {
    setIsTriggeringDrain(true);
    try {
      const res = await monitoringApi.triggerDrain();
      const slots = res.result?.allocated_slots ?? 0;
      const promoted = res.result?.promoted_count ?? 0;
      showToast(
        `Backlog drain completed: allocated ${slots} slots, promoted ${promoted} notifications.`,
        "success"
      );
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Drain trigger failed";
      showToast(msg, "error");
    } finally {
      setIsTriggeringDrain(false);
    }
  };

  const pipeline = monitoringDetails?.pipeline_status || systemStatus?.pipeline_status || {};

  const discoveryInfo = pipeline["job_discovery_worker"];
  const reevaluationInfo = pipeline["profile_reevaluation_worker"];
  const drainInfo = pipeline["backlog_drain_worker"];

  return (
    <div className="space-y-6" data-testid="dashboard-screen">
      {/* Offline Alert Banner */}
      {!isOnline && (
        <div className="p-4 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-lg text-amber-900 dark:text-amber-200 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-semibold">Backend Unreachable:</span>
            <span>Running in offline view. Check daemon at 127.0.0.1:8765.</span>
          </div>
          <Button size="sm" variant="outline" onClick={() => refreshAll()}>
            Retry Connection
          </Button>
        </div>
      )}

      {/* Browser Web Push Onboarding Banner */}
      <PushNotificationBanner />

      {/* User Greeting & SaaS Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-xs">
        <div className="space-y-1">
          <h2 className="text-xl font-bold text-slate-900 dark:text-slate-100 tracking-tight">
            {isAuthenticated
              ? `Welcome back, ${user?.full_name || user?.email.split("@")[0]}!`
              : "Career Intelligence Dashboard"}
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {isAuthenticated
              ? "Your automated career intelligence engine is actively monitoring target portals in the background."
              : "Public SaaS job monitoring platform. Follow target companies, get instant alerts, and track your applications."}
          </p>
        </div>

        {!isAuthenticated && (
          <div className="flex items-center gap-2.5">
            <Button variant="outline" size="sm" onClick={() => openAuthModal("login")}>
              Sign In
            </Button>
            <Button variant="primary" size="sm" onClick={() => openAuthModal("register")}>
              Create Account
            </Button>
          </div>
        )}
      </div>

      {/* First-Run Onboarding Guide for New Deployments */}
      {systemStatus?.companies_count === 0 && <FirstRunGuide />}

      {/* KPI Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Monitored & Followed Companies */}
        <div
          onClick={() => setActiveTab("companies")}
          className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 hover:border-teal-500/50 cursor-pointer transition-all shadow-xs group"
          data-testid="stat-card-companies"
        >
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-xs font-semibold uppercase tracking-wider">Companies</span>
            <Building2 className="w-5 h-5 text-teal-600 dark:text-teal-400 group-hover:scale-110 transition-transform" />
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              {systemStatus?.companies_count ?? "—"}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">
              {isAuthenticated ? `(${followedCount} followed)` : `(${systemStatus?.active_companies_count ?? 0} active)`}
            </span>
          </div>
          <div className="mt-2 text-xs text-teal-600 dark:text-teal-400 flex items-center gap-1 font-medium group-hover:underline">
            Browse company directory <ArrowRight className="w-3 h-3" />
          </div>
        </div>

        {/* Total Discovered Jobs */}
        <div
          onClick={() => setActiveTab("jobs")}
          className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 hover:border-teal-500/50 cursor-pointer transition-all shadow-xs group"
          data-testid="stat-card-jobs"
        >
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-xs font-semibold uppercase tracking-wider">Discovered Jobs</span>
            <Briefcase className="w-5 h-5 text-teal-600 dark:text-teal-400 group-hover:scale-110 transition-transform" />
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              {systemStatus?.total_jobs_count ?? "—"}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">ingested</span>
          </div>
          <div className="mt-2 text-xs text-teal-600 dark:text-teal-400 flex items-center gap-1 font-medium group-hover:underline">
            Browse matching feed <ArrowRight className="w-3 h-3" />
          </div>
        </div>

        {/* Tracked Applications Pipeline */}
        <div
          onClick={() => setActiveTab("applications")}
          className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 hover:border-teal-500/50 cursor-pointer transition-all shadow-xs group"
          data-testid="stat-card-applications"
        >
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-xs font-semibold uppercase tracking-wider">Applications</span>
            <Bookmark className="w-5 h-5 text-teal-600 dark:text-teal-400 group-hover:scale-110 transition-transform" />
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              {applications.length}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">
              tracked
            </span>
          </div>
          <div className="mt-2 text-xs text-teal-600 dark:text-teal-400 flex items-center gap-1 font-medium group-hover:underline">
            Manage pipeline board <ArrowRight className="w-3 h-3" />
          </div>
        </div>

        {/* Unread Notifications */}
        <div
          onClick={() => setActiveTab("notifications")}
          className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 hover:border-teal-500/50 cursor-pointer transition-all shadow-xs group"
          data-testid="stat-card-notifications"
        >
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-xs font-semibold uppercase tracking-wider">Notifications</span>
            <Bell className="w-5 h-5 text-teal-600 dark:text-teal-400 group-hover:scale-110 transition-transform" />
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              {systemStatus?.unread_notifications_count ?? "—"}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">
              unread alerts
            </span>
          </div>
          <div className="mt-2 text-xs text-teal-600 dark:text-teal-400 flex items-center gap-1 font-medium group-hover:underline">
            Open notification inbox <ArrowRight className="w-3 h-3" />
          </div>
        </div>
      </div>

      {/* Application Tracking Pipeline Summary */}
      {isAuthenticated && (
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-teal-600" />
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">
                Application Tracking Pipeline
              </h3>
            </div>
            <button
              onClick={() => setActiveTab("applications")}
              className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline flex items-center gap-1"
            >
              Open Kanban Board &rarr;
            </button>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1">
            <div className="bg-slate-50 dark:bg-slate-800/60 p-3 rounded-lg border border-slate-100 dark:border-slate-800 text-center">
              <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase">
                Saved
              </span>
              <span className="text-xl font-bold text-slate-900 dark:text-slate-100 block mt-0.5">
                {appStageCounts.saved}
              </span>
            </div>
            <div className="bg-blue-50/60 dark:bg-blue-950/30 p-3 rounded-lg border border-blue-100 dark:border-blue-900/50 text-center">
              <span className="text-[11px] font-semibold text-blue-700 dark:text-blue-300 uppercase">
                Applied
              </span>
              <span className="text-xl font-bold text-blue-900 dark:text-blue-100 block mt-0.5">
                {appStageCounts.applied}
              </span>
            </div>
            <div className="bg-purple-50/60 dark:bg-purple-950/30 p-3 rounded-lg border border-purple-100 dark:border-purple-900/50 text-center">
              <span className="text-[11px] font-semibold text-purple-700 dark:text-purple-300 uppercase">
                Interviewing
              </span>
              <span className="text-xl font-bold text-purple-900 dark:text-purple-100 block mt-0.5">
                {appStageCounts.interviewing}
              </span>
            </div>
            <div className="bg-emerald-50/60 dark:bg-emerald-950/30 p-3 rounded-lg border border-emerald-100 dark:border-emerald-900/50 text-center">
              <span className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-300 uppercase">
                Offers
              </span>
              <span className="text-xl font-bold text-emerald-900 dark:text-emerald-100 block mt-0.5">
                {appStageCounts.offer}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Quick Action Bar */}
      <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-teal-600 dark:text-teal-400" />
            Quick Pipeline Operations
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Trigger background pipeline workers manually without waiting for scheduler ticks.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleTriggerDiscovery}
            isLoading={isTriggeringDiscovery}
            leftIcon={<Play className="w-3.5 h-3.5" />}
            data-testid="btn-quick-discovery"
          >
            Run Discovery
          </Button>

          <Button
            variant="primary"
            size="sm"
            onClick={handleTriggerDrain}
            isLoading={isTriggeringDrain}
            leftIcon={<RotateCw className="w-3.5 h-3.5" />}
            data-testid="btn-quick-drain"
          >
            Run Backlog Drain
          </Button>
        </div>
      </div>

      {/* Grid: Background Workers & Recent Jobs */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Workers Overview (1 column) */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-teal-600 dark:text-teal-400" />
              Background Workers
            </h3>
            <Badge variant="neutral" size="sm">
              Continuous
            </Badge>
          </div>

          <div className="space-y-3">
            {/* Job Discovery */}
            <div className="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-slate-900 dark:text-slate-200">
                  Job Discovery Worker
                </span>
                <Badge
                  variant={discoveryInfo?.scheduled ? "success" : "neutral"}
                  size="sm"
                >
                  {discoveryInfo?.scheduled ? "Scheduled" : "Idle"}
                </Badge>
              </div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                Next: {discoveryInfo?.next_run_time ? new Date(discoveryInfo.next_run_time).toLocaleTimeString() : "—"}
              </div>
            </div>

            {/* Profile Reevaluation */}
            <div className="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-slate-900 dark:text-slate-200">
                  Profile Reevaluation
                </span>
                <Badge
                  variant={reevaluationInfo?.scheduled ? "success" : "neutral"}
                  size="sm"
                >
                  {reevaluationInfo?.scheduled ? "Scheduled" : "Idle"}
                </Badge>
              </div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                Next: {reevaluationInfo?.next_run_time ? new Date(reevaluationInfo.next_run_time).toLocaleTimeString() : "—"}
              </div>
            </div>

            {/* Backlog Drain */}
            <div className="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-slate-900 dark:text-slate-200">
                  Backlog Drain Worker
                </span>
                <Badge
                  variant={drainInfo?.scheduled ? "success" : "neutral"}
                  size="sm"
                >
                  {drainInfo?.scheduled ? "Scheduled" : "Idle"}
                </Badge>
              </div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                Next: {drainInfo?.next_run_time ? new Date(drainInfo.next_run_time).toLocaleTimeString() : "—"}
              </div>
            </div>
          </div>

          <div className="pt-2">
            <button
              onClick={() => setActiveTab("monitoring")}
              className="w-full py-2 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:text-teal-700 bg-teal-50 dark:bg-teal-950/40 rounded-lg hover:bg-teal-100 transition-colors"
            >
              Open Full Monitoring Controls &rarr;
            </button>
          </div>
        </div>

        {/* Recent Job Discoveries (2 columns) */}
        <div className="lg:col-span-2 bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Recent Career Discoveries
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Freshly monitored opportunities from active career sites.
              </p>
            </div>
            <button
              onClick={() => setActiveTab("jobs")}
              className="text-xs text-teal-600 dark:text-teal-400 font-medium hover:underline flex items-center gap-1"
            >
              View all <ArrowRight className="w-3 h-3" />
            </button>
          </div>

          {isLoading ? (
            <div className="py-12">
              <LoadingSpinner size="md" label="Loading recent opportunities..." />
            </div>
          ) : recentJobs.length === 0 ? (
            <EmptyState
              title="No jobs discovered yet"
              description="Add monitored companies and run a career scan to discover jobs."
              actionLabel="Add Company"
              onAction={() => setActiveTab("companies")}
            />
          ) : (
            <div className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="recent-jobs-list">
              {recentJobs.map((job) => {
                const companyName = companyMap.get(job.company_id) || `Company #${job.company_id}`;

                return (
                  <div
                    key={job.id}
                    onClick={() => setSelectedJobId(job.id)}
                    className="py-3 px-2 -mx-2 hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded-lg cursor-pointer transition-colors flex items-center justify-between gap-4 group"
                    data-testid={`job-row-${job.id}`}
                  >
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-sm text-slate-900 dark:text-slate-100 truncate group-hover:text-teal-600 dark:group-hover:text-teal-400 transition-colors">
                          {job.title}
                        </span>
                        {job.semantic_score !== undefined && job.semantic_score !== null && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-50 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300 border border-violet-200 dark:border-violet-800">
                            {Math.round(job.semantic_score)}% fit
                          </span>
                        )}
                        {job.job_type && (
                          <Badge variant="brand" size="sm">
                            {job.job_type}
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
                        <span className="font-medium text-slate-700 dark:text-slate-300">
                          {companyName}
                        </span>
                        {job.location && <span>&bull; {job.location}</span>}
                        <span>&bull; {new Date(job.first_seen_at).toLocaleDateString()}</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 flex-shrink-0">
                      <Badge variant={job.status === "ACTIVE" ? "success" : "neutral"} size="sm">
                        {job.status}
                      </Badge>
                      <ExternalLink className="w-4 h-4 text-slate-400 group-hover:text-teal-600 transition-colors" />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
