import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "./cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          "focus-ring block w-full rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-sm text-stone-900 placeholder:text-stone-400 disabled:bg-stone-50 disabled:text-stone-400 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-100 dark:placeholder:text-stone-600 dark:disabled:bg-stone-950",
          className,
        )}
        {...props}
      />
    );
  },
);
