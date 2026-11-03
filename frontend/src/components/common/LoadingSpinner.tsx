import React from "react";

interface LoadingSpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
  label?: string;
}

const sizeClasses = {
  sm: "w-4 h-4 border-2",
  md: "w-6 h-6 border-2",
  lg: "w-8 h-8 border-3",
};

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  size = "md",
  className = "",
  label,
}) => {
  return (
    <div className={`inline-flex items-center gap-2 text-slate-500 dark:text-slate-400 ${className}`}>
      <div
        className={`${sizeClasses[size]} border-slate-300 dark:border-slate-700 border-t-teal-600 dark:border-t-teal-400 rounded-full animate-spin`}
        role="status"
        aria-label="Loading"
      />
      {label && <span className="text-xs font-medium">{label}</span>}
    </div>
  );
};
