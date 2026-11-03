import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { DashboardScreen } from "../components/dashboard/DashboardScreen";
import { monitoringApi } from "../services/api";

vi.mock("../services/api", () => ({
  getApiBaseUrl: () => "http://127.0.0.1:8765/api/v1",
  setApiBaseUrl: vi.fn(),
  systemApi: {
    getHealth: vi.fn().mockResolvedValue({
      status: "healthy",
      database: "connected",
      scheduler: "running",
      timestamp: new Date().toISOString(),
    }),
    getStatus: vi.fn().mockResolvedValue({
      app_name: "HawkOffer",
      env: "development",
      is_running: true,
      is_lifecycle_gate_open: true,
      companies_count: 12,
      active_companies_count: 10,
      total_jobs_count: 156,
      unread_notifications_count: 5,
      pipeline_status: {
        job_discovery_worker: {
          job_id: "job_discovery_worker",
          scheduled: true,
          next_run_time: new Date(Date.now() + 60000).toISOString(),
          paused: false,
        },
        profile_reevaluation_worker: {
          job_id: "profile_reevaluation_worker",
          scheduled: true,
          next_run_time: new Date(Date.now() + 60000).toISOString(),
          paused: false,
        },
        backlog_drain_worker: {
          job_id: "backlog_drain_worker",
          scheduled: true,
          next_run_time: new Date(Date.now() + 30000).toISOString(),
          paused: false,
        },
      },
      timestamp: new Date().toISOString(),
    }),
  },
  jobsApi: {
    list: vi.fn().mockResolvedValue({
      items: [
        {
          id: 101,
          company_id: 1,
          title: "Frontend Infrastructure Engineer",
          location: "San Francisco, CA",
          department: "Platform",
          external_id: "ext-101",
          description: "Full description",
          application_url: "https://boards.greenhouse.io/job/101",
          employment_type: "Full-Time",
          job_type: "full_time",
          graduation_year: null,
          posted_at: null,
          status: "ACTIVE",
          fingerprint_hash: "a".repeat(64),
          content_hash: "b".repeat(64),
          first_seen_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
      total: 1,
      limit: 5,
      offset: 0,
    }),
  },
  monitoringApi: {
    getStatus: vi.fn().mockResolvedValue({
      is_running: true,
      is_lifecycle_gate_open: true,
      pipeline_status: {},
      timestamp: new Date().toISOString(),
    }),
    triggerDiscovery: vi.fn().mockResolvedValue({
      worker_name: "job_discovery",
      status: "completed",
      message: "Ran successfully",
      result: { claimed_count: 4, promoted_count: 2 },
      timestamp: new Date().toISOString(),
    }),
    triggerDrain: vi.fn().mockResolvedValue({
      worker_name: "backlog_drain",
      status: "completed",
      message: "Ran successfully",
      result: { allocated_slots: 3, promoted_count: 3 },
      timestamp: new Date().toISOString(),
    }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 5 }),
  },
}));

describe("DashboardScreen Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders KPI counts from backend status", async () => {
    render(
      <AppProvider>
        <DashboardScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("stat-card-companies")).toHaveTextContent("12");
      expect(screen.getByTestId("stat-card-jobs")).toHaveTextContent("156");
      expect(screen.getByTestId("stat-card-notifications")).toHaveTextContent("5");
    });
  });

  it("renders recent job discoveries in list", async () => {
    render(
      <AppProvider>
        <DashboardScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Frontend Infrastructure Engineer")).toBeInTheDocument();
      expect(screen.getByText("San Francisco, CA", { exact: false })).toBeInTheDocument();
    });
  });

  it("triggers manual discovery worker from quick action button", async () => {
    render(
      <AppProvider>
        <DashboardScreen />
      </AppProvider>
    );

    const btn = screen.getByTestId("btn-quick-discovery");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(monitoringApi.triggerDiscovery).toHaveBeenCalled();
    });
  });

  it("triggers manual backlog drain worker from quick action button", async () => {
    render(
      <AppProvider>
        <DashboardScreen />
      </AppProvider>
    );

    const btn = screen.getByTestId("btn-quick-drain");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(monitoringApi.triggerDrain).toHaveBeenCalled();
    });
  });
});
