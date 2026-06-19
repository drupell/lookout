import { forwardRef, type SelectHTMLAttributes } from "react";

import { cn } from "./cn";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: readonly SelectOption[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, options, ...props },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn(
        "focus-ring block w-full rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-sm text-stone-900 disabled:bg-stone-50 disabled:text-stone-400 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-100 dark:disabled:bg-stone-950",
        className,
      )}
      {...props}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
});
