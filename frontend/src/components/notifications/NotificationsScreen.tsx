import React, { useEffect, useState, useCallback } from "react";
import {
  Bell,
  Eye,
  EyeOff,
  Trash2,
  RotateCcw,
  ExternalLink,
  CheckSquare,
  Square,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { NotificationRead, NotificationStatus } from "../../types/api";
import { notificationsApi } from "../../services/api";
import { Badge } from "../common/Badge";
import { Button } from "../common/Button";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EmptyState } from "../common/EmptyState";

type TabFilter = "ALL" | "UNREAD" | "READ" | "DISMISSED";

export const NotificationsScreen: React.FC = () => {
  const { setSelectedJobId, showToast, refreshUnreadCount } = useApp();

  const [notifications, setNotifications] = useState<NotificationRead[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [activeFilter, setActiveFilter] = useState<TabFilter>("UNREAD");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isBatchActionInProgress, setIsBatchActionInProgress] = useState<boolean>(false);

  const fetchNotifications = useCallback(async () => {
    setIsLoading(true);
    try {
      const statusParam = activeFilter === "ALL" ? undefined : activeFilter;
      const res = await notificationsApi.list({
        status: statusParam,
        limit: 50,
      });
      setNotifications(res.items);
      setSelectedIds(new Set());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load notifications";
      showToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  }, [activeFilter, showToast]);

  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications]);

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === notifications.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(notifications.map((n) => n.id)));
    }
  };

  const handleMarkRead = async (id: number) => {
    try {
      await notificationsApi.markRead(id);
      showToast("Notification marked as read", "info");
      await fetchNotifications();
      await refreshUnreadCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update notification";
      showToast(msg, "error");
    }
  };

  const handleMarkUnread = async (id: number) => {
    try {
      await notificationsApi.markUnread(id);
      showToast("Notification marked as unread", "info");
      await fetchNotifications();
      await refreshUnreadCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update notification";
      showToast(msg, "error");
    }
  };

  const handleDismiss = async (id: number) => {
    try {
      await notificationsApi.dismiss(id);
      showToast("Notification dismissed", "info");
      await fetchNotifications();
      await refreshUnreadCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to dismiss notification";
      showToast(msg, "error");
    }
  };

  const handleRestore = async (id: number) => {
    try {
      await notificationsApi.restore(id);
      showToast("Notification restored to inbox", "success");
      await fetchNotifications();
      await refreshUnreadCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to restore notification";
      showToast(msg, "error");
    }
  };

  const handleBatchMarkRead = async () => {
    if (selectedIds.size === 0) return;
    setIsBatchActionInProgress(true);
    try {
      const ids = Array.from(selectedIds);
      const res = await notificationsApi.batchMarkRead(ids);
      showToast(`Marked ${res.affected_count} notifications as read.`, "success");
      await fetchNotifications();
      await refreshUnreadCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Batch operation failed";
      showToast(msg, "error");
    } finally {
      setIsBatchActionInProgress(false);
    }
  };

  const handleBatchDismiss = async () => {
    if (selectedIds.size === 0) return;
    setIsBatchActionInProgress(true);
    try {
      const ids = Array.from(selectedIds);
      const res = await notificationsApi.batchDismiss(ids);
      showToast(`Dismissed ${res.affected_count} notifications.`, "info");
      await fetchNotifications();
      await refreshUnreadCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Batch operation failed";
      showToast(msg, "error");
    } finally {
      setIsBatchActionInProgress(false);
    }
  };

  const getStatusBadge = (status: NotificationStatus | string) => {
    switch (status) {
      case "UNREAD":
        return <Badge variant="brand" size="sm">UNREAD</Badge>;
      case "READ":
        return <Badge variant="neutral" size="sm">READ</Badge>;
      case "DISMISSED":
        return <Badge variant="warning" size="sm">DISMISSED</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{status}</Badge>;
    }
  };

  const getDeliveryBadge = (delivery: string) => {
    switch (delivery) {
      case "DELIVERED":
        return <Badge variant="success" size="sm">Delivered</Badge>;
      case "PENDING":
        return <Badge variant="warning" size="sm">Pending</Badge>;
      case "FAILED":
        return <Badge variant="danger" size="sm">Delivery Failed</Badge>;
      case "NOT_REQUIRED":
      default:
        return <Badge variant="neutral" size="sm">In-App</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Header & Filter Tabs */}
      <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <Bell className="w-5 h-5 text-teal-600 dark:text-teal-400" />
              Notification Inbox
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Promoted candidate notifications from the fairness-aware backlog drain worker.
            </p>
          </div>

          {/* Filter Pills */}
          <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 p-1 rounded-lg text-xs font-medium">
            {(["UNREAD", "ALL", "READ", "DISMISSED"] as TabFilter[]).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveFilter(tab)}
                className={`px-3 py-1.5 rounded-md transition-colors ${
                  activeFilter === tab
                    ? "bg-white dark:bg-slate-700 text-teal-600 dark:text-teal-300 font-semibold shadow-xs"
                    : "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200"
                }`}
                data-testid={`filter-tab-${tab.toLowerCase()}`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {/* Batch Operations Bar (visible when items selected) */}
        {selectedIds.size > 0 && (
          <div className="pt-3 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between gap-3 text-xs">
            <span className="font-semibold text-slate-700 dark:text-slate-300">
              {selectedIds.size} notification{selectedIds.size > 1 ? "s" : ""} selected
            </span>

            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleBatchMarkRead}
                isLoading={isBatchActionInProgress}
                leftIcon={<Eye className="w-3.5 h-3.5" />}
                data-testid="btn-batch-read"
              >
                Mark Read
              </Button>

              <Button
                variant="outline"
                size="sm"
                onClick={handleBatchDismiss}
                isLoading={isBatchActionInProgress}
                leftIcon={<Trash2 className="w-3.5 h-3.5 text-rose-500" />}
                data-testid="btn-batch-dismiss"
              >
                Dismiss
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Notifications List */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden shadow-xs">
        {isLoading ? (
          <div className="py-20">
            <LoadingSpinner size="lg" label="Loading notifications..." />
          </div>
        ) : notifications.length === 0 ? (
          <div className="py-16">
            <EmptyState
              title={`No ${activeFilter.toLowerCase()} notifications`}
              description={
                activeFilter === "UNREAD"
                  ? "All caught up! New job matches will appear here when discovered and promoted."
                  : "No notifications match the selected filter."
              }
            />
          </div>
        ) : (
          <div>
            {/* List Table Header */}
            <div className="px-4 py-3 bg-slate-50 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs font-semibold text-slate-500 dark:text-slate-400">
              <div className="flex items-center gap-3">
                <button
                  onClick={toggleSelectAll}
                  className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 focus:outline-hidden"
                  title="Select / Deselect All"
                >
                  {selectedIds.size === notifications.length && notifications.length > 0 ? (
                    <CheckSquare className="w-4 h-4 text-teal-600" />
                  ) : (
                    <Square className="w-4 h-4" />
                  )}
                </button>
                <span>Notification Details</span>
              </div>
              <span>Actions</span>
            </div>

            {/* List Rows */}
            <div className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="notifications-list">
              {notifications.map((n) => {
                const isSelected = selectedIds.has(n.id);
                const isUnread = n.status === "UNREAD";

                return (
                  <div
                    key={n.id}
                    className={`p-4 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-4 ${
                      isUnread
                        ? "bg-teal-50/30 dark:bg-teal-950/20"
                        : "hover:bg-slate-50 dark:hover:bg-slate-800/40"
                    }`}
                    data-testid={`notification-row-${n.id}`}
                  >
                    <div className="flex items-start gap-3 min-w-0">
                      <button
                        onClick={() => toggleSelect(n.id)}
                        className="mt-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 focus:outline-hidden"
                      >
                        {isSelected ? (
                          <CheckSquare className="w-4 h-4 text-teal-600" />
                        ) : (
                          <Square className="w-4 h-4" />
                        )}
                      </button>

                      <div className="space-y-1.5 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span
                            onClick={() => setSelectedJobId(n.job_id)}
                            className="font-bold text-sm text-slate-900 dark:text-slate-100 hover:text-teal-600 dark:group-hover:text-teal-400 cursor-pointer transition-colors"
                          >
                            Job Alert #{n.id} (Job ID #{n.job_id})
                          </span>
                          {getStatusBadge(n.status)}
                          {getDeliveryBadge(n.delivery_status)}
                        </div>

                        <div className="flex items-center gap-3 text-[11px] text-slate-400 flex-wrap">
                          <span>Job #{n.job_id}</span>
                          {n.profile_id && <span>&bull; Profile #{n.profile_id}</span>}
                          <span>&bull; Type: {n.notification_type}</span>
                          <span>
                            &bull; Received: {new Date(n.created_at).toLocaleString()}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Action buttons */}
                    <div className="flex items-center justify-end gap-1.5 flex-shrink-0 self-end sm:self-center">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setSelectedJobId(n.job_id)}
                        leftIcon={<ExternalLink className="w-3.5 h-3.5" />}
                        data-testid={`btn-view-job-${n.id}`}
                      >
                        View Job
                      </Button>

                      {n.status === "UNREAD" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleMarkRead(n.id)}
                          title="Mark as Read"
                          data-testid={`btn-mark-read-${n.id}`}
                        >
                          <Eye className="w-4 h-4 text-slate-500" />
                        </Button>
                      )}

                      {n.status === "READ" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleMarkUnread(n.id)}
                          title="Mark as Unread"
                          data-testid={`btn-mark-unread-${n.id}`}
                        >
                          <EyeOff className="w-4 h-4 text-slate-500" />
                        </Button>
                      )}

                      {n.status !== "DISMISSED" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDismiss(n.id)}
                          title="Dismiss"
                          data-testid={`btn-dismiss-${n.id}`}
                        >
                          <Trash2 className="w-4 h-4 text-rose-500" />
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRestore(n.id)}
                          title="Restore to inbox"
                          data-testid={`btn-restore-${n.id}`}
                        >
                          <RotateCcw className="w-4 h-4 text-teal-500" />
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
