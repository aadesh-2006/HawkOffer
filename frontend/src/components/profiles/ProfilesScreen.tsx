import React, { useEffect, useState, useCallback } from "react";
import {
  Sliders,
  Plus,
  Edit2,
  Trash2,
  Tag,
  MapPin,
  Briefcase,
  Ban,
  GraduationCap,
  Info,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import {
  PreferenceProfileRead,
  PreferenceProfileCreate,
  PreferenceProfileUpdate,
} from "../../types/api";
import { profilesApi } from "../../services/api";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { Modal } from "../common/Modal";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EmptyState } from "../common/EmptyState";

export const ProfilesScreen: React.FC = () => {
  const { showToast, refreshAll } = useApp();

  const [profiles, setProfiles] = useState<PreferenceProfileRead[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Modals
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [editingProfile, setEditingProfile] = useState<PreferenceProfileRead | null>(null);
  const [deletingProfile, setDeletingProfile] = useState<PreferenceProfileRead | null>(null);

  // Form states
  const [formName, setFormName] = useState<string>("");
  const [formRoles, setFormRoles] = useState<string>("");
  const [formLocations, setFormLocations] = useState<string>("");
  const [formSkills, setFormSkills] = useState<string>("");
  const [formExclusions, setFormExclusions] = useState<string>("");
  const [formGradYear, setFormGradYear] = useState<string>("");
  const [formScoreThreshold, setFormScoreThreshold] = useState<number>(70);
  const [formIsActive, setFormIsActive] = useState<boolean>(true);
  const [formSubmitting, setFormSubmitting] = useState<boolean>(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchProfiles = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await profilesApi.list();
      setProfiles(res.items);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load profiles";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    fetchProfiles();
  }, [fetchProfiles]);

  const openAddModal = () => {
    setFormName("");
    setFormRoles("");
    setFormLocations("");
    setFormSkills("");
    setFormExclusions("");
    setFormGradYear("");
    setFormScoreThreshold(70);
    setFormIsActive(true);
    setFormError(null);
    setIsAddModalOpen(true);
  };

  const openEditModal = (p: PreferenceProfileRead) => {
    setEditingProfile(p);
    setFormName(p.name);
    setFormRoles((p.preferred_roles || []).join(", "));
    setFormLocations((p.preferred_locations || []).join(", "));
    setFormSkills((p.preferred_skills || []).join(", "));
    setFormExclusions((p.excluded_keywords || []).join(", "));
    setFormGradYear(p.graduation_year ? String(p.graduation_year) : "");
    setFormScoreThreshold(p.minimum_match_score || 0);
    setFormIsActive(p.is_active);
    setFormError(null);
  };

  const parseCommaList = (str: string): string[] => {
    return str
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  };

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) {
      setFormError("Profile name is required.");
      return;
    }

    setFormSubmitting(true);
    setFormError(null);
    try {
      const gradYearNum = formGradYear.trim() ? Number(formGradYear.trim()) : null;
      const payload: PreferenceProfileCreate = {
        name: formName.trim(),
        preferred_roles: parseCommaList(formRoles),
        preferred_locations: parseCommaList(formLocations),
        preferred_skills: parseCommaList(formSkills),
        excluded_keywords: parseCommaList(formExclusions),
        graduation_year: gradYearNum,
        minimum_match_score: Number(formScoreThreshold),
        is_active: formIsActive,
      };

      await profilesApi.create(payload);
      showToast(`Preference profile "${payload.name}" created.`, "success");
      setIsAddModalOpen(false);
      await fetchProfiles();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to create profile";
      setFormError(msg);
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingProfile) return;
    if (!formName.trim()) {
      setFormError("Profile name is required.");
      return;
    }

    setFormSubmitting(true);
    setFormError(null);
    try {
      const gradYearNum = formGradYear.trim() ? Number(formGradYear.trim()) : null;
      const payload: PreferenceProfileUpdate = {
        name: formName.trim(),
        preferred_roles: parseCommaList(formRoles),
        preferred_locations: parseCommaList(formLocations),
        preferred_skills: parseCommaList(formSkills),
        excluded_keywords: parseCommaList(formExclusions),
        graduation_year: gradYearNum,
        minimum_match_score: Number(formScoreThreshold),
        is_active: formIsActive,
      };

      const updated = await profilesApi.update(editingProfile.id, payload);
      showToast(
        `Profile updated to revision v${updated.revision}. Background reevaluation enqueued.`,
        "success"
      );
      setEditingProfile(null);
      await fetchProfiles();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update profile";
      setFormError(msg);
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingProfile) return;
    setFormSubmitting(true);
    try {
      await profilesApi.delete(deletingProfile.id);
      showToast(`Profile "${deletingProfile.name}" deleted.`, "info");
      setDeletingProfile(null);
      await fetchProfiles();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to delete profile";
      showToast(msg, "error");
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleToggleActive = async (profile: PreferenceProfileRead) => {
    try {
      const updated = await profilesApi.update(profile.id, { is_active: !profile.is_active });
      showToast(
        `"${profile.name}" ${updated.is_active ? "activated" : "deactivated"}. (Revision v${updated.revision})`,
        "info"
      );
      await fetchProfiles();
      await refreshAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to toggle profile status";
      showToast(msg, "error");
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Sliders className="w-5 h-5 text-teal-600 dark:text-teal-400" />
            Target Preference Profiles
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Matching engine filters ingested jobs according to these profiles. Updates increment revisions and automatically trigger historical reevaluations.
          </p>
        </div>

        <Button
          variant="primary"
          onClick={openAddModal}
          leftIcon={<Plus className="w-4 h-4" />}
          data-testid="btn-add-profile"
        >
          Create Profile
        </Button>
      </div>

      {/* Profiles Cards Grid */}
      {isLoading ? (
        <div className="py-20">
          <LoadingSpinner size="lg" label="Loading preference profiles..." />
        </div>
      ) : profiles.length === 0 ? (
        <div className="py-16 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800">
          <EmptyState
            title="No preference profiles defined"
            description="Create a profile to enable personalized job filtering, scoring, and notification backlog generation."
            actionLabel="Create Profile"
            onAction={openAddModal}
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5" data-testid="profiles-list">
          {profiles.map((profile) => (
            <div
              key={profile.id}
              className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 space-y-4 shadow-xs hover:border-slate-300 dark:hover:border-slate-700 transition-all flex flex-col justify-between"
              data-testid={`profile-card-${profile.id}`}
            >
              <div className="space-y-3">
                {/* Profile Header */}
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h4 className="font-bold text-base text-slate-900 dark:text-slate-100">
                        {profile.name}
                      </h4>
                      <Badge variant="brand" size="sm">
                        v{profile.revision}
                      </Badge>
                      <button
                        onClick={() => handleToggleActive(profile)}
                        className="cursor-pointer focus:outline-hidden"
                        title="Click to toggle active status"
                      >
                        <Badge
                          variant={profile.is_active ? "success" : "neutral"}
                          size="sm"
                        >
                          {profile.is_active ? "ACTIVE" : "INACTIVE"}
                        </Badge>
                      </button>
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      Minimum Match Score:{" "}
                      <strong className="text-teal-600 dark:text-teal-400 font-semibold">
                        {profile.minimum_match_score}%
                      </strong>
                    </div>
                  </div>

                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openEditModal(profile)}
                      title="Edit profile"
                      data-testid={`btn-edit-profile-${profile.id}`}
                    >
                      <Edit2 className="w-3.5 h-3.5 text-slate-500" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDeletingProfile(profile)}
                      title="Delete profile"
                      data-testid={`btn-delete-profile-${profile.id}`}
                    >
                      <Trash2 className="w-3.5 h-3.5 text-rose-500" />
                    </Button>
                  </div>
                </div>

                {/* Target Tags Sections */}
                <div className="space-y-2.5 text-xs pt-1">
                  {/* Roles */}
                  {(profile.preferred_roles || []).length > 0 && (
                    <div className="space-y-1">
                      <span className="font-semibold text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
                        <Briefcase className="w-3.5 h-3.5" /> Target Roles
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {profile.preferred_roles.map((r, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 rounded font-medium text-[11px]"
                          >
                            {r}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Locations */}
                  {(profile.preferred_locations || []).length > 0 && (
                    <div className="space-y-1">
                      <span className="font-semibold text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
                        <MapPin className="w-3.5 h-3.5" /> Locations
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {profile.preferred_locations.map((loc, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 bg-teal-50 dark:bg-teal-950/40 text-teal-800 dark:text-teal-300 rounded font-medium text-[11px]"
                          >
                            {loc}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Skills */}
                  {(profile.preferred_skills || []).length > 0 && (
                    <div className="space-y-1">
                      <span className="font-semibold text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
                        <Tag className="w-3.5 h-3.5" /> Preferred Skills
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {profile.preferred_skills.map((skill, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded text-[11px]"
                          >
                            {skill}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Exclusions */}
                  {(profile.excluded_keywords || []).length > 0 && (
                    <div className="space-y-1">
                      <span className="font-semibold text-slate-500 dark:text-slate-400 flex items-center gap-1.5 text-rose-600 dark:text-rose-400">
                        <Ban className="w-3.5 h-3.5" /> Excluded Keywords
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {profile.excluded_keywords.map((kw, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 rounded text-[11px]"
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Grad Year */}
                  {profile.graduation_year && (
                    <div className="space-y-1">
                      <span className="font-semibold text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
                        <GraduationCap className="w-3.5 h-3.5" /> Target Class
                      </span>
                      <div className="flex flex-wrap gap-1">
                        <span className="px-2 py-0.5 bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 rounded font-medium text-[11px]">
                          Class of {profile.graduation_year}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Card Footer */}
              <div className="pt-3 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
                <span>Updated {new Date(profile.updated_at).toLocaleDateString()}</span>
                <span>ID: #{profile.id}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Create Preference Profile"
        size="lg"
      >
        <form onSubmit={handleAddSubmit} className="space-y-4">
          {formError && (
            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 text-xs rounded-lg border border-rose-200 dark:border-rose-800">
              {formError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Profile Name *
            </label>
            <input
              type="text"
              required
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. 2026 SWE Internships, Senior Distributed Systems"
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              data-testid="input-profile-name"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Target Roles (comma-separated)
              </label>
              <input
                type="text"
                value={formRoles}
                onChange={(e) => setFormRoles(e.target.value)}
                placeholder="Software Engineer, Backend, Distributed"
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Locations (comma-separated)
              </label>
              <input
                type="text"
                value={formLocations}
                onChange={(e) => setFormLocations(e.target.value)}
                placeholder="Remote, San Francisco, Seattle, New York"
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Preferred Skills (comma-separated)
              </label>
              <input
                type="text"
                value={formSkills}
                onChange={(e) => setFormSkills(e.target.value)}
                placeholder="Python, React, TypeScript, Rust, Go"
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Excluded Keywords (comma-separated)
              </label>
              <input
                type="text"
                value={formExclusions}
                onChange={(e) => setFormExclusions(e.target.value)}
                placeholder="Director, VP, Staff, Principal, Unpaid"
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Target Graduation Year (e.g. 2026)
              </label>
              <input
                type="number"
                value={formGradYear}
                onChange={(e) => setFormGradYear(e.target.value)}
                placeholder="2026"
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Match Score Threshold: {formScoreThreshold}%
              </label>
              <input
                type="range"
                min="0"
                max="100"
                step="5"
                value={formScoreThreshold}
                onChange={(e) => setFormScoreThreshold(Number(e.target.value))}
                className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-600 mt-2"
              />
            </div>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <input
              type="checkbox"
              id="profile_active_checkbox"
              checked={formIsActive}
              onChange={(e) => setFormIsActive(e.target.checked)}
              className="rounded text-teal-600 focus:ring-teal-500 h-4 w-4 border-slate-300 dark:border-slate-700"
            />
            <label
              htmlFor="profile_active_checkbox"
              className="text-xs font-medium text-slate-700 dark:text-slate-300 cursor-pointer"
            >
              Activate this profile immediately
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
              data-testid="btn-save-profile"
            >
              Create Profile
            </Button>
          </div>
        </form>
      </Modal>

      {/* Edit Modal with Revision Notice */}
      <Modal
        isOpen={editingProfile !== null}
        onClose={() => setEditingProfile(null)}
        title={`Edit Profile: ${editingProfile?.name || ""}`}
        size="lg"
      >
        <form onSubmit={handleEditSubmit} className="space-y-4">
          {/* Important Revision and Historical Reevaluation Notice */}
          <div className="p-3.5 bg-teal-50 dark:bg-teal-950/40 rounded-lg border border-teal-200 dark:border-teal-800 text-teal-900 dark:text-teal-200 text-xs flex items-start gap-2.5">
            <Info className="w-4 h-4 text-teal-600 dark:text-teal-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">Automatic Revision Bump &amp; Historical Reevaluation</p>
              <p className="mt-0.5 text-teal-800/90 dark:text-teal-300/90 leading-relaxed">
                Saving updates will automatically bump this profile from{" "}
                <strong>v{editingProfile?.revision}</strong> to{" "}
                <strong>v{(editingProfile?.revision ?? 0) + 1}</strong> and enqueue a background
                historical reevaluation task across all indexed jobs.
              </p>
            </div>
          </div>

          {formError && (
            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 text-xs rounded-lg border border-rose-200 dark:border-rose-800">
              {formError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Profile Name *
            </label>
            <input
              type="text"
              required
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Target Roles (comma-separated)
              </label>
              <input
                type="text"
                value={formRoles}
                onChange={(e) => setFormRoles(e.target.value)}
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Locations (comma-separated)
              </label>
              <input
                type="text"
                value={formLocations}
                onChange={(e) => setFormLocations(e.target.value)}
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Preferred Skills (comma-separated)
              </label>
              <input
                type="text"
                value={formSkills}
                onChange={(e) => setFormSkills(e.target.value)}
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Excluded Keywords (comma-separated)
              </label>
              <input
                type="text"
                value={formExclusions}
                onChange={(e) => setFormExclusions(e.target.value)}
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Target Graduation Year (e.g. 2026)
              </label>
              <input
                type="number"
                value={formGradYear}
                onChange={(e) => setFormGradYear(e.target.value)}
                className="w-full text-sm px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-teal-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Match Score Threshold: {formScoreThreshold}%
              </label>
              <input
                type="range"
                min="0"
                max="100"
                step="5"
                value={formScoreThreshold}
                onChange={(e) => setFormScoreThreshold(Number(e.target.value))}
                className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-600 mt-2"
              />
            </div>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <input
              type="checkbox"
              id="edit_profile_active_checkbox"
              checked={formIsActive}
              onChange={(e) => setFormIsActive(e.target.checked)}
              className="rounded text-teal-600 focus:ring-teal-500 h-4 w-4 border-slate-300 dark:border-slate-700"
            />
            <label
              htmlFor="edit_profile_active_checkbox"
              className="text-xs font-medium text-slate-700 dark:text-slate-300 cursor-pointer"
            >
              Active for real-time and historical evaluations
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-4 border-t border-slate-100 dark:border-slate-800">
            <Button
              type="button"
              variant="secondary"
              onClick={() => setEditingProfile(null)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              isLoading={formSubmitting}
            >
              Save &amp; Reevaluate
            </Button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={deletingProfile !== null}
        onClose={() => setDeletingProfile(null)}
        title="Delete Preference Profile"
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Are you sure you want to delete profile{" "}
            <strong className="text-slate-900 dark:text-slate-100">
              {deletingProfile?.name}
            </strong>
            ? New job matches for this profile will no longer be generated.
          </p>

          <div className="flex justify-end gap-2 pt-4 border-t border-slate-100 dark:border-slate-800">
            <Button
              variant="secondary"
              onClick={() => setDeletingProfile(null)}
              disabled={formSubmitting}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={handleDelete}
              isLoading={formSubmitting}
              data-testid="btn-confirm-delete-profile"
            >
              Delete Profile
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
