import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { UserRead } from "../types/api";
import {
  authApi,
  clearTokens,
  getAuthToken,
  getRefreshToken,
  setAuthToken,
  setRefreshToken,
} from "../services/api";

interface AuthContextValue {
  user: UserRead | null;
  isAuthenticated: boolean;
  isLoadingAuth: boolean;
  authError: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  register: (email: string, password: string, fullName?: string) => Promise<boolean>;
  logout: () => Promise<void>;
  clearAuthError: () => void;
  isAuthModalOpen: boolean;
  authModalMode: "login" | "register";
  openAuthModal: (mode?: "login" | "register") => void;
  closeAuthModal: () => void;
  refreshUserProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserRead | null>(null);
  const [isLoadingAuth, setIsLoadingAuth] = useState<boolean>(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState<boolean>(false);
  const [authModalMode, setAuthModalMode] = useState<"login" | "register">("login");

  const clearAuthError = useCallback(() => {
    setAuthError(null);
  }, []);

  const openAuthModal = useCallback((mode: "login" | "register" = "login") => {
    setAuthModalMode(mode);
    setAuthError(null);
    setIsAuthModalOpen(true);
  }, []);

  const closeAuthModal = useCallback(() => {
    setIsAuthModalOpen(false);
    setAuthError(null);
  }, []);

  const refreshUserProfile = useCallback(async () => {
    const token = getAuthToken();
    if (!token) {
      setUser(null);
      return;
    }
    try {
      const u = await authApi.getMe();
      setUser(u);
    } catch {
      setUser(null);
    }
  }, []);

  // Restore authenticated session on initial mount
  useEffect(() => {
    let mounted = true;

    const restoreSession = async () => {
      const token = getAuthToken();
      const refreshToken = getRefreshToken();

      if (!token && !refreshToken) {
        if (mounted) {
          setUser(null);
          setIsLoadingAuth(false);
        }
        return;
      }

      try {
        if (token) {
          const u = await authApi.getMe();
          if (mounted) {
            setUser(u);
            setIsLoadingAuth(false);
          }
          return;
        }
      } catch {
        // Access token expired or invalid; attempt refresh
      }

      if (refreshToken) {
        try {
          const res = await authApi.refresh({ refresh_token: refreshToken });
          setAuthToken(res.access_token);
          setRefreshToken(res.refresh_token);
          const u = await authApi.getMe();
          if (mounted) {
            setUser(u);
          }
        } catch {
          clearTokens();
          if (mounted) {
            setUser(null);
          }
        }
      } else {
        clearTokens();
        if (mounted) {
          setUser(null);
        }
      }

      if (mounted) {
        setIsLoadingAuth(false);
      }
    };

    restoreSession();

    return () => {
      mounted = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<boolean> => {
    setAuthError(null);
    try {
      const res = await authApi.login({ email, password });
      setAuthToken(res.access_token);
      setRefreshToken(res.refresh_token);
      if (res.user) {
        setUser(res.user);
      } else {
        const u = await authApi.getMe();
        setUser(u);
      }
      setIsAuthModalOpen(false);
      return true;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Authentication failed";
      setAuthError(msg);
      return false;
    }
  }, []);

  const register = useCallback(
    async (email: string, password: string, fullName?: string): Promise<boolean> => {
      setAuthError(null);
      try {
        await authApi.register({ email, password, full_name: fullName });
        // Automatically log in after registration
        const loginSuccess = await login(email, password);
        return loginSuccess;
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Registration failed";
        setAuthError(msg);
        return false;
      }
    },
    [login]
  );

  const logout = useCallback(async () => {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      try {
        await authApi.logout({ refresh_token: refreshToken });
      } catch {
        // Non-fatal if already invalidated
      }
    }
    clearTokens();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoadingAuth,
        authError,
        login,
        register,
        logout,
        clearAuthError,
        isAuthModalOpen,
        authModalMode,
        openAuthModal,
        closeAuthModal,
        refreshUserProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

const defaultAuthValue: AuthContextValue = {
  user: null,
  isAuthenticated: false,
  isLoadingAuth: false,
  authError: null,
  login: async () => false,
  register: async () => false,
  logout: async () => {},
  clearAuthError: () => {},
  isAuthModalOpen: false,
  authModalMode: "login",
  openAuthModal: () => {},
  closeAuthModal: () => {},
  refreshUserProfile: async () => {},
};

export const useAuth = (): AuthContextValue => {
  const context = useContext(AuthContext);
  return context || defaultAuthValue;
};
