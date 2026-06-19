import type { HTMLAttributes } from "react";

import { cn } from "./cn";

type Tone = "neutral" | "success" | "warning" | "danger" | "brand";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

const TONES: Record<Tone, string> = {
  neutral:
    "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
  success:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  warning:
    "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  danger:
    "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  brand:
    "bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
};

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}
