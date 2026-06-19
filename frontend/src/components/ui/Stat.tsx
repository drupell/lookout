import type { ReactNode } from "react";

import { Card } from "./Card";
import { cn } from "./cn";

interface StatProps {
  label: string;
  value: ReactNode;
  /** Optional smaller line under the value — e.g. a time beneath a date. */
  secondary?: ReactNode;
  hint?: string;
  icon?: ReactNode;
  tone?: "neutral" | "brand";
  /**
   * Visual weight of the value line. `numeric` (default) is the big serif
   * numeral; `text` is a slightly smaller serif sized for words/dates so a
   * long string doesn't shrink the whole card. Both sit on the same baseline
   * grid, so a row of mixed Stats reads as one coherent set.
   */
  variant?: "numeric" | "text";
}

/**
 * Compact KPI card. Big serif value, small label above, optional secondary
 * line + hint below. Use 3–4 in a grid for the dashboard overview.
 */
export function Stat({
  label,
  value,
  secondary,
  hint,
  icon,
  tone = "neutral",
  variant = "numeric",
}: StatProps) {
  return (
    <Card className="px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-2xs font-medium uppercase tracking-wide text-stone-500 dark:text-stone-500">
            {label}
          </p>
          <p
            className={cn(
              "font-serif font-medium tabular-nums leading-none tracking-tight",
              // Text-valued stats sit a hair smaller than big numerals so a
              // date like "May 20" feels balanced beside a "12", not dwarfed.
              variant === "numeric" ? "text-3xl" : "text-[1.625rem]",
              tone === "brand"
                ? "text-amber-700 dark:text-amber-400"
                : "text-stone-900 dark:text-stone-100",
            )}
          >
            {value}
          </p>
          {secondary ? (
            <p className="text-xs tabular-nums text-stone-500 dark:text-stone-400">{secondary}</p>
          ) : null}
        </div>
        {icon ? <span className="shrink-0 text-stone-400 dark:text-stone-500">{icon}</span> : null}
      </div>
      {hint ? <p className="mt-2 text-xs text-stone-500 dark:text-stone-500">{hint}</p> : null}
    </Card>
  );
}
