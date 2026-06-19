"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { type ReactNode } from "react";

import { cn } from "./cn";

interface TooltipProps {
  /** The trigger — usually an icon-only button or truncated text. */
  children: ReactNode;
  /** Tooltip content. */
  label: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  /** Delay before showing, in ms. */
  delayDuration?: number;
}

/**
 * Accessible tooltip wrapper over Radix. Self-contained: it bundles its own
 * single-trigger Provider so callers don't need to mount one. Reach for it on
 * icon-only buttons and truncated text where a label would otherwise be lost.
 */
export function Tooltip({ children, label, side = "top", delayDuration = 250 }: TooltipProps) {
  return (
    <TooltipPrimitive.Provider delayDuration={delayDuration}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            side={side}
            sideOffset={6}
            className={cn(
              "z-[110] max-w-xs rounded-md bg-stone-900 px-2 py-1 text-xs font-medium text-stone-50 shadow-md dark:bg-stone-100 dark:text-stone-900",
              "data-[state=delayed-open]:animate-fade-in-up",
              "select-none",
            )}
          >
            {label}
            <TooltipPrimitive.Arrow className="fill-stone-900 dark:fill-stone-100" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
