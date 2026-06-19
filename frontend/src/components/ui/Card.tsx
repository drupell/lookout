import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Adds a hover lift + shadow bump — for cards that are themselves links/clickable. */
  interactive?: boolean;
}

export function Card({ className, interactive = false, ...props }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface-elevated shadow-sm dark:border-stone-800 dark:bg-stone-900",
        interactive &&
          "transition-[transform,box-shadow,border-color] duration-fast ease-editorial hover:-translate-y-0.5 hover:border-border-strong hover:shadow-md dark:hover:border-stone-700",
        className,
      )}
      {...props}
    />
  );
}

interface SectionHeaderProps {
  title: string;
  description?: string;
  badge?: string;
  action?: ReactNode;
}

/**
 * Title + optional description + optional right-aligned action. Use inside a
 * Card or as a standalone block. Keeps spacing/typography consistent across
 * settings sections, dashboard widgets, and modals.
 */
export function SectionHeader({ title, description, badge, action }: SectionHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-5 py-4 dark:border-stone-800">
      <div className="space-y-0.5">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-semibold tracking-tight text-stone-900 dark:text-stone-100">
            {title}
          </h2>
          {badge ? (
            <span className="rounded bg-stone-100 px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide text-stone-700 dark:bg-stone-800 dark:text-stone-300">
              {badge}
            </span>
          ) : null}
        </div>
        {description ? (
          <p className="text-xs text-stone-500 dark:text-stone-500">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}
