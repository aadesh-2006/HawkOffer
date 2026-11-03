import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { CompaniesScreen } from "../components/companies/CompaniesScreen";
import { companiesApi } from "../services/api";

const { mockCompanies } = vi.hoisted(() => ({
  mockCompanies: [
    {
      id: 1,
      name: "Stripe",
      career_url: "https://boards.greenhouse.io/stripe",
      platform: "greenhouse",
      is_active: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
    {
      id: 2,
      name: "OpenAI",
      career_url: "https://jobs.ashbyhq.com/openai",
      platform: "ashby",
      is_active: false,
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
    getStatus: vi.fn().mockResolvedValue({ is_running: true, is_lifecycle_gate_open: true, companies_count: 2, active_companies_count: 1, total_jobs_count: 10, unread_notifications_count: 0, pipeline_status: {}, timestamp: "" }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
  companiesApi: {
    list: vi.fn().mockResolvedValue({
      items: mockCompanies,
      total: 2,
      limit: 50,
      offset: 0,
    }),
    get: vi.fn().mockImplementation((id: number) =>
      Promise.resolve(mockCompanies.find((c) => c.id === id) || mockCompanies[0])
    ),
    getScanState: vi.fn().mockResolvedValue({
      id: 1,
      company_id: 1,
      scan_interval_minutes: 60,
      status: "IDLE",
      last_scan_started_at: null,
      last_scan_completed_at: new Date().toISOString(),
      next_scan_at: null,
      consecutive_failures: 0,
      last_error: null,
      total_scans: 10,
      total_errors: 0,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
    create: vi.fn().mockResolvedValue({
      id: 3,
      name: "Anthropic",
      career_url: "https://jobs.lever.co/anthropic",
      platform: "lever",
      is_active: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
    update: vi.fn().mockResolvedValue({
      id: 1,
      name: "Stripe",
      career_url: "https://boards.greenhouse.io/stripe",
      platform: "greenhouse",
      is_active: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
    delete: vi.fn().mockResolvedValue({ message: "Deleted" }),
    triggerScan: vi.fn().mockResolvedValue({
      company_id: 1,
      status: "completed",
      message: "Scanned",
      stats: { total_fetched: 20, newly_inserted: 5 },
    }),
  },
}));

describe("CompaniesScreen Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders list of monitored companies and platform badges", async () => {
    render(
      <AppProvider>
        <CompaniesScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Stripe")).toBeInTheDocument();
      expect(screen.getByText("OpenAI")).toBeInTheDocument();
      expect(screen.getByText("GREENHOUSE")).toBeInTheDocument();
      expect(screen.getByText("ASHBY")).toBeInTheDocument();
    });
  });

  it("opens add modal, fills form and creates company", async () => {
    render(
      <AppProvider>
        <CompaniesScreen />
      </AppProvider>
    );

    fireEvent.click(screen.getByTestId("btn-add-company"));

    const nameInput = screen.getByTestId("input-company-name");
    const urlInput = screen.getByTestId("input-company-url");
    const platformSelect = screen.getByTestId("select-company-platform");

    fireEvent.change(nameInput, { target: { value: "Anthropic" } });
    fireEvent.change(urlInput, { target: { value: "https://jobs.lever.co/anthropic" } });
    fireEvent.change(platformSelect, { target: { value: "lever" } });

    fireEvent.click(screen.getByTestId("btn-save-company"));

    await waitFor(() => {
      expect(companiesApi.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Anthropic",
          career_url: "https://jobs.lever.co/anthropic",
          platform: "lever",
          is_active: true,
        })
      );
    });
  });

  it("triggers on-demand career scan for a company", async () => {
    render(
      <AppProvider>
        <CompaniesScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("btn-scan-company-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-scan-company-1"));

    await waitFor(() => {
      expect(companiesApi.triggerScan).toHaveBeenCalledWith(1);
    });
  });
});
