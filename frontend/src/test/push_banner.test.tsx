import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { PushNotificationBanner } from "../components/dashboard/PushNotificationBanner";
import * as pushService from "../services/pushService";

vi.mock("../services/pushService", () => ({
  isWebPushSupported: vi.fn(),
  getPushStatus: vi.fn(),
  subscribeToPush: vi.fn(),
  unsubscribeFromPush: vi.fn(),
}));

describe("PushNotificationBanner Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("renders non-intrusive banner when Web Push is supported and permission is default", async () => {
    vi.mocked(pushService.isWebPushSupported).mockReturnValue(true);
    vi.mocked(pushService.getPushStatus).mockResolvedValue({
      supported: true,
      permission: "default",
      isSubscribed: false,
      isTauri: false,
    });

    render(
      <AppProvider>
        <PushNotificationBanner />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("push-onboarding-banner")).toBeInTheDocument();
      expect(screen.getByText("Stay Ahead with Real-Time Push Alerts")).toBeInTheDocument();
      expect(screen.getByTestId("enable-push-banner-btn")).toBeInTheDocument();
    });
  });

  it("subscribes when user clicks Enable Web Push", async () => {
    vi.mocked(pushService.isWebPushSupported).mockReturnValue(true);
    vi.mocked(pushService.getPushStatus).mockResolvedValue({
      supported: true,
      permission: "default",
      isSubscribed: false,
      isTauri: false,
    });
    vi.mocked(pushService.subscribeToPush).mockResolvedValue({
      success: true,
    });

    render(
      <AppProvider>
        <PushNotificationBanner />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("enable-push-banner-btn")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("enable-push-banner-btn"));

    await waitFor(() => {
      expect(pushService.subscribeToPush).toHaveBeenCalled();
    });
  });

  it("dismisses banner and sets localStorage when dismiss button is clicked", async () => {
    vi.mocked(pushService.isWebPushSupported).mockReturnValue(true);
    vi.mocked(pushService.getPushStatus).mockResolvedValue({
      supported: true,
      permission: "default",
      isSubscribed: false,
      isTauri: false,
    });

    render(
      <AppProvider>
        <PushNotificationBanner />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("dismiss-push-banner-btn")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("dismiss-push-banner-btn"));

    expect(localStorage.getItem("hawkoffer_push_banner_dismissed")).toBe("true");
    expect(screen.queryByTestId("push-onboarding-banner")).not.toBeInTheDocument();
  });
});
