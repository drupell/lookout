"use client";

import { Moon, Monitor, Sun } from "lucide-react";

import { useTheme, type ThemeMode } from "@/lib/theme";

/**
 * Compact three-state segmented control: auto · light · dark. Lives in the
 * sidebar account row so theme is a quiet utility, not a marketing surface.
 * "Auto" follows the OS preference (the default) — the user can pin a mode
 * with one click and we'll persist it.
 */
const MODES: readonly { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: "auto", label: "Auto", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
];

export function ThemeToggle({ className }: { className?: string }) {
  const { mode, setMode } = useTheme();
  return (
    <div
      role="group"
      aria-label="Color theme"
      className={`inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-elevated p-0.5 dark:border-stone-800 dark:bg-stone-900 ${
        className ?? ""
      }`}
    >
      {MODES.map(({ value, label, icon: Icon }) => {
        const active = mode === value;
        return (
          <button
            key={value}
            type="button"
            aria-label={`Use ${label.toLowerCase()} theme`}
            aria-pressed={active}
            onClick={() => {
              setMode(value);
            }}
            title={label}
            className={`focus-ring rounded-sm p-1 transition-colors duration-fast ${
              active
                ? "bg-stone-100 text-stone-900 dark:bg-stone-800 dark:text-stone-100"
                : "text-stone-500 hover:text-stone-800 dark:text-stone-500 dark:hover:text-stone-200"
            }`}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
