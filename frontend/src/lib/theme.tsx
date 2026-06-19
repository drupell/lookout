"use client";

/**
 * Theme: auto / light / dark, persisted in localStorage.
 *
 * Why hand-rolled (instead of next-themes): the surface area is tiny — three
 * modes, one localStorage key, one media-query listener — and a dep we own
 * a few lines of beats a dep we have to track upstream. The no-flash guard
 * lives inline in `layout.tsx` so the right class is on `<html>` *before*
 * paint; this hook is purely the runtime user-facing knob.
 */
import { useEffect, useState, type ReactNode, useCallback } from "react";
import { createContext, useContext } from "react";

export type ThemeMode = "auto" | "light" | "dark";

export const THEME_STORAGE_KEY = "lookout-theme";

interface ThemeContextValue {
  /** What the user picked. "auto" = follow the system preference. */
  mode: ThemeMode;
  /** Currently-applied theme — what the user actually sees right now. */
  resolved: "light" | "dark";
  /** Update preference + persist + restamp `.dark` on <html>. */
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

/** Read the system preference once. Safe-guarded for SSR. */
function systemPref(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStored(): ThemeMode {
  if (typeof window === "undefined") return "auto";
  const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
  return raw === "light" || raw === "dark" || raw === "auto" ? raw : "auto";
}

/** Apply or remove the `.dark` class on <html> to match the resolved theme. */
function applyClass(resolved: "light" | "dark"): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Initialize from storage so React state matches the no-flash script's
  // class on <html>. Defaults to "auto" — system preference wins.
  const [mode, setModeState] = useState<ThemeMode>(() => readStored());
  const [resolved, setResolved] = useState<"light" | "dark">(() =>
    mode === "auto" ? systemPref() : mode,
  );

  // When mode is "auto", listen to system changes so the toggle reflects what
  // the page is actually rendering as the OS pref shifts.
  useEffect(() => {
    if (mode !== "auto") {
      setResolved(mode);
      applyClass(mode);
      return;
    }
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const next = mql.matches ? "dark" : "light";
      setResolved(next);
      applyClass(next);
    };
    handler();
    mql.addEventListener("change", handler);
    return () => {
      mql.removeEventListener("change", handler);
    };
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage may be disabled (private mode); preference is then session-only.
    }
  }, []);

  return (
    <ThemeContext.Provider value={{ mode, resolved, setMode }}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be called inside a <ThemeProvider>");
  }
  return ctx;
}

/**
 * Inline script body to run before paint. Sets `.dark` on <html> based on
 * stored preference (+ system pref when "auto"), so the first painted frame
 * is in the right theme — no flash. Designed to be embedded via
 * `dangerouslySetInnerHTML` in the root `<head>`.
 */
export const NO_FLASH_SCRIPT = `(function(){try{var k=${JSON.stringify(THEME_STORAGE_KEY)};var t=localStorage.getItem(k);if(t!=='light'&&t!=='dark'&&t!=='auto')t='auto';var dark=t==='dark'||(t==='auto'&&matchMedia('(prefers-color-scheme: dark)').matches);if(dark)document.documentElement.classList.add('dark');}catch(e){}})();`;
