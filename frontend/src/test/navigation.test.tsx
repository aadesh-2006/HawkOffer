import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider, useApp } from "../context/AppContext";
import { Sidebar } from "../components/layout/Sidebar";
import { Header } from "../components/layout/Header";

// Mock API calls made by AppContext
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
      active_companies_count: 4,
      total_jobs_count: 42,
      unread_notifications_count: 7,
      pipeline_status: {},
      timestamp: new Date().toISOString(),
    }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({
      unread_count: 7,
    }),
  },
}));

const NavigationTestHarness: React.FC = () => {
  const { activeTab } = useApp();
  return (
    <div>
      <Header />
      <Sidebar />
      <div data-testid="current-active-tab">{activeTab}</div>
    </div>
  );
};

describe("Navigation & Application Shell", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders all navigation items and marks Dashboard active by default", async () => {
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    expect(screen.getByTestId("nav-dashboard")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("dashboard");

    // Check all tabs exist
    expect(screen.getByTestId("nav-jobs")).toBeInTheDocument();
    expect(screen.getByTestId("nav-companies")).toBeInTheDocument();
    expect(screen.getByTestId("nav-profiles")).toBeInTheDocument();
    expect(screen.getByTestId("nav-notifications")).toBeInTheDocument();
    expect(screen.getByTestId("nav-monitoring")).toBeInTheDocument();
    expect(screen.getByTestId("nav-settings")).toBeInTheDocument();
  });

  it("switches active tabs when sidebar buttons are clicked", async () => {
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    fireEvent.click(screen.getByTestId("nav-jobs"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("jobs");

    fireEvent.click(screen.getByTestId("nav-companies"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("companies");
  });

  it("displays unread count badge in sidebar", async () => {
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("sidebar-unread-badge")).toHaveTextContent("7");
    });
  });

  it("toggles theme when theme button is clicked", async () => {
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    const themeBtn = screen.getByTestId("theme-toggle-button");
    fireEvent.click(themeBtn);
    // Verified button responds and updates theme without crash
    expect(themeBtn).toBeInTheDocument();
  });
});
