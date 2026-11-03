import React from "react";
import {
  LayoutDashboard,
  Briefcase,
  Building2,
  Bookmark,
  Sliders,
  Bell,
  Activity,
  Settings,
  Circle,
  Radio,
} from "lucide-react";
import { TabId, useApp } from "../../context/AppContext";

interface NavItem {
  id: TabId;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  showBadge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "jobs", label: "Jobs Feed", icon: Briefcase },
  { id: "companies", label: "Companies", icon: Building2 },
  { id: "applications", label: "Applications", icon: Bookmark },
  { id: "profiles", label: "Preference Profiles", icon: Sliders },
  { id: "notifications", label: "Notifications", icon: Bell, showBadge: true },
  { id: "monitoring", label: "Monitoring", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings },
];

export const Sidebar: React.FC = () => {
  const { activeTab, setActiveTab, unreadCount, isOnline, systemStatus, health } = useApp();

  const isSchedulerRunning = systemStatus?.is_running ?? (health?.scheduler === "running");
  const isGateOpen = systemStatus?.is_lifecycle_gate_open ?? false;

  return (
    <aside className="w-64 flex-shrink-0 bg-white dark:bg-slate-900 border-r border-slate-200 dark:border-slate-800 flex flex-col justify-between select-none">
      {/* Brand Header */}
      <div>
        <div className="h-16 flex items-center px-6 gap-3 border-b border-slate-200 dark:border-slate-800">
          <div className="w-9 h-9 rounded-lg bg-teal-600 flex items-center justify-center text-white shadow-sm font-bold text-lg">
            H
          </div>
          <div className="flex flex-col">
            <span className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-base leading-none">
              HawkOffer
            </span>
            <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium mt-1">
              Career Monitor v0.7.0
            </span>
          </div>
        </div>

        {/* Navigation Links */}
        <nav className="p-3 space-y-1" aria-label="Main Navigation">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;

            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-teal-50 dark:bg-teal-950/50 text-teal-700 dark:text-teal-300 font-semibold shadow-xs"
                    : "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-800/60"
                }`}
                aria-current={isActive ? "page" : undefined}
                data-testid={`nav-${item.id}`}
              >
                <div className="flex items-center gap-3">
                  <Icon
                    className={`w-5 h-5 ${
                      isActive
                        ? "text-teal-600 dark:text-teal-400"
                        : "text-slate-400 dark:text-slate-500"
                    }`}
                  />
                  <span>{item.label}</span>
                </div>

                {item.showBadge && unreadCount > 0 && (
                  <span
                    data-testid="sidebar-unread-badge"
                    className="inline-flex items-center justify-center px-2 py-0.5 text-xs font-semibold rounded-full bg-teal-600 text-white min-w-5 leading-none"
                  >
                    {unreadCount > 99 ? "99+" : unreadCount}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* System Status Footer */}
      <div className="p-4 border-t border-slate-200 dark:border-slate-800 space-y-2.5">
        <div className="bg-slate-50 dark:bg-slate-800/60 rounded-lg p-3 space-y-2 border border-slate-200/60 dark:border-slate-700/60">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-600 dark:text-slate-400 font-medium">Backend:</span>
            <span
              className={`inline-flex items-center gap-1.5 font-medium ${
                isOnline ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"
              }`}
            >
              <Circle
                className={`w-2 h-2 fill-current ${
                  isOnline ? "text-emerald-500 animate-pulse" : "text-rose-500"
                }`}
              />
              {isOnline ? "Online" : "Disconnected"}
            </span>
          </div>

          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-600 dark:text-slate-400 font-medium">Scheduler:</span>
            <span
              className={`inline-flex items-center gap-1.5 font-medium ${
                isSchedulerRunning
                  ? isGateOpen
                    ? "text-emerald-700 dark:text-emerald-400"
                    : "text-amber-700 dark:text-amber-400"
                  : "text-slate-600 dark:text-slate-400"
              }`}
            >
              <Radio
                className={`w-2 h-2 fill-current ${
                  isSchedulerRunning ? "text-teal-500" : "text-slate-400"
                }`}
              />
              {isSchedulerRunning ? (isGateOpen ? "Active" : "Warming") : "Idle"}
            </span>
          </div>
        </div>

        <div className="text-[11px] text-center text-slate-500 dark:text-slate-400">
          Local SQLite &bull; 127.0.0.1:8765
        </div>
      </div>
    </aside>
  );
};
