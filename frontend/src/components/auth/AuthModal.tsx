import React, { useState } from "react";
import { Eye, EyeOff, Lock, Mail, User, AlertCircle, ArrowRight } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { Modal } from "../common/Modal";
import { Button } from "../common/Button";

export const AuthModal: React.FC = () => {
  const {
    isAuthModalOpen,
    closeAuthModal,
    authModalMode,
    openAuthModal,
    login,
    register,
    authError,
    clearAuthError,
  } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isRegister = authModalMode === "register";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) return;

    setIsSubmitting(true);
    clearAuthError();

    try {
      if (isRegister) {
        await register(email.trim(), password, fullName.trim() || undefined);
      } else {
        await login(email.trim(), password);
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSwitchMode = () => {
    clearAuthError();
    openAuthModal(isRegister ? "login" : "register");
  };

  return (
    <Modal
      isOpen={isAuthModalOpen}
      onClose={closeAuthModal}
      title={isRegister ? "Create HawkOffer Account" : "Sign in to HawkOffer"}
      size="md"
    >
      <div className="space-y-5" data-testid="auth-modal">
        {/* Value Proposition Description */}
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {isRegister
            ? "Create your personal career profile to follow target companies, receive instant matching alerts, and track job applications."
            : "Sign in to access your followed companies, application tracker, and customized notification preferences."}
        </p>

        {/* Error Alert */}
        {authError && (
          <div
            className="p-3 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900 rounded-lg flex items-start gap-2 text-rose-800 dark:text-rose-300 text-xs"
            data-testid="auth-error-alert"
          >
            <AlertCircle className="w-4 h-4 text-rose-600 dark:text-rose-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 font-medium">{authError}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {isRegister && (
            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Full Name <span className="text-slate-400 font-normal">(optional)</span>
              </label>
              <div className="relative">
                <User className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
                <input
                  type="text"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="e.g. Alex Chen"
                  className="w-full pl-9 pr-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-slate-900 dark:text-slate-100 placeholder-slate-400"
                  data-testid="auth-fullname-input"
                />
              </div>
            </div>
          )}

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Email Address <span className="text-rose-500">*</span>
            </label>
            <div className="relative">
              <Mail className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full pl-9 pr-3 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-slate-900 dark:text-slate-100 placeholder-slate-400"
                data-testid="auth-email-input"
              />
            </div>
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Password <span className="text-rose-500">*</span>
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
              <input
                type={showPassword ? "text" : "password"}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full pl-9 pr-10 py-2 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-slate-900 dark:text-slate-100 placeholder-slate-400"
                data-testid="auth-password-input"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-2.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {isRegister && (
              <p className="text-[11px] text-slate-400 dark:text-slate-500 pt-0.5">
                Must be at least 8 characters with lowercase, uppercase, and digit.
              </p>
            )}
          </div>

          <div className="pt-2 space-y-3">
            <Button
              type="submit"
              variant="primary"
              className="w-full justify-center"
              isLoading={isSubmitting}
              data-testid="auth-submit-btn"
            >
              <span>{isRegister ? "Create Account" : "Sign In"}</span>
              <ArrowRight className="w-4 h-4 ml-1.5" />
            </Button>

            <div className="text-center">
              <button
                type="button"
                onClick={handleSwitchMode}
                className="text-xs text-teal-600 dark:text-teal-400 hover:underline font-medium"
                data-testid="auth-switch-mode-btn"
              >
                {isRegister
                  ? "Already have an account? Sign In"
                  : "Don't have an account yet? Register"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </Modal>
  );
};
