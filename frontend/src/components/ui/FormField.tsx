import type { ReactNode } from "react";

interface FormFieldProps {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}

/**
 * Wraps an Input/Select/etc with a label and optional hint/error text.
 * Errors take precedence over hints visually.
 */
export function FormField({ label, hint, error, children }: FormFieldProps) {
  return (
    <label className="block space-y-1.5">
      <span className="block text-xs font-medium text-stone-700 dark:text-stone-300">
        {label}
      </span>
      {children}
      {error ? (
        <span className="block text-2xs text-red-600 dark:text-red-400">{error}</span>
      ) : hint ? (
        <span className="block text-2xs text-stone-500 dark:text-stone-500">{hint}</span>
      ) : null}
    </label>
  );
}
