import React from "react";
import { LoadingSpinner } from "./LoadingSpinner";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost" | "outline";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  icon?: React.ReactNode;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary:
    "bg-teal-600 hover:bg-teal-700 active:bg-teal-800 text-white shadow-sm border border-teal-600 dark:bg-teal-600 dark:hover:bg-teal-500",
  secondary:
    "bg-slate-100 hover:bg-slate-200 active:bg-slate-300 text-slate-800 border border-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 dark:text-slate-100 dark:border-slate-700",
  danger:
    "bg-rose-600 hover:bg-rose-700 active:bg-rose-800 text-white shadow-sm border border-rose-600 dark:bg-rose-700 dark:hover:bg-rose-600",
  ghost:
    "hover:bg-slate-100 active:bg-slate-200 text-slate-700 dark:hover:bg-slate-800 dark:text-slate-300 border border-transparent",
  outline:
    "bg-transparent hover:bg-slate-50 text-slate-700 border border-slate-300 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-900",
};

const sizeStyles: Record<ButtonSize, string> = {
  sm: "text-xs px-2.5 py-1.5 gap-1.5 rounded-md",
  md: "text-sm px-3.5 py-2 gap-2 rounded-lg",
  lg: "text-base px-4 py-2.5 gap-2.5 rounded-lg",
};

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = "primary",
  size = "md",
  isLoading = false,
  icon,
  leftIcon,
  rightIcon,
  disabled,
  className = "",
  ...props
}) => {
  const isDisabled = disabled || isLoading;
  const effectiveLeftIcon = leftIcon || icon;

  return (
    <button
      disabled={isDisabled}
      className={`inline-flex items-center justify-center font-medium transition-colors focus:outline-hidden focus:ring-2 focus:ring-teal-500/40 disabled:opacity-60 disabled:cursor-not-allowed select-none ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}
      {...props}
    >
      {isLoading ? (
        <LoadingSpinner size="sm" />
      ) : (
        effectiveLeftIcon && <span className="flex-shrink-0">{effectiveLeftIcon}</span>
      )}
      {children}
      {!isLoading && rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
    </button>
  );
};
