import React from "react";
import { AppProvider, useApp } from "./context/AppContext";
import { Sidebar } from "./components/layout/Sidebar";
import { Header } from "./components/layout/Header";
import { ToastContainer } from "./components/layout/ToastContainer";
import { JobDetailModal } from "./components/jobs/JobDetailModal";
import { DashboardScreen } from "./components/dashboard/DashboardScreen";
import { JobsFeedScreen } from "./components/jobs/JobsFeedScreen";
import { CompaniesScreen } from "./components/companies/CompaniesScreen";
import { ProfilesScreen } from "./components/profiles/ProfilesScreen";
import { NotificationsScreen } from "./components/notifications/NotificationsScreen";
import { MonitoringScreen } from "./components/monitoring/MonitoringScreen";
import { SettingsScreen } from "./components/settings/SettingsScreen";
import { BackendReadinessGate } from "./components/layout/BackendReadinessGate";

const AppContent: React.FC = () => {
  const { activeTab } = useApp();

  const renderActiveScreen = () => {
    switch (activeTab) {
      case "dashboard":
        return <DashboardScreen />;
      case "jobs":
        return <JobsFeedScreen />;
      case "companies":
        return <CompaniesScreen />;
      case "profiles":
        return <ProfilesScreen />;
      case "notifications":
        return <NotificationsScreen />;
      case "monitoring":
        return <MonitoringScreen />;
      case "settings":
        return <SettingsScreen />;
      default:
        return <DashboardScreen />;
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-100 font-sans">
      {/* Persistent Left Navigation Sidebar */}
      <Sidebar />

      {/* Main App Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header />

        <main className="flex-1 overflow-y-auto p-6 md:p-8">
          <div className="max-w-7xl mx-auto">
            {renderActiveScreen()}
          </div>
        </main>
      </div>

      {/* Global Job Detail Inspector Modal */}
      <JobDetailModal />

      {/* Global Toast Notifications */}
      <ToastContainer />
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <AppProvider>
      <BackendReadinessGate>
        <AppContent />
      </BackendReadinessGate>
    </AppProvider>
  );
};

export default App;
