import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { MonitoringScreen } from "../components/monitoring/MonitoringScreen";
import { SettingsScreen } from "../components/settings/SettingsScreen";
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
      env: "test",
      is_running: true,
      is_lifecycle_gate_open: true,
      companies_count: 5,
      active_companies_count: 5,
      total_jobs_count: 50,
      unread_notifications_count: 0,
      pipeline_status: {
        job_discovery_worker: { job_id: "job_discovery_worker", scheduled: true, next_run_time: null, paused: false },
        profile_reevaluation_worker: { job_id: "profile_reevaluation_worker", scheduled: true, next_run_time: null, paused: false },
        backlog_drain_worker: { job_id: "backlog_drain_worker", scheduled: true, next_run_time: null, paused: false },
        company_scan_1: { job_id: "company_scan_1", scheduled: true, next_run_time: new Date(Date.now() + 100000).toISOString(), paused: false },
      },
      timestamp: new Date().toISOString(),
    }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
  monitoringApi: {
    getStatus: vi.fn().mockResolvedValue({
      is_running: true,
      is_lifecycle_gate_open: true,
      pipeline_status: {
        job_discovery_worker: { job_id: "job_discovery_worker", scheduled: true, next_run_time: null, paused: false },
        profile_reevaluation_worker: { job_id: "profile_reevaluation_worker", scheduled: true, next_run_time: null, paused: false },
        backlog_drain_worker: { job_id: "backlog_drain_worker", scheduled: true, next_run_time: null, paused: false },
        company_scan_1: { job_id: "company_scan_1", scheduled: true, next_run_time: new Date(Date.now() + 100000).toISOString(), paused: false },
      },
      timestamp: new Date().toISOString(),
    }),
    triggerDiscovery: vi.fn().mockResolvedValue({
      worker_name: "job_discovery",
      status: "completed",
      message: "Done",
      result: { claimed_count: 10, promoted_count: 3 },
      timestamp: new Date().toISOString(),
    }),
    triggerReevaluation: vi.fn().mockResolvedValue({
      worker_name: "profile_reevaluation",
      status: "completed",
      message: "Done",
      result: { claimed_count: 1, promoted_count: 2 },
      timestamp: new Date().toISOString(),
    }),
    triggerDrain: vi.fn().mockResolvedValue({
      worker_name: "backlog_drain",
      status: "completed",
      message: "Done",
      result: { allocated_slots: 5, promoted_count: 4 },
      timestamp: new Date().toISOString(),
    }),
    pause: vi.fn().mockResolvedValue({ message: "Paused" }),
    resume: vi.fn().mockResolvedValue({ message: "Resumed" }),
  },
}));

describe("MonitoringScreen and SettingsScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders pipeline workers and triggers discovery", async () => {
    render(
      <AppProvider>
        <MonitoringScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("APScheduler RUNNING")).toBeInTheDocument();
      expect(screen.getByText("Gate OPEN")).toBeInTheDocument();
      expect(screen.getByTestId("btn-trigger-discovery")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-trigger-discovery"));

    await waitFor(() => {
      expect(monitoringApi.triggerDiscovery).toHaveBeenCalled();
      expect(screen.getByTestId("execution-result-card")).toBeInTheDocument();
    });
  });

  it("renders settings screen with endpoint configuration and diagnostics", async () => {
    render(
      <AppProvider>
        <SettingsScreen />
      </AppProvider>
    );

    expect(screen.getByTestId("input-base-url")).toHaveValue("http://127.0.0.1:8765/api/v1");
    await waitFor(() => {
      expect(screen.getByText("SQLITE CONNECTED")).toBeInTheDocument();
      expect(screen.getByText("ONLINE")).toBeInTheDocument();
    });
  });
});
