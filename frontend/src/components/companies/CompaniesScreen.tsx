import React, { useEffect, useState, useCallback, useMemo } from "react";
import {
  Building2,
  Plus,
  Play,
  ExternalLink,
  Edit2,
  Trash2,
  Check,
  Search,
  ShieldCheck,
  Heart,
  Globe,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { useAuth } from "../../context/AuthContext";
import {
  CompanyRead,
  CompanyCreate,
  CompanyUpdate,
  CompanyPlatform,
  ScanStateRead,
} from "../../types/api";
import { companiesApi, subscriptionsApi } from "../../services/api";
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
  const { isAuthenticated, openAuthModal } = useAuth();

  const [companies, setCompanies] = useState<CompanyRead[]>([]);
  const [subscribedIds, setSubscribedIds] = useState<Set<number>>(new Set());
  const [scanStates, setScanStates] = useState<Record<number, ScanStateRead>>({});
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [scanningCompanyId, setScanningCompanyId] = useState<number | null>(null);
  const [subscribingId, setSubscribingId] = useState<number | null>(null);

  // Tab & Search state
  const [activeCatalogTab, setActiveCatalogTab] = useState<"curated" | "following" | "all">("curated");
  const [searchTerm, setSearchTerm] = useState<string>("");

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
      const [compRes, subRes] = await Promise.allSettled([
        companiesApi.list(),
        isAuthenticated ? subscriptionsApi.list() : Promise.resolve([]),
      ]);

      if (compRes.status === "fulfilled") {
        setCompanies(compRes.value.items);

        // Load scan states asynchronously
        const statePromises = compRes.value.items.map(async (c) => {
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
      }

      if (subRes.status === "fulfilled" && Array.isArray(subRes.value)) {
        const idSet = new Set<number>(subRes.value.map((s) => s.company_id));
        setSubscribedIds(idSet);
      } else {
        setSubscribedIds(new Set());
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load companies";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [isAuthenticated, showToast]);

  useEffect(() => {
    fetchCompanies();
  }, [fetchCompanies]);

  // Handle Follow / Unfollow toggle
  const handleToggleFollow = async (company: CompanyRead) => {
    if (!isAuthenticated) {
      openAuthModal("login");
      return;
    }

    const isCurrentlyFollowed = subscribedIds.has(company.id);
    setSubscribingId(company.id);

    try {
      if (isCurrentlyFollowed) {
        await subscriptionsApi.unsubscribe(company.id);
        setSubscribedIds((prev) => {
          const next = new Set(prev);
          next.delete(company.id);
          return next;
        });
        showToast(`Unfollowed ${company.name}`, "info");
      } else {
        await subscriptionsApi.subscribe({ company_id: company.id });
        setSubscribedIds((prev) => {
          const next = new Set(prev);
          next.add(company.id);
          return next;
        });
        showToast(`Now following ${company.name}! Background monitoring active.`, "success");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Subscription update failed";
      showToast(msg, "error");
    } finally {
      setSubscribingId(null);
    }
  };

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
      const created = await companiesApi.create(payload);
      showToast(`Company "${payload.name}" added successfully.`, "success");

      // If user is authenticated, automatically follow the custom company
      if (isAuthenticated) {
        try {
          await subscriptionsApi.subscribe({ company_id: created.id });
          setSubscribedIds((prev) => new Set(prev).add(created.id));
        } catch {
          // Non-fatal
        }
      }

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
      showToast(`Updated "${editingCompany.name}" successfully.`, "success");
      setEditingCompany(null);
      await fetchCompanies();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update company";
      setFormError(msg);
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleTriggerScan = async (companyId: number, companyName: string) => {
    setScanningCompanyId(companyId);
    try {
      const res = await companiesApi.triggerScan(companyId);
      const inserted = res.stats?.newly_inserted ?? 0;
      const total = res.stats?.total_fetched ?? 0;
      showToast(
        `Scanned ${companyName}: found ${total} jobs (${inserted} new).`,
        "success"
      );
      await fetchCompanies();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Scan failed";
      showToast(msg, "error");
    } finally {
      setScanningCompanyId(null);
    }
  };

  const handleDelete = async () => {
    if (!deletingCompany) return;
    setFormSubmitting(true);
    try {
      await companiesApi.delete(deletingCompany.id);
      showToast(`Deleted "${deletingCompany.name}".`, "info");
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

  // Filtered companies based on tabs and search
  const filteredCompanies = useMemo(() => {
    let list = companies;

    // Filter by tab
    if (activeCatalogTab === "curated") {
      list = list.filter((c) => c.is_curated);
      // Fallback if none are curated yet in db
      if (list.length === 0) list = companies;
    } else if (activeCatalogTab === "following") {
      list = list.filter((c) => subscribedIds.has(c.id));
    }

    // Filter by search term
    if (searchTerm.trim()) {
      const term = searchTerm.toLowerCase();
      list = list.filter(
        (c) =>
          c.name.toLowerCase().includes(term) ||
          c.platform.toLowerCase().includes(term) ||
          c.career_url.toLowerCase().includes(term)
      );
    }

    return list;
  }, [companies, activeCatalogTab, subscribedIds, searchTerm]);

  return (
    <div className="space-y-6" data-testid="companies-screen">
      {/* Top Catalog & Filter Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800">
        {/* Tabs: Curated Directory, Following, All */}
        <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 p-1 rounded-lg border border-slate-200 dark:border-slate-700">
          <button
            onClick={() => setActiveCatalogTab("curated")}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${
              activeCatalogTab === "curated"
                ? "bg-white dark:bg-slate-900 text-teal-700 dark:text-teal-300 shadow-xs"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
            data-testid="tab-curated-companies"
          >
            <ShieldCheck className="w-3.5 h-3.5 text-teal-600" />
            Curated Catalog
          </button>
          <button
            onClick={() => setActiveCatalogTab("following")}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${
              activeCatalogTab === "following"
                ? "bg-white dark:bg-slate-900 text-teal-700 dark:text-teal-300 shadow-xs"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
            data-testid="tab-following-companies"
          >
            <Heart className="w-3.5 h-3.5 text-rose-500" />
            Followed ({subscribedIds.size})
          </button>
          <button
            onClick={() => setActiveCatalogTab("all")}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${
              activeCatalogTab === "all"
                ? "bg-white dark:bg-slate-900 text-teal-700 dark:text-teal-300 shadow-xs"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
            data-testid="tab-all-companies"
          >
            <Globe className="w-3.5 h-3.5 text-slate-500" />
            All Companies ({companies.length})
          </button>
        </div>

        {/* Search & Add Target */}
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Filter companies..."
              className="pl-9 pr-3 py-1.5 text-xs bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500 w-48 sm:w-56"
            />
          </div>

          <Button variant="primary" size="sm" onClick={openAddModal} data-testid="btn-add-company">
            <Plus className="w-4 h-4 mr-1.5" />
            Add Target
          </Button>
        </div>
      </div>

      {/* Main Companies Grid */}
      {isLoading ? (
        <div className="py-20 flex justify-center">
          <LoadingSpinner size="lg" label="Loading companies..." />
        </div>
      ) : filteredCompanies.length === 0 ? (
        <EmptyState
          icon={<Building2 className="w-8 h-8 text-teal-600" />}
          title={
            activeCatalogTab === "following"
              ? "No followed companies yet"
              : "No companies found"
          }
          description={
            activeCatalogTab === "following"
              ? "Follow curated companies from the catalog to start monitoring their career portals for matching openings."
              : "No companies match your current search criteria. Try a different search term or add a new target."
          }
          action={
            activeCatalogTab === "following" ? (
              <Button variant="primary" onClick={() => setActiveCatalogTab("curated")}>
                Browse Curated Catalog
              </Button>
            ) : (
              <Button variant="primary" onClick={openAddModal}>
                Add Monitored Company
              </Button>
            )
          }
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredCompanies.map((company) => {
            const scan = scanStates[company.id];
            const isScanning = scanningCompanyId === company.id;
            const isFollowed = subscribedIds.has(company.id);
            const isFollowLoading = subscribingId === company.id;

            return (
              <div
                key={company.id}
                className={`bg-white dark:bg-slate-900 border rounded-xl p-5 shadow-xs space-y-4 transition-all ${
                  isFollowed
                    ? "border-teal-500/50 ring-1 ring-teal-500/20"
                    : "border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700"
                }`}
                data-testid={`company-card-${company.id}`}
              >
                {/* Header: Name, Badges & Follow Button */}
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-bold text-slate-900 dark:text-slate-100 text-sm">
                        {company.name}
                      </h3>
                      {company.is_curated && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-teal-50 text-teal-800 dark:bg-teal-950/60 dark:text-teal-300 border border-teal-200 dark:border-teal-800 flex items-center gap-1">
                          <ShieldCheck className="w-3 h-3 text-teal-600" />
                          Curated
                        </span>
                      )}
                    </div>
                    <Badge variant="neutral" size="sm">
                      {company.platform.toUpperCase()}
                    </Badge>
                  </div>

                  {/* Follow / Unfollow Button */}
                  <Button
                    variant={isFollowed ? "secondary" : "primary"}
                    size="sm"
                    onClick={() => handleToggleFollow(company)}
                    isLoading={isFollowLoading}
                    className="text-xs"
                    data-testid={`follow-company-btn-${company.id}`}
                  >
                    {isFollowed ? (
                      <>
                        <Check className="w-3.5 h-3.5 mr-1 text-teal-600" />
                        Following
                      </>
                    ) : (
                      <>
                        <Plus className="w-3.5 h-3.5 mr-1" />
                        Follow
                      </>
                    )}
                  </Button>
                </div>

                {/* Career Portal URL */}
                <div className="text-xs">
                  <span className="text-slate-400 block mb-0.5 font-medium">Career Portal:</span>
                  <a
                    href={company.career_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-teal-600 dark:text-teal-400 hover:underline flex items-center gap-1 truncate"
                  >
                    <span className="truncate">{company.career_url}</span>
                    <ExternalLink className="w-3 h-3 flex-shrink-0" />
                  </a>
                </div>

                {/* Scan Status Summary */}
                <div className="p-2.5 bg-slate-50 dark:bg-slate-950/50 rounded-lg text-xs space-y-1 border border-slate-100 dark:border-slate-800/80">
                  <div className="flex justify-between items-center text-slate-600 dark:text-slate-400">
                    <span>Scan Status:</span>
                    <span className="font-semibold text-slate-900 dark:text-slate-200">
                      {scan?.status || (company.is_active ? "READY" : "INACTIVE")}
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-slate-600 dark:text-slate-400">
                    <span>Last Scan:</span>
                    <span>
                      {scan?.last_scan_completed_at
                        ? new Date(scan.last_scan_completed_at).toLocaleDateString()
                        : "Never"}
                    </span>
                  </div>
                </div>

                {/* Card Footer Actions */}
                <div className="pt-2 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleTriggerScan(company.id, company.name)}
                    isLoading={isScanning}
                    className="text-xs"
                    data-testid={`btn-scan-company-${company.id}`}
                  >
                    <Play className="w-3.5 h-3.5 mr-1" />
                    Scan Now
                  </Button>

                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => openEditModal(company)}
                      className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 rounded"
                      title="Edit Company"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    {!company.is_curated && (
                      <button
                        onClick={() => setDeletingCompany(company)}
                        className="p-1.5 text-slate-400 hover:text-rose-600 rounded"
                        title="Delete Company"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Add Target Modal */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add Company Career Target"
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
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
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
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
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
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
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
              Active for scheduled background monitoring
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
              Save &amp; Follow Company
            </Button>
          </div>
        </form>
      </Modal>

      {/* Edit Company Modal */}
      {editingCompany && (
        <Modal
          isOpen={true}
          onClose={() => setEditingCompany(null)}
          title={`Edit ${editingCompany.name}`}
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
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
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
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                ATS Platform *
              </label>
              <select
                value={formPlatform}
                onChange={(e) => setFormPlatform(e.target.value as CompanyPlatform)}
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
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
                Active for scheduled monitoring
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
              <Button type="submit" variant="primary" isLoading={formSubmitting}>
                Save Changes
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* Delete Confirmation Modal */}
      {deletingCompany && (
        <Modal
          isOpen={true}
          onClose={() => setDeletingCompany(null)}
          title="Confirm Deletion"
          size="sm"
        >
          <div className="space-y-4">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Are you sure you want to delete{" "}
              <strong className="text-slate-900 dark:text-slate-100">
                {deletingCompany.name}
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
      )}
    </div>
  );
};
