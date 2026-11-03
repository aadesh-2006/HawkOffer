import React, { useEffect, useState, useCallback, useMemo } from "react";
import {
  Search,
  MapPin,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Building2,
  X,
  Sparkles,
  Bookmark,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { useAuth } from "../../context/AuthContext";
import { JobRead, CompanyRead, JobFilterParams, ApplicationRead } from "../../types/api";
import { jobsApi, companiesApi, applicationsApi } from "../../services/api";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EmptyState } from "../common/EmptyState";

export const JobsFeedScreen: React.FC = () => {
  const { setSelectedJobId, showToast } = useApp();
  const { isAuthenticated, openAuthModal } = useAuth();

  const [jobs, setJobs] = useState<JobRead[]>([]);
  const [companies, setCompanies] = useState<CompanyRead[]>([]);
  const [trackedApplications, setTrackedApplications] = useState<ApplicationRead[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Tab & Filters state
  const [feedTab, setFeedTab] = useState<"all" | "matching" | "saved">("all");
  const [search, setSearch] = useState<string>("");
  const [debouncedSearch, setDebouncedSearch] = useState<string>("");
  const [companyId, setCompanyId] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [jobType, setJobType] = useState<string>("");
  const [gradYear, setGradYear] = useState<string>("");
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(25);

  // Quick save loading state
  const [savingJobId, setSavingJobId] = useState<number | null>(null);

  // Debounce search input by 300ms
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1); // reset to first page on search change
    }, 300);
    return () => clearTimeout(handler);
  }, [search]);

  // Load companies for filter dropdown and tracked applications
  useEffect(() => {
    companiesApi
      .list()
      .then((res) => setCompanies(res.items))
      .catch(() => {});

    if (isAuthenticated) {
      applicationsApi
        .list()
        .then((apps) => setTrackedApplications(apps))
        .catch(() => {});
    } else {
      setTrackedApplications([]);
    }
  }, [isAuthenticated]);

  const trackedJobMap = useMemo(() => {
    const map = new Map<number, ApplicationRead>();
    trackedApplications.forEach((app) => map.set(app.job_id, app));
    return map;
  }, [trackedApplications]);

  const fetchJobs = useCallback(async () => {
    setIsLoading(true);
    try {
      const params: JobFilterParams = {
        limit: pageSize,
        offset: (page - 1) * pageSize,
      };
      if (debouncedSearch.trim()) params.search = debouncedSearch.trim();
      if (companyId) params.company_id = Number(companyId);
      if (status) params.status = status;
      if (jobType) params.job_type = jobType;
      if (gradYear) params.graduation_year = Number(gradYear);

      const res = await jobsApi.list(params);
      setJobs(res.items);
      setTotalCount(res.total);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load jobs feed";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [page, pageSize, debouncedSearch, companyId, status, jobType, gradYear, showToast]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  const handleQuickSave = async (e: React.MouseEvent, job: JobRead) => {
    e.stopPropagation();
    if (!isAuthenticated) {
      openAuthModal("login");
      return;
    }

    const existing = trackedJobMap.get(job.id);
    if (existing) {
      // Already tracked; inspect details
      setSelectedJobId(job.id);
      return;
    }

    setSavingJobId(job.id);
    try {
      const created = await applicationsApi.create({
        job_id: job.id,
        status: "SAVED",
      });
      setTrackedApplications((prev) => [...prev, created]);
      showToast(`Saved "${job.title}" to Application Tracker!`, "success");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save job";
      showToast(msg, "error");
    } finally {
      setSavingJobId(null);
    }
  };

  const clearFilters = () => {
    setSearch("");
    setDebouncedSearch("");
    setCompanyId("");
    setStatus("");
    setJobType("");
    setGradYear("");
    setPage(1);
  };

  const hasActiveFilters =
    Boolean(search) || Boolean(companyId) || Boolean(status) || Boolean(jobType) || Boolean(gradYear);

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  const companyMap = useMemo(() => {
    const map = new Map<number, string>();
    companies.forEach((c) => map.set(c.id, c.name));
    return map;
  }, [companies]);

  // Filter jobs based on active tab
  const displayedJobs = useMemo(() => {
    if (feedTab === "matching") {
      return jobs.filter(
        (j) => (j.semantic_score && j.semantic_score >= 50) || (j.match_tier && j.match_tier !== "INELIGIBLE")
      );
    }
    if (feedTab === "saved") {
      return jobs.filter((j) => trackedJobMap.has(j.id));
    }
    return jobs;
  }, [jobs, feedTab, trackedJobMap]);

  return (
    <div className="space-y-6">
      {/* Top View Selector & Search / Filters Bar */}
      <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 space-y-3">
        {/* Navigation Tabs: Matching, Saved, All */}
        <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 p-1 rounded-lg border border-slate-200 dark:border-slate-700 w-fit">
          <button
            onClick={() => setFeedTab("all")}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
              feedTab === "all"
                ? "bg-white dark:bg-slate-900 text-teal-700 dark:text-teal-300 shadow-xs"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
            data-testid="tab-all-jobs"
          >
            All Discoveries
          </button>
          <button
            onClick={() => setFeedTab("matching")}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${
              feedTab === "matching"
                ? "bg-white dark:bg-slate-900 text-teal-700 dark:text-teal-300 shadow-xs"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
            data-testid="tab-matching-jobs"
          >
            <Sparkles className="w-3.5 h-3.5 text-violet-500" />
            Top Matches
          </button>
          <button
            onClick={() => setFeedTab("saved")}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${
              feedTab === "saved"
                ? "bg-white dark:bg-slate-900 text-teal-700 dark:text-teal-300 shadow-xs"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
            data-testid="tab-saved-jobs"
          >
            <Bookmark className="w-3.5 h-3.5 text-teal-600" />
            Tracked in Pipeline ({trackedApplications.length})
          </button>
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          {/* Search Input */}
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by job title, department, or keywords..."
              className="w-full pl-9 pr-8 py-2 text-sm bg-slate-50 dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:outline-hidden focus:ring-2 focus:ring-teal-500 transition-colors"
              data-testid="input-job-search"
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Quick Clear Button */}
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearFilters}
              leftIcon={<X className="w-3.5 h-3.5" />}
              data-testid="btn-clear-filters"
            >
              Reset Filters
            </Button>
          )}
        </div>

        {/* Filter Dropdowns Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-1">
          {/* Company Filter */}
          <div>
            <select
              value={companyId}
              onChange={(e) => {
                setCompanyId(e.target.value);
                setPage(1);
              }}
              className="w-full text-xs py-2 px-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-700 dark:text-slate-200 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="select-filter-company"
            >
              <option value="">All Companies</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          {/* Status Filter */}
          <div>
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
              className="w-full text-xs py-2 px-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-700 dark:text-slate-200 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="select-filter-status"
            >
              <option value="">All Statuses</option>
              <option value="ACTIVE">ACTIVE</option>
              <option value="CLOSED">CLOSED</option>
              <option value="STALE">STALE</option>
            </select>
          </div>

          {/* Job Type Filter */}
          <div>
            <select
              value={jobType}
              onChange={(e) => {
                setJobType(e.target.value);
                setPage(1);
              }}
              className="w-full text-xs py-2 px-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-700 dark:text-slate-200 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="select-filter-type"
            >
              <option value="">All Job Types</option>
              <option value="internship">Internship</option>
              <option value="full_time">Full Time</option>
              <option value="new_grad">New Grad</option>
              <option value="coop">Co-op</option>
            </select>
          </div>

          {/* Graduation Year Filter */}
          <div>
            <select
              value={gradYear}
              onChange={(e) => {
                setGradYear(e.target.value);
                setPage(1);
              }}
              className="w-full text-xs py-2 px-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-700 dark:text-slate-200 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="select-filter-gradyear"
            >
              <option value="">Any Grad Year</option>
              <option value="2025">Class of 2025</option>
              <option value="2026">Class of 2026</option>
              <option value="2027">Class of 2027</option>
              <option value="2028">Class of 2028</option>
            </select>
          </div>
        </div>
      </div>

      {/* Jobs Feed Table / List */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden shadow-xs">
        {isLoading ? (
          <div className="py-20">
            <LoadingSpinner size="lg" label="Loading jobs feed..." />
          </div>
        ) : displayedJobs.length === 0 ? (
          <div className="py-16">
            <EmptyState
              title={
                feedTab === "saved"
                  ? "No saved or tracked jobs yet"
                  : hasActiveFilters
                  ? "No matching jobs found"
                  : "No jobs discovered yet"
              }
              description={
                feedTab === "saved"
                  ? "Click the 'Save' button on any job card to track it through your application pipeline."
                  : hasActiveFilters
                  ? "Try adjusting your search criteria or resetting filters."
                  : "Add companies to begin monitoring job openings."
              }
              actionLabel={hasActiveFilters ? "Clear All Filters" : undefined}
              onAction={hasActiveFilters ? clearFilters : undefined}
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" data-testid="jobs-table">
              <thead className="bg-slate-50 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-800 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                <tr>
                  <th className="py-3 px-4">Title &amp; Fit</th>
                  <th className="py-3 px-4">Company</th>
                  <th className="py-3 px-4">Location</th>
                  <th className="py-3 px-4">Classification</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Discovered</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {displayedJobs.map((job) => {
                  const companyName = companyMap.get(job.company_id) || `Company #${job.company_id}`;
                  const trackedApp = trackedJobMap.get(job.id);
                  const isSavingThis = savingJobId === job.id;

                  return (
                    <tr
                      key={job.id}
                      onClick={() => setSelectedJobId(job.id)}
                      className="hover:bg-slate-50 dark:hover:bg-slate-800/40 cursor-pointer transition-colors group"
                      data-testid={`job-row-${job.id}`}
                    >
                      <td className="py-3.5 px-4">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-slate-900 dark:text-slate-100 group-hover:text-teal-600 dark:group-hover:text-teal-400 transition-colors">
                            {job.title}
                          </span>
                          {job.semantic_score !== undefined && job.semantic_score !== null && (
                            <span
                              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-50 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300 border border-violet-200 dark:border-violet-800"
                              title={`Local AI semantic similarity: ${job.semantic_score.toFixed(1)}%`}
                            >
                              <Sparkles className="w-2.5 h-2.5 text-violet-500" />
                              {Math.round(job.semantic_score)}% fit
                            </span>
                          )}
                          {trackedApp && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-50 text-teal-800 dark:bg-teal-950/60 dark:text-teal-300 border border-teal-200 dark:border-teal-800">
                              <Bookmark className="w-2.5 h-2.5 text-teal-600 fill-teal-600" />
                              {trackedApp.status}
                            </span>
                          )}
                        </div>
                        {job.department && (
                          <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                            {job.department}
                          </div>
                        )}
                      </td>

                      <td className="py-3.5 px-4 text-xs font-medium text-slate-700 dark:text-slate-300 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <Building2 className="w-3.5 h-3.5 text-slate-400" />
                          <span>{companyName}</span>
                        </div>
                      </td>

                      <td className="py-3.5 px-4 text-xs text-slate-600 dark:text-slate-400 whitespace-nowrap">
                        {job.location ? (
                          <div className="flex items-center gap-1.5">
                            <MapPin className="w-3.5 h-3.5 text-slate-400" />
                            <span>{job.location}</span>
                          </div>
                        ) : (
                          <span className="text-slate-400 italic">—</span>
                        )}
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          {job.job_type && (
                            <Badge variant="brand" size="sm">
                              {job.job_type}
                            </Badge>
                          )}
                          {job.graduation_year && (
                            <Badge variant="warning" size="sm">
                              '{job.graduation_year.toString().slice(-2)}
                            </Badge>
                          )}
                          {!job.job_type && !job.graduation_year && (
                            <span className="text-xs text-slate-400">—</span>
                          )}
                        </div>
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <Badge
                          variant={job.status === "ACTIVE" ? "success" : "neutral"}
                          size="sm"
                        >
                          {job.status}
                        </Badge>
                      </td>

                      <td className="py-3.5 px-4 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                        {new Date(job.first_seen_at).toLocaleDateString()}
                      </td>

                      <td className="py-3.5 px-4 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1.5">
                          {/* Quick Track / Bookmark Button */}
                          <button
                            onClick={(e) => handleQuickSave(e, job)}
                            disabled={isSavingThis}
                            className={`p-1.5 rounded transition-colors text-xs flex items-center gap-1 ${
                              trackedApp
                                ? "text-teal-600 dark:text-teal-400 bg-teal-50 dark:bg-teal-950/40"
                                : "text-slate-400 hover:text-teal-600 hover:bg-slate-100 dark:hover:bg-slate-800"
                            }`}
                            title={trackedApp ? `Tracked (${trackedApp.status})` : "Save / Track Application"}
                            data-testid={`quick-save-btn-${job.id}`}
                          >
                            <Bookmark className={`w-3.5 h-3.5 ${trackedApp ? "fill-teal-600 text-teal-600" : ""}`} />
                          </button>

                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setSelectedJobId(job.id)}
                            data-testid={`btn-inspect-job-${job.id}`}
                          >
                            Details
                          </Button>

                          {job.application_url && (
                            <a
                              href={job.application_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-slate-400 hover:text-teal-600 p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-800"
                              title="Open original job posting"
                            >
                              <ExternalLink className="w-4 h-4" />
                            </a>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Footer */}
        <div className="px-4 py-3 bg-slate-50 dark:bg-slate-800/40 border-t border-slate-200 dark:border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-slate-600 dark:text-slate-400">
          <div className="flex items-center gap-3">
            <span>
              Showing{" "}
              <strong className="text-slate-900 dark:text-slate-200">
                {totalCount === 0 ? 0 : (page - 1) * pageSize + 1}
              </strong>{" "}
              to{" "}
              <strong className="text-slate-900 dark:text-slate-200">
                {Math.min(page * pageSize, totalCount)}
              </strong>{" "}
              of <strong className="text-slate-900 dark:text-slate-200">{totalCount}</strong> jobs
            </span>

            {/* Page Size Selector */}
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
              className="py-1 px-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded text-xs text-slate-700 dark:text-slate-200"
              aria-label="Jobs per page"
            >
              <option value="10">10 / page</option>
              <option value="25">25 / page</option>
              <option value="50">50 / page</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              leftIcon={<ChevronLeft className="w-3.5 h-3.5" />}
              data-testid="pagination-prev"
            >
              Previous
            </Button>

            <span className="px-2 font-medium">
              Page {page} of {totalPages}
            </span>

            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              rightIcon={<ChevronRight className="w-3.5 h-3.5" />}
              data-testid="pagination-next"
            >
              Next
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};
