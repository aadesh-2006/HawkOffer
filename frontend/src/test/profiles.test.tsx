import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { ProfilesScreen } from "../components/profiles/ProfilesScreen";
import { profilesApi } from "../services/api";

const { mockProfiles } = vi.hoisted(() => ({
  mockProfiles: [
    {
      id: 1,
      name: "2026 SWE Internships",
      graduation_year: 2026,
      preferred_roles: ["Software Engineer", "Backend Developer"],
      preferred_locations: ["Remote", "San Francisco"],
      preferred_skills: ["Python", "Rust"],
      preferred_employment_types: ["internship"],
      required_employment_types: [],
      preferred_workplace_types: [],
      excluded_keywords: ["Senior", "Staff"],
      excluded_title_keywords: [],
      excluded_content_keywords: [],
      required_roles: [],
      required_locations: [],
      require_graduation_year_match: true,
      minimum_match_score: 75,
      is_active: true,
      revision: 2,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ],
}));

vi.mock("../services/api", () => ({
  getApiBaseUrl: () => "http://127.0.0.1:8765/api/v1",
  setApiBaseUrl: vi.fn(),
  systemApi: {
    getHealth: vi.fn().mockResolvedValue({ status: "healthy", database: "connected", scheduler: "running", timestamp: "" }),
    getStatus: vi.fn().mockResolvedValue({ is_running: true, is_lifecycle_gate_open: true, companies_count: 1, active_companies_count: 1, total_jobs_count: 1, unread_notifications_count: 0, pipeline_status: {}, timestamp: "" }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
  profilesApi: {
    list: vi.fn().mockResolvedValue({
      items: mockProfiles,
      total: 1,
      limit: 50,
      offset: 0,
    }),
    create: vi.fn().mockResolvedValue({
      ...mockProfiles[0],
      id: 2,
      name: "New Grad SWE",
      revision: 1,
    }),
    update: vi.fn().mockResolvedValue({
      ...mockProfiles[0],
      revision: 3,
    }),
    delete: vi.fn().mockResolvedValue({ message: "Deleted" }),
  },
}));

describe("ProfilesScreen Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders profile cards with revision badges and role tags", async () => {
    render(
      <AppProvider>
        <ProfilesScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("2026 SWE Internships")).toBeInTheDocument();
      expect(screen.getByText("v2")).toBeInTheDocument();
      expect(screen.getByText("Software Engineer")).toBeInTheDocument();
      expect(screen.getByText("Remote")).toBeInTheDocument();
      expect(screen.getByText("Class of 2026")).toBeInTheDocument();
    });
  });

  it("creates a new preference profile", async () => {
    render(
      <AppProvider>
        <ProfilesScreen />
      </AppProvider>
    );

    fireEvent.click(screen.getByTestId("btn-add-profile"));

    const nameInput = screen.getByTestId("input-profile-name");
    fireEvent.change(nameInput, { target: { value: "New Grad SWE" } });

    fireEvent.click(screen.getByTestId("btn-save-profile"));

    await waitFor(() => {
      expect(profilesApi.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "New Grad SWE",
        })
      );
    });
  });

  it("informs user that updates trigger historical reevaluations", async () => {
    render(
      <AppProvider>
        <ProfilesScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("btn-edit-profile-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-edit-profile-1"));

    expect(
      screen.getByText(/Automatic Revision Bump & Historical Reevaluation/i)
    ).toBeInTheDocument();
  });
});
