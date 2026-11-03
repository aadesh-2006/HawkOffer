import React, { useEffect, useState } from "react";
import { Bell, X, AlertTriangle } from "lucide-react";
import { getPushStatus, isWebPushSupported, subscribeToPush } from "../../services/pushService";
import { useApp } from "../../context/AppContext";
import { Button } from "../common/Button";

export const PushNotificationBanner: React.FC = () => {
  const { showToast } = useApp();
  const [isVisible, setIsVisible] = useState<boolean>(false);
  const [isSubscribing, setIsSubscribing] = useState<boolean>(false);
  const [permissionDenied, setPermissionDenied] = useState<boolean>(false);

  useEffect(() => {
    let mounted = true;

    const checkStatus = async () => {
      // Check if user previously dismissed
      const dismissed = localStorage.getItem("hawkoffer_push_banner_dismissed") === "true";
      if (dismissed) {
        return;
      }

      if (!isWebPushSupported()) {
        return;
      }

      const status = await getPushStatus();
      if (!mounted) return;

      if (status.supported && status.permission === "default" && !status.isSubscribed) {
        setIsVisible(true);
      } else if (status.permission === "denied") {
        setPermissionDenied(true);
      }
    };

    checkStatus();

    return () => {
      mounted = false;
    };
  }, []);

  const handleEnablePush = async () => {
    setIsSubscribing(true);
    try {
      const res = await subscribeToPush();
      if (res.success) {
        showToast("Web Push notifications activated successfully!", "success");
        setIsVisible(false);
      } else {
        if (res.error?.includes("denied")) {
          setPermissionDenied(true);
        }
        showToast(res.error || "Failed to enable notifications", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Notification subscription failed";
      showToast(msg, "error");
    } finally {
      setIsSubscribing(false);
    }
  };

  const handleDismiss = () => {
    localStorage.setItem("hawkoffer_push_banner_dismissed", "true");
    setIsVisible(false);
  };

  if (!isVisible && !permissionDenied) {
    return null;
  }

  if (permissionDenied) {
    return (
      <div
        className="p-3.5 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-900 dark:text-amber-200 shadow-xs"
        data-testid="push-denied-banner"
      >
        <div className="flex items-center gap-2.5">
          <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 flex-shrink-0" />
          <span>
            Browser notifications are blocked in your browser settings. To receive timely job alerts,
            enable notifications in your browser address bar.
          </span>
        </div>
        <button
          onClick={() => setPermissionDenied(false)}
          className="text-amber-700 dark:text-amber-300 hover:text-amber-900 p-1"
          title="Dismiss"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    );
  }

  return (
    <div
      className="p-4 bg-gradient-to-r from-teal-500/10 via-teal-600/5 to-slate-50 dark:from-teal-950/40 dark:via-teal-900/20 dark:to-slate-900 border border-teal-200 dark:border-teal-900/60 rounded-xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xs"
      data-testid="push-onboarding-banner"
    >
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-teal-600 flex items-center justify-center text-white flex-shrink-0 shadow-sm mt-0.5">
          <Bell className="w-5 h-5" />
        </div>
        <div className="space-y-0.5">
          <h4 className="text-sm font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            Stay Ahead with Real-Time Push Alerts
            <span className="px-2 py-0.5 bg-teal-100 dark:bg-teal-900/60 text-teal-800 dark:text-teal-300 rounded text-[10px] font-semibold uppercase tracking-wider">
              Background Dispatch
            </span>
          </h4>
          <p className="text-xs text-slate-600 dark:text-slate-400 max-w-2xl leading-relaxed">
            HawkOffer monitors company career portals continuously in the background. Enable Web Push to
            receive instant desktop and mobile notifications the second a new matching job is discovered.
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-shrink-0 w-full sm:w-auto justify-end">
        <Button
          variant="secondary"
          size="sm"
          onClick={handleDismiss}
          className="text-xs"
          data-testid="dismiss-push-banner-btn"
        >
          Maybe Later
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={handleEnablePush}
          isLoading={isSubscribing}
          data-testid="enable-push-banner-btn"
        >
          <Bell className="w-3.5 h-3.5 mr-1.5" />
          Enable Alerts
        </Button>
      </div>
    </div>
  );
};
