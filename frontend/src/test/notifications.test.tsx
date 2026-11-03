import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { NotificationsScreen } from "../components/notifications/NotificationsScreen";
import { notificationsApi } from "../services/api";

const { mockNotifications } = vi.hoisted(() => ({
  mockNotifications: [
    {
      id: 1,
      job_id: 101,
      profile_id: 1,
      notification_type: "job_alert",
      status: "UNREAD" as const,
      delivery_status: "DELIVERED" as const,
      created_at: new Date().toISOString(),
      read_at: null,
      dismissed_at: null,
      delivered_at: new Date().toISOString(),
    },
    {
      id: 2,
      job_id: 102,
      profile_id: 1,
      notification_type: "job_alert",
      status: "UNREAD" as const,
      delivery_status: "NOT_REQUIRED" as const,
      created_at: new Date().toISOString(),
      read_at: null,
      dismissed_at: null,
      delivered_at: null,
    },
  ],
}));

vi.mock("../services/api", () => ({
  getApiBaseUrl: () => "http://127.0.0.1:8765/api/v1",
  setApiBaseUrl: vi.fn(),
  systemApi: {
    getHealth: vi.fn().mockResolvedValue({ status: "healthy", database: "connected", scheduler: "running", timestamp: "" }),
    getStatus: vi.fn().mockResolvedValue({ is_running: true, is_lifecycle_gate_open: true, companies_count: 1, active_companies_count: 1, total_jobs_count: 2, unread_notifications_count: 2, pipeline_status: {}, timestamp: "" }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 2 }),
    list: vi.fn().mockResolvedValue({
      items: mockNotifications,
      total: 2,
      limit: 50,
      offset: 0,
    }),
    markRead: vi.fn().mockResolvedValue({ ...mockNotifications[0], status: "READ" }),
    markUnread: vi.fn().mockResolvedValue({ ...mockNotifications[0], status: "UNREAD" }),
    dismiss: vi.fn().mockResolvedValue({ ...mockNotifications[0], status: "DISMISSED" }),
    restore: vi.fn().mockResolvedValue({ ...mockNotifications[0], status: "UNREAD" }),
    batchMarkRead: vi.fn().mockResolvedValue({ affected_count: 2, message: "Marked as read" }),
    batchDismiss: vi.fn().mockResolvedValue({ affected_count: 2, message: "Dismissed" }),
  },
}));

describe("NotificationsScreen Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders notifications list and filter tabs", async () => {
    render(
      <AppProvider>
        <NotificationsScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Job Alert #1 (Job ID #101)")).toBeInTheDocument();
      expect(screen.getByText("Job Alert #2 (Job ID #102)")).toBeInTheDocument();
      expect(screen.getByTestId("filter-tab-unread")).toBeInTheDocument();
      expect(screen.getByTestId("filter-tab-all")).toBeInTheDocument();
    });
  });

  it("marks a notification as read", async () => {
    render(
      <AppProvider>
        <NotificationsScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("btn-mark-read-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-mark-read-1"));

    await waitFor(() => {
      expect(notificationsApi.markRead).toHaveBeenCalledWith(1);
    });
  });

  it("performs batch actions when multiple notifications are selected", async () => {
    render(
      <AppProvider>
        <NotificationsScreen />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("notification-row-1")).toBeInTheDocument();
    });

    // Toggle select all
    const selectAllBtn = screen.getByTitle("Select / Deselect All");
    fireEvent.click(selectAllBtn);

    await waitFor(() => {
      expect(screen.getByTestId("btn-batch-read")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-batch-read"));

    await waitFor(() => {
      expect(notificationsApi.batchMarkRead).toHaveBeenCalledWith([1, 2]);
    });
  });
});
