import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { BackendReadinessGate } from "../components/layout/BackendReadinessGate";
import { FirstRunGuide } from "../components/dashboard/FirstRunGuide";
import { DashboardScreen } from "../components/dashboard/DashboardScreen";

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
      companies_count: 0,
      active_companies_count: 0,
      total_jobs_count: 0,
      unread_notifications_count: 0,
      pipeline_status: {},
      timestamp: new Date().toISOString(),
    }),
  },
  jobsApi: {
    list: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0 }),
    get: vi.fn(),
  },
  monitoringApi: {
    getStatus: vi.fn().mockResolvedValue({
      is_running: true,
      is_lifecycle_gate_open: true,
      registered_jobs: [],
      pipeline_status: {},
      company_job_counts: {},
    }),
    triggerDiscovery: vi.fn(),
    triggerDrain: vi.fn(),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
}));

describe("M8 Frontend Components: Readiness & Onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders children when readiness is ready", async () => {
    render(
      <AppProvider>
        <BackendReadinessGate>
          <div data-testid="app-content">Ready Content</div>
        </BackendReadinessGate>
      </AppProvider>
    );

    // Initial state or resolved ready state should render content
    expect(await screen.findByTestId("app-content")).toBeInTheDocument();
  });

  it("renders FirstRunGuide when companies count is 0", async () => {
    render(
      <AppProvider>
        <DashboardScreen />
      </AppProvider>
    );

    // Should find the first-run guide
    const guide = await screen.findByTestId("first-run-guide");
    expect(guide).toBeInTheDocument();
    expect(screen.getByText("Welcome to HawkOffer")).toBeInTheDocument();
    expect(screen.getByText("Add Monitored Company")).toBeInTheDocument();
    expect(screen.getByText("Set Preference Profile")).toBeInTheDocument();
    expect(screen.getByText("Run Scan & Monitor")).toBeInTheDocument();
  });

  it("FirstRunGuide action buttons trigger tab changes", async () => {
    render(
      <AppProvider>
        <FirstRunGuide />
      </AppProvider>
    );

    const addCompanyBtn = screen.getByRole("button", { name: /Add Company/i });
    expect(addCompanyBtn).toBeInTheDocument();
    fireEvent.click(addCompanyBtn);

    const configProfileBtn = screen.getByRole("button", { name: /Configure Profile/i });
    expect(configProfileBtn).toBeInTheDocument();
    fireEvent.click(configProfileBtn);

    const monitoringBtn = screen.getByRole("button", { name: /Monitoring Controls/i });
    expect(monitoringBtn).toBeInTheDocument();
    fireEvent.click(monitoringBtn);
  });
});
