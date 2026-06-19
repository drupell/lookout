import type { HTMLAttributes } from "react";

import { cn } from "./cn";

/**
 * Loading placeholder with a warm shimmer sweep. Compose freely — set width via
 * className (e.g. `w-32 h-4`). The shimmer is gated by reduced-motion globally,
 * falling back to a calm static tint. Decorative, so hidden from the a11y tree.
 */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn(
        "relative overflow-hidden rounded-md bg-stone-200/70 dark:bg-stone-800/70",
        // Shimmer sweep — a soft highlight that travels left→right.
        "before:absolute before:inset-0 before:-translate-x-full before:animate-shimmer",
        "before:bg-gradient-to-r before:from-transparent before:via-white/60 before:to-transparent",
        "dark:before:via-white/10",
        className,
      )}
      {...props}
    />
  );
}
