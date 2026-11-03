import React, { useEffect, useState, useCallback } from "react";
import {
  Building2,
  Plus,
  Play,
  ExternalLink,
  Edit2,
  Trash2,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import {
  CompanyRead,
  CompanyCreate,
  CompanyUpdate,
  CompanyPlatform,
  ScanStateRead,
} from "../../types/api";
import { companiesApi } from "../../services/api";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { Modal } from "../common/Modal";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EmptyState } from "../common/EmptyState";

const PLATFORMS: { value: CompanyPlatform; label: string }[] = [
  { value: "greenhouse", label: "Greenhouse" },
  { value: "lever", label: "Lever" },
  { value: "ashby", label: "Ashby" },
  { value: "workday", label: "Workday" },
  { value: "generic", label: "Generic / Custom" },
];

export const CompaniesScreen: React.FC = () => {
  const { showToast, refreshAll } = useApp();

  const [companies, setCompanies] = useState<CompanyRead[]>([]);
  const [scanStates, setScanStates] = useState<Record<number, ScanStateRead>>({});
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [scanningCompanyId, setScanningCompanyId] = useState<number | null>(null);

  // Modal States
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [editingCompany, setEditingCompany] = useState<CompanyRead | null>(null);
  const [deletingCompany, setDeletingCompany] = useState<CompanyRead | null>(null);

  // Form Fields
  const [formName, setFormName] = useState<string>("");
  const [formUrl, setFormUrl] = useState<string>("");
  const [formPlatform, setFormPlatform] = useState<CompanyPlatform>("greenhouse");
  const [formIsActive, setFormIsActive] = useState<boolean>(true);
  const [formSubmitting, setFormSubmitting] = useState<boolean>(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchCompanies = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await companiesApi.list();
      setCompanies(res.items);

      // Load scan states asynchronously
      const statePromises = res.items.map(async (c) => {
        try {
          const s = await companiesApi.getScanState(c.id);
          return { id: c.id, state: s };
        } catch {
          return null;
        }
      });

      const resolved = await Promise.all(statePromises);
      const stateMap: Record<number, ScanStateRead> = {};
      resolved.forEach((item) => {
        if (item) stateMap[item.id] = item.state;
      });
      setScanStates(stateMap);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load companies";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    fetchCompanies();
  }, [fetchCompanies]);

  const openAddModal = () => {
    setFormName("");
    setFormUrl("");
    setFormPlatform("greenhouse");
    setFormIsActive(true);
    setFormError(null);
    setIsAddModalOpen(true);
  };

  const openEditModal = (company: CompanyRead) => {
    setEditingCompany(company);
    setFormName(company.name);
    setFormUrl(company.career_url);
    setFormPlatform((company.platform as CompanyPlatform) || "greenhouse");
    setFormIsActive(company.is_active);
    setFormError(null);
  };

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim() || !formUrl.trim()) {
      setFormError("Company name and career URL are required.");
      return;
    }

    setFormSubmitting(true);
    setFormError(null);
    try {
      const payload: CompanyCreate = {
        name: formName.trim(),
        career_url: formUrl.trim(),
        platform: formPlatform,
        is_active: formIsActive,
      };
      await companiesApi.create(payload);
      showToast(`Company "${payload.name}" added successfully.`, "success");
      setIsAddModalOpen(false);
      await fetchCompanies();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to add company";
      setFormError(msg);
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingCompany) return;
    if (!formName.trim() || !formUrl.trim()) {
      setFormError("Company name and career URL are required.");
      return;
    }

    setFormSubmitting(true);
    setFormError(null);
    try {
      const payload: CompanyUpdate = {
        name: formName.trim(),
        career_url: formUrl.trim(),
        platform: formPlatform,
        is_active: formIsActive,
      };
      await companiesApi.update(editingCompany.id, payload);
      showToast(`Company "${payload.name}" updated.`, "success");
      setEditingCompany(null);
      await fetchCompanies();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update company";
      setFormError(msg);
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingCompany) return;
    setFormSubmitting(true);
    try {
      await companiesApi.delete(deletingCompany.id);
      showToast(`Company "${deletingCompany.name}" deleted.`, "info");
      setDeletingCompany(null);
      await fetchCompanies();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to delete company";
      showToast(msg, "error");
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleToggleActive = async (company: CompanyRead) => {
    try {
      await companiesApi.update(company.id, { is_active: !company.is_active });
      showToast(
        `"${company.name}" ${!company.is_active ? "activated" : "deactivated"}.`,
        "info"
      );
      await fetchCompanies();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to toggle status";
      showToast(msg, "error");
    }
  };

  const handleTriggerScan = async (company: CompanyRead) => {
    setScanningCompanyId(company.id);
    try {
      const res = await companiesApi.triggerScan(company.id);
      const fetched = res.stats?.total_fetched ?? 0;
      const inserted = res.stats?.newly_inserted ?? 0;
      showToast(
        `Scan for "${company.name}" completed: fetched ${fetched}, inserted ${inserted} jobs.`,
        "success"
      );
      await fetchCompanies();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Career scan failed";
      showToast(msg, "error");
    } finally {
      setScanningCompanyId(null);
    }
  };

  const getScanStateBadge = (state: string | undefined) => {
    switch (state) {
      case "RUNNING":
        return <Badge variant="brand" size="sm">RUNNING</Badge>;
      case "FAILED":
        return <Badge variant="danger" size="sm">FAILED</Badge>;
      case "RATE_LIMITED":
        return <Badge variant="warning" size="sm">RATE LIMITED</Badge>;
      case "IDLE":
      default:
        return <Badge variant="neutral" size="sm">{state || "IDLE"}</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Action Bar */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Building2 className="w-5 h-5 text-teal-600 dark:text-teal-400" />
            Monitored Career Sites
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {companies.length} configured targets. Scheduled scans run automatically through APScheduler.
          </p>
        </div>

        <Button
          variant="primary"
          onClick={openAddModal}
          leftIcon={<Plus className="w-4 h-4" />}
          data-testid="btn-add-company"
        >
          Add Company
        </Button>
      </div>

      {/* Companies Table */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden shadow-xs">
        {isLoading ? (
          <div className="py-20">
            <LoadingSpinner size="lg" label="Loading monitored companies..." />
          </div>
        ) : companies.length === 0 ? (
          <div className="py-16">
            <EmptyState
              title="No companies configured"
              description="Add your first company career site to start automated job discovery."
              actionLabel="Add Company"
              onAction={openAddModal}
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" data-testid="companies-table">
              <thead className="bg-slate-50 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-800 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                <tr>
                  <th className="py-3 px-4">Company</th>
                  <th className="py-3 px-4">Platform</th>
                  <th className="py-3 px-4">Monitoring</th>
                  <th className="py-3 px-4">Scan State</th>
                  <th className="py-3 px-4">Last Scanned</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {companies.map((company) => {
                  const isScanning = scanningCompanyId === company.id;
                  const state = scanStates[company.id];

                  return (
                    <tr
                      key={company.id}
                      className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors"
                      data-testid={`company-row-${company.id}`}
                    >
                      <td className="py-3.5 px-4">
                        <div className="font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                          <span>{company.name}</span>
                          <a
                            href={company.career_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-slate-400 hover:text-teal-600 dark:hover:text-teal-400"
                            title={company.career_url}
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </a>
                        </div>
                        <div className="text-xs text-slate-500 font-mono truncate max-w-xs mt-0.5">
                          {company.career_url}
                        </div>
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <Badge variant="brand" size="sm">
                          {company.platform.toUpperCase()}
                        </Badge>
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <button
                          onClick={() => handleToggleActive(company)}
                          className="flex items-center gap-1.5 focus:outline-hidden"
                          title="Click to toggle monitoring status"
                        >
                          {company.is_active ? (
                            <Badge variant="success" size="sm">
                              ACTIVE
                            </Badge>
                          ) : (
                            <Badge variant="neutral" size="sm">
                              PAUSED
                            </Badge>
                          )}
                        </button>
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <div className="space-y-1">
                          {getScanStateBadge(state?.status)}
                          {state?.last_error && (
                            <div
                              className="text-[11px] text-rose-500 dark:text-rose-400 truncate max-w-xs"
                              title={state.last_error}
                            >
                              {state.last_error}
                            </div>
                          )}
                        </div>
                      </td>

                      <td className="py-3.5 px-4 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                        {state?.last_scan_completed_at ? (
                          <div>
                            <div>{new Date(state.last_scan_completed_at).toLocaleDateString()}</div>
                            <div className="text-[11px] text-slate-400">
                              {new Date(state.last_scan_completed_at).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </div>
                          </div>
                        ) : (
                          <span className="italic text-slate-400">Never</span>
                        )}
                      </td>

                      <td className="py-3.5 px-4 text-right whitespace-nowrap">
                        <div className="flex items-center justify-end gap-1.5">
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleTriggerScan(company)}
                            isLoading={isScanning}
                            leftIcon={<Play className="w-3 h-3" />}
                            title="Trigger immediate scrape and ingestion"
                            data-testid={`btn-scan-company-${company.id}`}
                          >
                            Scan Now
                          </Button>

                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openEditModal(company)}
                            title="Edit company"
                            data-testid={`btn-edit-company-${company.id}`}
                          >
                            <Edit2 className="w-3.5 h-3.5 text-slate-500" />
                          </Button>

                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setDeletingCompany(company)}
                            title="Delete company"
                            data-testid={`btn-delete-company-${company.id}`}
                          >
                            <Trash2 className="w-3.5 h-3.5 text-rose-500" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add Company Modal */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add Monitored Company"
        size="md"
      >
        <form onSubmit={handleAddSubmit} className="space-y-4">
          {formError && (
            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 text-xs rounded-lg border border-rose-200 dark:border-rose-800">
              {formError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Company Name *
            </label>
            <input
              type="text"
              required
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. Stripe, OpenAI, Figma"
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="input-company-name"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Career Site URL *
            </label>
            <input
              type="url"
              required
              value={formUrl}
              onChange={(e) => setFormUrl(e.target.value)}
              placeholder="https://boards.greenhouse.io/stripe"
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="input-company-url"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              ATS Platform *
            </label>
            <select
              value={formPlatform}
              onChange={(e) => setFormPlatform(e.target.value as CompanyPlatform)}
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="select-company-platform"
            >
              {PLATFORMS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <input
              type="checkbox"
              id="is_active_checkbox"
              checked={formIsActive}
              onChange={(e) => setFormIsActive(e.target.checked)}
              className="rounded text-teal-600 focus:ring-teal-500 h-4 w-4 border-slate-300 dark:border-slate-700"
            />
            <label
              htmlFor="is_active_checkbox"
              className="text-xs font-medium text-slate-700 dark:text-slate-300 cursor-pointer"
            >
              Enable automated monitoring immediately
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-4 border-t border-slate-100 dark:border-slate-800">
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
              isLoading={formSubmitting}
              data-testid="btn-save-company"
            >
              Save Company
            </Button>
          </div>
        </form>
      </Modal>

      {/* Edit Company Modal */}
      <Modal
        isOpen={editingCompany !== null}
        onClose={() => setEditingCompany(null)}
        title={`Edit ${editingCompany?.name || "Company"}`}
        size="md"
      >
        <form onSubmit={handleEditSubmit} className="space-y-4">
          {formError && (
            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 text-xs rounded-lg border border-rose-200 dark:border-rose-800">
              {formError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Company Name *
            </label>
            <input
              type="text"
              required
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Career Site URL *
            </label>
            <input
              type="url"
              required
              value={formUrl}
              onChange={(e) => setFormUrl(e.target.value)}
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              ATS Platform *
            </label>
            <select
              value={formPlatform}
              onChange={(e) => setFormPlatform(e.target.value as CompanyPlatform)}
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
            >
              {PLATFORMS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <input
              type="checkbox"
              id="edit_is_active_checkbox"
              checked={formIsActive}
              onChange={(e) => setFormIsActive(e.target.checked)}
              className="rounded text-teal-600 focus:ring-teal-500 h-4 w-4 border-slate-300 dark:border-slate-700"
            />
            <label
              htmlFor="edit_is_active_checkbox"
              className="text-xs font-medium text-slate-700 dark:text-slate-300 cursor-pointer"
            >
              Active for automated scheduler monitoring
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-4 border-t border-slate-100 dark:border-slate-800">
            <Button
              type="button"
              variant="secondary"
              onClick={() => setEditingCompany(null)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              isLoading={formSubmitting}
            >
              Save Changes
            </Button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={deletingCompany !== null}
        onClose={() => setDeletingCompany(null)}
        title="Confirm Deletion"
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Are you sure you want to delete{" "}
            <strong className="text-slate-900 dark:text-slate-100">
              {deletingCompany?.name}
            </strong>
            ? This will stop future monitoring of this career site.
          </p>

          <div className="flex justify-end gap-2 pt-4 border-t border-slate-100 dark:border-slate-800">
            <Button
              variant="secondary"
              onClick={() => setDeletingCompany(null)}
              disabled={formSubmitting}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={handleDelete}
              isLoading={formSubmitting}
              data-testid="btn-confirm-delete-company"
            >
              Delete Company
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
