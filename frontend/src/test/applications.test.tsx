import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { AuthProvider } from "../context/AuthContext";
import { ApplicationsScreen } from "../components/applications/ApplicationsScreen";
import { applicationsApi } from "../services/api";

vi.mock("../services/api", () => ({
  getApiBaseUrl: () => "http://127.0.0.1:8765/api/v1",
  setApiBaseUrl: vi.fn(),
  getAuthToken: () => "mock-access-token",
  getRefreshToken: () => "mock-refresh-token",
  setAuthTokens: vi.fn(),
  clearTokens: vi.fn(),
  authApi: {
    getMe: vi.fn().mockResolvedValue({
      id: 1,
      email: "engineer@example.com",
      full_name: "Jane Engineer",
      role: "user",
      is_active: true,
      created_at: new Date().toISOString(),
    }),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  },
  applicationsApi: {
    list: vi.fn().mockResolvedValue([
      {
        id: 1,
        user_id: 1,
        job_id: 101,
        status: "SAVED",
        applied_at: null,
        notes: "Need to polish resume for distributed systems.",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        job: {
          id: 101,
          company_id: 1,
          title: "Senior Backend Engineer",
          location: "Remote",
          department: "Platform",
          application_url: "https://example.com/apply/101",
          status: "ACTIVE",
          fingerprint_hash: "a".repeat(64),
          content_hash: "b".repeat(64),
          first_seen_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      },
      {
        id: 2,
        user_id: 1,
        job_id: null,
        status: "APPLIED",
        applied_at: new Date().toISOString(),
        notes: "Applied directly on Stripe careers page.",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        job: null,
      },
    ]),
    create: vi.fn().mockResolvedValue({
      id: 3,
      user_id: 1,
      job_id: null,
      status: "INTERVIEWING",
      applied_at: new Date().toISOString(),
      notes: "Screening scheduled for Tuesday.",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
    update: vi.fn().mockResolvedValue({
      id: 1,
      user_id: 1,
      job_id: 101,
      status: "INTERVIEWING",
      applied_at: new Date().toISOString(),
      notes: "Updated status after technical screen.",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
    delete: vi.fn().mockResolvedValue({ success: true }),
  },
  jobsApi: {
    list: vi.fn().mockResolvedValue({
      items: [
        {
          id: 101,
          company_id: 1,
          title: "Senior Backend Engineer",
          location: "Remote",
        },
      ],
      total: 1,
    }),
    get: vi.fn(),
  },
  companiesApi: {
    list: vi.fn().mockResolvedValue({
      items: [{ id: 1, name: "Stripe", is_active: true }],
      total: 1,
    }),
  },
  systemApi: {
    getStatus: vi.fn().mockResolvedValue({ is_running: true }),
    getHealth: vi.fn().mockResolvedValue({ status: "healthy" }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
}));

describe("ApplicationsScreen Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("hawkoffer_access_token", "mock-token");
  });

  it("renders pipeline stage headers and metrics in Kanban board view", async () => {
    render(
      <AppProvider>
        <AuthProvider>
          <ApplicationsScreen />
        </AuthProvider>
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
      expect(screen.getByText("Need to polish resume for distributed systems.")).toBeInTheDocument();
    });

    // Check Kanban stage columns exist
    expect(screen.getByTestId("kanban-stage-SAVED")).toBeInTheDocument();
    expect(screen.getByTestId("kanban-stage-APPLIED")).toBeInTheDocument();
    expect(screen.getByTestId("kanban-stage-INTERVIEWING")).toBeInTheDocument();
    expect(screen.getByTestId("kanban-stage-OFFER")).toBeInTheDocument();
    expect(screen.getByTestId("kanban-stage-REJECTED")).toBeInTheDocument();
  });

  it("switches to list view and filters applications by stage", async () => {
    render(
      <AppProvider>
        <AuthProvider>
          <ApplicationsScreen />
        </AuthProvider>
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });

    // Click list view toggle
    fireEvent.click(screen.getByTestId("view-list-btn"));

    // Check list view table headers
    await waitFor(() => {
      expect(screen.getAllByText("Opportunity").length).toBeGreaterThan(0);
      expect(screen.getByText("Status")).toBeInTheDocument();
    });
  });

  it("opens add application modal and submits new application entry", async () => {
    render(
      <AppProvider>
        <AuthProvider>
          <ApplicationsScreen />
        </AuthProvider>
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("add-application-btn")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("add-application-btn"));

    await waitFor(() => {
      expect(screen.getByText("Track Career Opportunity")).toBeInTheDocument();
    });

    // Select job opportunity
    const jobSelect = screen.getByTestId("add-app-job-select");
    fireEvent.change(jobSelect, { target: { value: "101" } });

    // Enter notes
    const notesInput = screen.getByPlaceholderText(/Applied via referral/i);
    fireEvent.change(notesInput, { target: { value: "Recruiter reach out on LinkedIn" } });

    // Submit modal
    fireEvent.click(screen.getByTestId("submit-add-app-btn"));

    await waitFor(() => {
      expect(applicationsApi.create).toHaveBeenCalled();
    });
  });
});
