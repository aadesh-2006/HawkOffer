import React, { useEffect, useState } from "react";
import {
  MapPin,
  ExternalLink,
  Building2,
  Tag,
  Hash,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  Cpu,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { JobRead, CompanyRead, JobMatchDetail } from "../../types/api";
import { jobsApi, companiesApi } from "../../services/api";
import { Modal } from "../common/Modal";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { LoadingSpinner } from "../common/LoadingSpinner";

export const JobDetailModal: React.FC = () => {
  const { selectedJobId, setSelectedJobId, showToast } = useApp();
  const [job, setJob] = useState<JobRead | null>(null);
  const [company, setCompany] = useState<CompanyRead | null>(null);
  const [matchDetail, setMatchDetail] = useState<JobMatchDetail | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedJobId) {
      setJob(null);
      setCompany(null);
      setMatchDetail(null);
      setError(null);
      return;
    }

    let isMounted = true;
    setIsLoading(true);
    setError(null);

    const fetchDetails = async () => {
      try {
        const fetchedJob = await jobsApi.get(selectedJobId);
        if (!isMounted) return;
        setJob(fetchedJob);

        // Fetch company name for convenience
        try {
          const fetchedCompany = await companiesApi.get(fetchedJob.company_id);
          if (isMounted) {
            setCompany(fetchedCompany);
          }
        } catch {
          // Company lookup non-fatal
        }

        // Fetch AI Match Detail (if active profile available)
        try {
          const fetchedMatch = await jobsApi.getMatchDetail(selectedJobId);
          if (isMounted) {
            setMatchDetail(fetchedMatch);
          }
        } catch {
          if (isMounted) {
            setMatchDetail(null);
          }
        }
      } catch (err: unknown) {
        if (!isMounted) return;
        const msg = err instanceof Error ? err.message : "Failed to load job details";
        setError(msg);
        showToast(msg, "error");
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    fetchDetails();

    return () => {
      isMounted = false;
    };
  }, [selectedJobId, showToast]);

  const handleClose = () => {
    setSelectedJobId(null);
  };

  const getStatusVariant = (status: string) => {
    switch (status.toUpperCase()) {
      case "ACTIVE":
        return "success";
      case "CLOSED":
        return "neutral";
      case "STALE":
        return "warning";
      default:
        return "neutral";
    }
  };

  return (
    <Modal
      isOpen={selectedJobId !== null}
      onClose={handleClose}
      title={job ? job.title : "Job Details"}
      size="xl"
    >
      {isLoading && (
        <div className="py-16">
          <LoadingSpinner size="lg" label="Loading job details..." />
        </div>
      )}

      {error && !isLoading && (
        <div className="p-4 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 rounded-lg text-sm border border-rose-200 dark:border-rose-800">
          {error}
        </div>
      )}

      {job && !isLoading && (
        <div className="space-y-6">
          {/* Header Summary */}
          <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-200 dark:border-slate-800">
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant={getStatusVariant(job.status)} size="md">
                  {job.status}
                </Badge>
                {job.job_type && (
                  <Badge variant="brand" size="md">
                    {job.job_type}
                  </Badge>
                )}
                {job.employment_type && (
                  <Badge variant="neutral" size="md">
                    {job.employment_type}
                  </Badge>
                )}
                {job.graduation_year && (
                  <Badge variant="warning" size="md">
                    Class of {job.graduation_year}
                  </Badge>
                )}
              </div>

              <div className="flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400 flex-wrap">
                <span className="flex items-center gap-1 font-medium text-slate-700 dark:text-slate-300">
                  <Building2 className="w-3.5 h-3.5" />
                  {company ? company.name : `Company #${job.company_id}`}
                </span>
                {job.location && (
                  <span className="flex items-center gap-1">
                    <MapPin className="w-3.5 h-3.5" />
                    {job.location}
                  </span>
                )}
                {job.department && (
                  <span className="flex items-center gap-1">
                    <Tag className="w-3.5 h-3.5" />
                    {job.department}
                  </span>
                )}
              </div>
            </div>

            {/* Direct External Application Link */}
            {job.application_url && (
              <a
                href={job.application_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex"
              >
                <Button variant="primary" rightIcon={<ExternalLink className="w-4 h-4" />}>
                  Apply Now
                </Button>
              </a>
            )}
          </div>

          {/* Key Metadata Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-3.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg text-xs">
            <div>
              <span className="text-slate-500 dark:text-slate-400 block font-medium">External ID</span>
              <span className="font-mono text-slate-900 dark:text-slate-200 truncate block">
                {job.external_id || "—"}
              </span>
            </div>
            <div>
              <span className="text-slate-500 dark:text-slate-400 block font-medium">Job Type</span>
              <span className="text-slate-900 dark:text-slate-200 font-medium">
                {job.job_type || "Standard"}
              </span>
            </div>
            <div>
              <span className="text-slate-500 dark:text-slate-400 block font-medium">First Seen</span>
              <span className="text-slate-900 dark:text-slate-200">
                {new Date(job.first_seen_at).toLocaleDateString()}
              </span>
            </div>
            <div>
              <span className="text-slate-500 dark:text-slate-400 block font-medium">Last Verified</span>
              <span className="text-slate-900 dark:text-slate-200">
                {new Date(job.last_seen_at).toLocaleDateString()}
              </span>
            </div>
          </div>

          {/* AI Semantic Fit & Relevance Analysis */}
          {matchDetail && (
            <div className="p-4 bg-gradient-to-br from-violet-50/70 to-slate-50 dark:from-violet-950/20 dark:to-slate-900 rounded-xl border border-violet-200 dark:border-violet-900/40 space-y-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  <h4 className="text-xs font-bold text-slate-900 dark:text-slate-100 uppercase tracking-wider">
                    Semantic Matching &amp; Fit Analysis
                  </h4>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`px-2 py-0.5 rounded text-[11px] font-bold border ${
                      matchDetail.relevance_tier === "EXCELLENT"
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-300"
                        : matchDetail.relevance_tier === "HIGH"
                        ? "bg-teal-100 text-teal-800 dark:bg-teal-950/60 dark:text-teal-300 border-teal-300"
                        : matchDetail.relevance_tier === "MEDIUM"
                        ? "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 border-amber-300"
                        : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-300"
                    }`}
                  >
                    {matchDetail.relevance_tier} MATCH
                  </span>
                  <span className="text-xs font-semibold text-violet-700 dark:text-violet-300">
                    {matchDetail.semantic_score != null
                      ? `${Math.round(matchDetail.semantic_score * 100)}% Similarity`
                      : "Semantic Matching Inactive"}
                  </span>
                </div>
              </div>

              {/* Score Progress Bar */}
              <div>
                <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2 overflow-hidden">
                  <div
                    className="bg-violet-600 h-2 rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.min(
                        100,
                        Math.max(0, (matchDetail.semantic_score ?? 0) * 100)
                      )}%`,
                    }}
                  />
                </div>
                <div className="flex justify-between items-center text-[10px] text-slate-500 dark:text-slate-400 mt-1">
                  <span>Deterministic Score: {Math.round(matchDetail.deterministic_score)}%</span>
                  <span>Blended Rank Score: {Math.round(matchDetail.blended_score)}%</span>
                </div>
              </div>

              {/* Shared and Missing Skills Breakdown */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1 text-xs">
                {/* Shared Skills */}
                <div className="space-y-1">
                  <span className="font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Shared / Matching Skills ({matchDetail.shared_skills.length})
                  </span>
                  {matchDetail.shared_skills.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {matchDetail.shared_skills.map((skill, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 bg-emerald-50 dark:bg-emerald-950/50 text-emerald-800 dark:text-emerald-300 rounded text-[11px] font-medium"
                        >
                          {skill}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="text-slate-400 text-[11px] italic">No direct keyword overlap</span>
                  )}
                </div>

                {/* Missing Skills */}
                <div className="space-y-1">
                  <span className="font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
                    <AlertCircle className="w-3.5 h-3.5" /> Missing Requirements ({matchDetail.missing_skills.length})
                  </span>
                  {matchDetail.missing_skills.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {matchDetail.missing_skills.map((skill, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 rounded text-[11px] font-medium"
                        >
                          {skill}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="text-slate-400 text-[11px] italic">No missing required skills</span>
                  )}
                </div>
              </div>

              {/* Model and Sparse Description Notes */}
              <div className="flex items-center justify-between text-[10px] text-slate-400 pt-1 border-t border-violet-100 dark:border-violet-900/30">
                <span className="flex items-center gap-1">
                  <Cpu className="w-3 h-3 text-slate-400" /> Model: all-MiniLM-L6-v2 (Local CPU)
                </span>
                {matchDetail.is_sparse_description && (
                  <span className="text-amber-500 font-medium">
                    ⚠️ Short description; score confidence adjusted
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Job Description */}
          <div className="space-y-2">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Job Description
            </h4>
            <div className="p-4 bg-slate-50 dark:bg-slate-950/40 rounded-lg border border-slate-200 dark:border-slate-800 max-h-96 overflow-y-auto text-xs leading-relaxed text-slate-700 dark:text-slate-300 font-sans whitespace-pre-wrap selection:bg-teal-100 dark:selection:bg-teal-900">
              {job.description ? (
                job.description
              ) : (
                <span className="italic text-slate-400">No raw description available.</span>
              )}
            </div>
          </div>

          {/* Technical Diagnostics / Dedup Hashes */}
          <details className="group text-xs border border-slate-200 dark:border-slate-800 rounded-lg p-3">
            <summary className="font-medium text-slate-600 dark:text-slate-400 cursor-pointer list-none flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Hash className="w-3.5 h-3.5" />
                Ingestion Integrity & Dedup Hashes
              </span>
              <span className="text-[10px] uppercase font-semibold text-teal-600 group-open:rotate-180 transition-transform">
                ▼
              </span>
            </summary>
            <div className="mt-3 space-y-1.5 pt-2 border-t border-slate-100 dark:border-slate-800 text-[11px] font-mono">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
                <span className="text-slate-500">Fingerprint Hash:</span>
                <span className="text-slate-700 dark:text-slate-300 select-all truncate max-w-sm">
                  {job.fingerprint_hash}
                </span>
              </div>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
                <span className="text-slate-500">Content Hash:</span>
                <span className="text-slate-700 dark:text-slate-300 select-all truncate max-w-sm">
                  {job.content_hash}
                </span>
              </div>
            </div>
          </details>

          <div className="flex justify-end pt-2">
            <Button variant="secondary" onClick={handleClose}>
              Close
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
};
