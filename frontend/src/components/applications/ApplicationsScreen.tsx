import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bookmark,
  Calendar,
  FileText,
  Kanban,
  List,
  MapPin,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { useAuth } from "../../context/AuthContext";
import {
  ApplicationCreate,
  ApplicationRead,
  ApplicationStatus,
  ApplicationUpdate,
  CompanyRead,
  JobRead,
} from "../../types/api";
import { applicationsApi, companiesApi, jobsApi } from "../../services/api";
import { Button } from "../common/Button";
import { EmptyState } from "../common/EmptyState";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { Modal } from "../common/Modal";

const APPLICATION_STAGES: { status: ApplicationStatus; label: string; color: string }[] = [
  { status: "SAVED", label: "Saved", color: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-300" },
  { status: "APPLIED", label: "Applied", color: "bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300" },
  { status: "INTERVIEWING", label: "Interviewing", color: "bg-purple-100 text-purple-800 dark:bg-purple-950/60 dark:text-purple-300" },
  { status: "OFFER", label: "Offer Received", color: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300" },
  { status: "REJECTED", label: "Archived / Rejected", color: "bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-300" },
];

export const ApplicationsScreen: React.FC = () => {
  const { setSelectedJobId, showToast, setActiveTab } = useApp();
  const { isAuthenticated, openAuthModal } = useAuth();

  const [applications, setApplications] = useState<ApplicationRead[]>([]);
  const [companies, setCompanies] = useState<CompanyRead[]>([]);
  const [recentJobs, setRecentJobs] = useState<JobRead[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [viewMode, setViewMode] = useState<"kanban" | "list">("kanban");
  const [searchFilter, setSearchFilter] = useState<string>("");

  // Modal States
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [editingApp, setEditingApp] = useState<ApplicationRead | null>(null);

  // Form Fields
  const [formJobId, setFormJobId] = useState<string>("");
  const [formStatus, setFormStatus] = useState<ApplicationStatus>("SAVED");
  const [formNotes, setFormNotes] = useState<string>("");
  const [formAppliedAt, setFormAppliedAt] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const fetchApplications = useCallback(async () => {
    if (!isAuthenticated) {
      setApplications([]);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    try {
      const [appsRes, compsRes, jobsRes] = await Promise.allSettled([
        applicationsApi.list(),
        companiesApi.list({ limit: 100 }),
        jobsApi.list({ limit: 50 }),
      ]);

      if (appsRes.status === "fulfilled") {
        setApplications(appsRes.value);
      }
      if (compsRes.status === "fulfilled") {
        setCompanies(compsRes.value.items);
      }
      if (jobsRes.status === "fulfilled") {
        setRecentJobs(jobsRes.value.items);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load applications";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [isAuthenticated, showToast]);

  useEffect(() => {
    fetchApplications();
  }, [fetchApplications]);

  const companyMap = useMemo(() => {
    const map = new Map<number, string>();
    companies.forEach((c) => map.set(c.id, c.name));
    return map;
  }, [companies]);

  // Open Add Modal
  const openAddModal = () => {
    setFormJobId(recentJobs[0]?.id ? String(recentJobs[0].id) : "");
    setFormStatus("SAVED");
    setFormNotes("");
    setFormAppliedAt("");
    setIsAddModalOpen(true);
  };

  // Open Edit Modal
  const openEditModal = (app: ApplicationRead) => {
    setEditingApp(app);
    setFormStatus(app.status as ApplicationStatus);
    setFormNotes(app.notes || "");
    setFormAppliedAt(app.applied_at ? app.applied_at.slice(0, 10) : "");
  };

  // Handle Create Application
  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const jId = Number(formJobId);
    if (!jId) {
      showToast("Please select a job opportunity to track", "error");
      return;
    }

    setIsSubmitting(true);
    try {
      const payload: ApplicationCreate = {
        job_id: jId,
        status: formStatus,
        notes: formNotes.trim() || null,
        applied_at: formAppliedAt ? new Date(formAppliedAt).toISOString() : null,
      };
      await applicationsApi.create(payload);
      showToast("Job added to application tracker!", "success");
      setIsAddModalOpen(false);
      await fetchApplications();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to track application";
      showToast(msg, "error");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Handle Update Application
  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingApp) return;

    setIsSubmitting(true);
    try {
      const payload: ApplicationUpdate = {
        status: formStatus,
        notes: formNotes.trim() || null,
        applied_at: formAppliedAt ? new Date(formAppliedAt).toISOString() : null,
      };
      await applicationsApi.update(editingApp.id, payload);
      showToast("Application updated successfully", "success");
      setEditingApp(null);
      await fetchApplications();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update application";
      showToast(msg, "error");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Quick Status Transition (e.g. from card dropdown)
  const handleQuickStatusChange = async (appId: number, newStatus: ApplicationStatus) => {
    try {
      await applicationsApi.update(appId, { status: newStatus });
      showToast(`Status updated to ${newStatus}`, "success");
      setApplications((prev) =>
        prev.map((a) => (a.id === appId ? { ...a, status: newStatus } : a))
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update status";
      showToast(msg, "error");
    }
  };

  // Handle Delete Application
  const handleDelete = async (appId: number) => {
    try {
      await applicationsApi.delete(appId);
      showToast("Application removed from tracker", "success");
      setApplications((prev) => prev.filter((a) => a.id !== appId));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to delete application";
      showToast(msg, "error");
    }
  };

  // Filtered applications
  const filteredApps = useMemo(() => {
    if (!searchFilter.trim()) return applications;
    const term = searchFilter.toLowerCase();
    return applications.filter((app) => {
      const title = app.job?.title?.toLowerCase() || "";
      const companyName = app.job?.company_id
        ? (companyMap.get(app.job.company_id) || "").toLowerCase()
        : "";
      const notes = (app.notes || "").toLowerCase();
      const status = app.status.toLowerCase();
      return (
        title.includes(term) ||
        companyName.includes(term) ||
        notes.includes(term) ||
        status.includes(term)
      );
    });
  }, [applications, searchFilter, companyMap]);

  // Stage counts
  const stageCounts = useMemo(() => {
    const counts: Record<string, number> = {
      TOTAL: applications.length,
      SAVED: 0,
      APPLIED: 0,
      INTERVIEWING: 0,
      OFFER: 0,
      REJECTED: 0,
    };
    applications.forEach((a) => {
      if (counts[a.status] !== undefined) {
        counts[a.status]++;
      }
    });
    return counts;
  }, [applications]);

  // Render Unauthenticated State
  if (!isAuthenticated) {
    return (
      <div className="space-y-6" data-testid="applications-unauthenticated">
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-10 text-center max-w-xl mx-auto space-y-4 shadow-sm">
          <div className="w-14 h-14 bg-teal-50 dark:bg-teal-950/50 rounded-2xl flex items-center justify-center text-teal-600 dark:text-teal-400 mx-auto">
            <Bookmark className="w-7 h-7" />
          </div>
          <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">
            Track Your Career Pipeline
          </h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
            Sign in to organize matched opportunities, record interview notes, and track your
            progress from application to offer across all followed companies.
          </p>
          <div className="pt-2">
            <Button
              variant="primary"
              size="md"
              onClick={() => openAuthModal("login")}
              data-testid="login-to-track-btn"
            >
              Sign In to Access Tracker
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="applications-screen">
      {/* Metric Stage Badges */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs">
          <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider block">
            Total Tracked
          </span>
          <span className="text-2xl font-bold text-slate-900 dark:text-slate-100 mt-0.5 block">
            {stageCounts.TOTAL}
          </span>
        </div>
        {APPLICATION_STAGES.map((s) => (
          <div
            key={s.status}
            className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs"
          >
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                {s.label}
              </span>
            </div>
            <span className="text-2xl font-bold text-slate-900 dark:text-slate-100 mt-0.5 block">
              {stageCounts[s.status] || 0}
            </span>
          </div>
        ))}
      </div>

      {/* Control Bar: Search, View Mode, Add Application */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800">
        <div className="relative flex-1 max-w-md">
          <Search className="w-4 h-4 absolute left-3 top-3 text-slate-400" />
          <input
            type="text"
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            placeholder="Search by job title, company, or notes..."
            className="w-full pl-9 pr-4 py-2 text-sm bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-slate-900 dark:text-slate-100"
            data-testid="app-search-input"
          />
        </div>

        <div className="flex items-center gap-2">
          {/* View Mode Toggle */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800 rounded-lg p-0.5 border border-slate-200 dark:border-slate-700">
            <button
              onClick={() => setViewMode("kanban")}
              className={`p-1.5 rounded-md text-xs font-medium flex items-center gap-1 ${
                viewMode === "kanban"
                  ? "bg-white dark:bg-slate-900 text-teal-600 dark:text-teal-400 shadow-xs"
                  : "text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
              }`}
              title="Kanban Board View"
              data-testid="view-kanban-btn"
            >
              <Kanban className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={`p-1.5 rounded-md text-xs font-medium flex items-center gap-1 ${
                viewMode === "list"
                  ? "bg-white dark:bg-slate-900 text-teal-600 dark:text-teal-400 shadow-xs"
                  : "text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
              }`}
              title="List View"
              data-testid="view-list-btn"
            >
              <List className="w-4 h-4" />
            </button>
          </div>

          <Button variant="primary" size="sm" onClick={openAddModal} data-testid="add-application-btn">
            <Plus className="w-4 h-4 mr-1.5" />
            Track Job
          </Button>
        </div>
      </div>

      {/* Main Content Area */}
      {isLoading ? (
        <div className="py-20 flex justify-center">
          <LoadingSpinner size="lg" label="Loading applications..." />
        </div>
      ) : applications.length === 0 ? (
        <EmptyState
          icon={<Bookmark className="w-8 h-8 text-teal-600" />}
          title="No applications tracked yet"
          description="When you discover relevant career opportunities, click 'Save' or 'Track Application' to organize your recruitment pipeline here."
          action={
            <Button variant="primary" onClick={() => setActiveTab("jobs")}>
              Explore Jobs Feed
            </Button>
          }
        />
      ) : viewMode === "kanban" ? (
        /* Kanban Columns */
        <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-4 items-start">
          {APPLICATION_STAGES.map((stage) => {
            const stageApps = filteredApps.filter((a) => a.status === stage.status);

            return (
              <div
                key={stage.status}
                className="bg-slate-100/70 dark:bg-slate-900/60 rounded-xl p-3 border border-slate-200 dark:border-slate-800 space-y-3"
                data-testid={`kanban-stage-${stage.status}`}
              >
                {/* Stage Header */}
                <div className="flex items-center justify-between pb-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider">
                      {stage.label}
                    </span>
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-200 dark:bg-slate-800 text-slate-600 dark:text-slate-300">
                      {stageApps.length}
                    </span>
                  </div>
                </div>

                {/* Cards in this stage */}
                <div className="space-y-2.5">
                  {stageApps.length === 0 ? (
                    <div className="p-4 border border-dashed border-slate-200 dark:border-slate-800 rounded-lg text-center text-[11px] text-slate-400">
                      No jobs in {stage.label.toLowerCase()}
                    </div>
                  ) : (
                    stageApps.map((app) => {
                      const job = app.job;
                      const companyName = job?.company_id
                        ? companyMap.get(job.company_id) || "Company"
                        : "Opportunity";

                      return (
                        <div
                          key={app.id}
                          className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3.5 shadow-xs space-y-2.5 hover:border-teal-500/50 transition-colors"
                          data-testid={`app-card-${app.id}`}
                        >
                          <div className="space-y-1">
                            <span className="text-[11px] font-semibold text-teal-600 dark:text-teal-400 block truncate">
                              {companyName}
                            </span>
                            <h4
                              onClick={() => setSelectedJobId(app.job_id)}
                              className="text-xs font-bold text-slate-900 dark:text-slate-100 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer line-clamp-2"
                              title={job?.title || `Job #${app.job_id}`}
                            >
                              {job?.title || `Job #${app.job_id}`}
                            </h4>
                          </div>

                          {job?.location && (
                            <div className="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400 truncate">
                              <MapPin className="w-3 h-3 flex-shrink-0" />
                              <span className="truncate">{job.location}</span>
                            </div>
                          )}

                          {app.notes && (
                            <div className="text-[11px] text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80 line-clamp-3">
                              {app.notes}
                            </div>
                          )}

                          {app.applied_at && (
                            <div className="flex items-center gap-1 text-[10px] text-slate-400">
                              <Calendar className="w-3 h-3 flex-shrink-0" />
                              <span>Applied: {new Date(app.applied_at).toLocaleDateString()}</span>
                            </div>
                          )}

                          {/* Card Footer Actions */}
                          <div className="pt-2 border-t border-slate-100 dark:border-slate-800/80 flex items-center justify-between gap-1">
                            {/* Status Change Selector */}
                            <select
                              value={app.status}
                              onChange={(e) =>
                                handleQuickStatusChange(app.id, e.target.value as ApplicationStatus)
                              }
                              className="text-[11px] font-medium bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded px-1.5 py-0.5 text-slate-700 dark:text-slate-300 focus:outline-none"
                              data-testid={`quick-status-${app.id}`}
                            >
                              {APPLICATION_STAGES.map((s) => (
                                <option key={s.status} value={s.status}>
                                  {s.label}
                                </option>
                              ))}
                            </select>

                            <div className="flex items-center gap-1">
                              <button
                                onClick={() => openEditModal(app)}
                                className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded"
                                title="Edit notes & details"
                              >
                                <FileText className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => handleDelete(app.id)}
                                className="p-1 text-slate-400 hover:text-rose-600 rounded"
                                title="Delete from tracker"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* List View */
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden shadow-xs">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-950/60 border-b border-slate-200 dark:border-slate-800 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              <tr>
                <th className="py-3 px-4">Opportunity</th>
                <th className="py-3 px-4">Location</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Notes</th>
                <th className="py-3 px-4">Applied Date</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {filteredApps.map((app) => {
                const job = app.job;
                const companyName = job?.company_id
                  ? companyMap.get(job.company_id) || "Company"
                  : "Opportunity";

                return (
                  <tr
                    key={app.id}
                    className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40 transition-colors"
                  >
                    <td className="py-3.5 px-4">
                      <div className="font-semibold text-slate-900 dark:text-slate-100 hover:text-teal-600 cursor-pointer"
                        onClick={() => setSelectedJobId(app.job_id)}
                      >
                        {job?.title || `Job #${app.job_id}`}
                      </div>
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        {companyName}
                      </div>
                    </td>
                    <td className="py-3.5 px-4 text-xs text-slate-600 dark:text-slate-400">
                      {job?.location || "Remote / Unspecified"}
                    </td>
                    <td className="py-3.5 px-4">
                      <select
                        value={app.status}
                        onChange={(e) =>
                          handleQuickStatusChange(app.id, e.target.value as ApplicationStatus)
                        }
                        className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-md px-2 py-1 text-slate-800 dark:text-slate-200 focus:outline-none"
                      >
                        {APPLICATION_STAGES.map((s) => (
                          <option key={s.status} value={s.status}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-3.5 px-4 text-xs text-slate-600 dark:text-slate-300 max-w-xs truncate">
                      {app.notes || <span className="text-slate-400 italic">None</span>}
                    </td>
                    <td className="py-3.5 px-4 text-xs text-slate-500">
                      {app.applied_at
                        ? new Date(app.applied_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td className="py-3.5 px-4 text-right space-x-2">
                      <button
                        onClick={() => openEditModal(app)}
                        className="text-slate-400 hover:text-teal-600 text-xs font-medium"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(app.id)}
                        className="text-slate-400 hover:text-rose-600 text-xs font-medium"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Application Modal */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Track Career Opportunity"
        size="md"
      >
        <form onSubmit={handleCreate} className="space-y-4">
          <div className="space-y-1">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Select Job Opportunity <span className="text-rose-500">*</span>
            </label>
            <select
              required
              value={formJobId}
              onChange={(e) => setFormJobId(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500"
              data-testid="add-app-job-select"
            >
              <option value="">-- Choose from discovered jobs --</option>
              {recentJobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.title} ({companyMap.get(j.company_id) || "Company"})
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Pipeline Stage
            </label>
            <select
              value={formStatus}
              onChange={(e) => setFormStatus(e.target.value as ApplicationStatus)}
              className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500"
            >
              {APPLICATION_STAGES.map((s) => (
                <option key={s.status} value={s.status}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Application Date <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="date"
              value={formAppliedAt}
              onChange={(e) => setFormAppliedAt(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500"
            />
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Notes & Interview Details
            </label>
            <textarea
              rows={3}
              value={formNotes}
              onChange={(e) => setFormNotes(e.target.value)}
              placeholder="e.g. Applied via referral; follow-up scheduled next Monday."
              className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:ring-2 focus:ring-teal-500"
            />
          </div>

          <div className="pt-2 flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => setIsAddModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              isLoading={isSubmitting}
              data-testid="submit-add-app-btn"
            >
              Save to Tracker
            </Button>
          </div>
        </form>
      </Modal>

      {/* Edit Application Modal */}
      {editingApp && (
        <Modal
          isOpen={true}
          onClose={() => setEditingApp(null)}
          title="Update Application Record"
          size="md"
        >
          <form onSubmit={handleUpdate} className="space-y-4">
            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Pipeline Stage
              </label>
              <select
                value={formStatus}
                onChange={(e) => setFormStatus(e.target.value as ApplicationStatus)}
                className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500"
              >
                {APPLICATION_STAGES.map((s) => (
                  <option key={s.status} value={s.status}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Application Date
              </label>
              <input
                type="date"
                value={formAppliedAt}
                onChange={(e) => setFormAppliedAt(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Notes & Updates
              </label>
              <textarea
                rows={3}
                value={formNotes}
                onChange={(e) => setFormNotes(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div className="pt-2 flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setEditingApp(null)}
              >
                Cancel
              </Button>
              <Button type="submit" variant="primary" isLoading={isSubmitting}>
                Save Changes
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
};
