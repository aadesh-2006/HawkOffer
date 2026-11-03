import React, { useEffect, useState } from "react";
import {
  MapPin,
  ExternalLink,
  Building2,
  Tag,
  Hash,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { JobRead, CompanyRead } from "../../types/api";
import { jobsApi, companiesApi } from "../../services/api";
import { Modal } from "../common/Modal";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { LoadingSpinner } from "../common/LoadingSpinner";

export const JobDetailModal: React.FC = () => {
  const { selectedJobId, setSelectedJobId, showToast } = useApp();
  const [job, setJob] = useState<JobRead | null>(null);
  const [company, setCompany] = useState<CompanyRead | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedJobId) {
      setJob(null);
      setCompany(null);
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
