import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { AuthProvider, useAuth } from "../context/AuthContext";
import { AuthModal } from "../components/auth/AuthModal";
import { authApi } from "../services/api";

vi.mock("../services/api", () => ({
  getApiBaseUrl: () => "http://127.0.0.1:8765/api/v1",
  setApiBaseUrl: vi.fn(),
  getAuthToken: () => null,
  getRefreshToken: () => null,
  setAuthTokens: vi.fn(),
  clearTokens: vi.fn(),
  authApi: {
    getMe: vi.fn().mockRejectedValue(new Error("Unauthenticated")),
    login: vi.fn().mockResolvedValue({
      access_token: "mock-jwt-access",
      refresh_token: "mock-jwt-refresh",
      token_type: "bearer",
      user: {
        id: 1,
        email: "alice@example.com",
        full_name: "Alice Smith",
        role: "user",
        is_active: true,
        created_at: new Date().toISOString(),
      },
    }),
    register: vi.fn().mockResolvedValue({
      access_token: "mock-jwt-access-new",
      refresh_token: "mock-jwt-refresh-new",
      token_type: "bearer",
      user: {
        id: 2,
        email: "bob@example.com",
        full_name: "Bob Jones",
        role: "user",
        is_active: true,
        created_at: new Date().toISOString(),
      },
    }),
    logout: vi.fn().mockResolvedValue({ success: true }),
    refresh: vi.fn(),
  },
  systemApi: {
    getStatus: vi.fn().mockResolvedValue({ is_running: true }),
    getHealth: vi.fn().mockResolvedValue({ status: "healthy" }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
}));

// Test helper to trigger modal open
const AuthTestTrigger: React.FC = () => {
  const { openAuthModal, isAuthenticated, user, logout } = useAuth();
  return (
    <div>
      <button data-testid="btn-trigger-login" onClick={() => openAuthModal("login")}>
        Open Login
      </button>
      <button data-testid="btn-trigger-register" onClick={() => openAuthModal("register")}>
        Open Register
      </button>
      {isAuthenticated && (
        <div>
          <span data-testid="user-display">Logged in as {user?.email}</span>
          <button data-testid="btn-test-logout" onClick={() => logout()}>
            Logout
          </button>
        </div>
      )}
    </div>
  );
};

describe("Authentication & AuthModal Flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("opens login modal, handles user input, and submits successfully", async () => {
    render(
      <AppProvider>
        <AuthProvider>
          <AuthTestTrigger />
          <AuthModal />
        </AuthProvider>
      </AppProvider>
    );

    // Click trigger to open login modal
    fireEvent.click(screen.getByTestId("btn-trigger-login"));

    await waitFor(() => {
      expect(screen.getByText(/Sign in to HawkOffer/i)).toBeInTheDocument();
    });

    const emailInput = screen.getByTestId("auth-email-input");
    const passwordInput = screen.getByTestId("auth-password-input");

    fireEvent.change(emailInput, { target: { value: "alice@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "SecretPass123!" } });

    fireEvent.click(screen.getByTestId("auth-submit-btn"));

    await waitFor(() => {
      expect(authApi.login).toHaveBeenCalledWith({
        email: "alice@example.com",
        password: "SecretPass123!",
      });
    });
  });

  it("switches to registration mode and submits registration payload", async () => {
    render(
      <AppProvider>
        <AuthProvider>
          <AuthTestTrigger />
          <AuthModal />
        </AuthProvider>
      </AppProvider>
    );

    fireEvent.click(screen.getByTestId("btn-trigger-register"));

    await waitFor(() => {
      expect(screen.getByText("Create HawkOffer Account")).toBeInTheDocument();
      expect(screen.getByTestId("auth-fullname-input")).toBeInTheDocument();
    });

    const fullNameInput = screen.getByTestId("auth-fullname-input");
    const emailInput = screen.getByTestId("auth-email-input");
    const passwordInput = screen.getByTestId("auth-password-input");

    fireEvent.change(fullNameInput, { target: { value: "Bob Jones" } });
    fireEvent.change(emailInput, { target: { value: "bob@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "ValidPassword123!" } });

    fireEvent.click(screen.getByTestId("auth-submit-btn"));

    await waitFor(() => {
      expect(authApi.register).toHaveBeenCalledWith({
        email: "bob@example.com",
        password: "ValidPassword123!",
        full_name: "Bob Jones",
      });
    });
  });
});
