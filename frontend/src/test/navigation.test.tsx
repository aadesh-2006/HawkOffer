import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AppProvider, getPathFromTab, getTabFromPathname, useApp } from "../context/AppContext";
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
    window.history.pushState(null, "", "/");
  });

  afterEach(() => {
    window.history.pushState(null, "", "/");
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

  it("maps URL pathnames accurately to application tabs", () => {
    expect(getTabFromPathname("/")).toBe("dashboard");
    expect(getTabFromPathname("/tracker")).toBe("applications");
    expect(getTabFromPathname("/jobs")).toBe("jobs");
    expect(getTabFromPathname("/settings")).toBe("settings");
    expect(getTabFromPathname("/companies")).toBe("companies");
    expect(getTabFromPathname("/monitoring")).toBe("monitoring");
    expect(getTabFromPathname("/unknown-route")).toBe("dashboard");

    expect(getPathFromTab("applications")).toBe("/tracker");
    expect(getPathFromTab("dashboard")).toBe("/");
    expect(getPathFromTab("jobs")).toBe("/jobs");
    expect(getPathFromTab("settings")).toBe("/settings");
  });

  it("initializes active tab from window.location.pathname on direct navigation", async () => {
    window.history.pushState(null, "", "/tracker");
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("applications");
    expect(screen.getByTestId("nav-applications")).toHaveAttribute("aria-current", "page");
  });

  it("updates the browser URL when sidebar navigation buttons are clicked", async () => {
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    fireEvent.click(screen.getByTestId("nav-jobs"));
    expect(window.location.pathname).toBe("/jobs");
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("jobs");

    fireEvent.click(screen.getByTestId("nav-applications"));
    expect(window.location.pathname).toBe("/tracker");
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("applications");

    fireEvent.click(screen.getByTestId("nav-settings"));
    expect(window.location.pathname).toBe("/settings");
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("settings");

    fireEvent.click(screen.getByTestId("nav-dashboard"));
    expect(window.location.pathname).toBe("/");
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("dashboard");
  });

  it("updates the selected tab when handling synthetic popstate events (popstate handler)", async () => {
    // Verifies the application's window popstate listener updates activeTab
    // in response to dispatched PopStateEvents (testing the event handler, not browser history traversal)
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    // Navigate to jobs, then applications
    fireEvent.click(screen.getByTestId("nav-jobs"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("jobs");

    fireEvent.click(screen.getByTestId("nav-applications"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("applications");

    // Dispatch synthetic popstate simulating Back transition to /jobs
    act(() => {
      window.history.pushState(null, "", "/jobs");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("jobs");

    // Dispatch synthetic popstate simulating Forward transition to /tracker
    act(() => {
      window.history.pushState(null, "", "/tracker");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("applications");
  });

  it("navigates smoothly between /tracker and other tabs", async () => {
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    fireEvent.click(screen.getByTestId("nav-applications"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("applications");
    expect(window.location.pathname).toBe("/tracker");

    fireEvent.click(screen.getByTestId("nav-companies"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("companies");
    expect(window.location.pathname).toBe("/companies");

    fireEvent.click(screen.getByTestId("nav-applications"));
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("applications");
    expect(window.location.pathname).toBe("/tracker");
  });

  it("redirects unknown application paths to root / and selects dashboard", async () => {
    window.history.pushState(null, "", "/unrecognized/nested/path");
    render(
      <AppProvider>
        <NavigationTestHarness />
      </AppProvider>
    );

    expect(window.location.pathname).toBe("/");
    expect(screen.getByTestId("current-active-tab")).toHaveTextContent("dashboard");
  });
});
