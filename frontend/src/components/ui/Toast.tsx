"use client";

import * as ToastPrimitive from "@radix-ui/react-toast";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { cn } from "./cn";

/**
 * Editorial-warm toast system, built on Radix for correct a11y (live region,
 * focus, swipe-to-dismiss) and reduced-motion behaviour. This is the channel
 * for surfacing API errors and quiet confirmations.
 *
 * Usage:
 *   const toast = useToast();
 *   toast.success("Saved");
 *   toast.error("Couldn't save — try again");
 */

type ToastTone = "success" | "error" | "info";

interface ToastOptions {
  /** Optional second line under the title. */
  description?: string;
  /** Auto-dismiss delay in ms. Defaults to 5s. Errors linger a little longer. */
  duration?: number;
}

interface ToastRecord {
  id: number;
  tone: ToastTone;
  message: string;
  description?: string;
  /** Resolved (never undefined) auto-dismiss delay in ms. */
  duration: number;
}

interface ToastApi {
  success: (message: string, opts?: ToastOptions) => void;
  error: (message: string, opts?: ToastOptions) => void;
  info: (message: string, opts?: ToastOptions) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const TONE_STYLES: Record<ToastTone, { ring: string; icon: ReactNode; iconColor: string }> = {
  success: {
    ring: "border-emerald-200 dark:border-emerald-900/60",
    iconColor: "text-emerald-600 dark:text-emerald-400",
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  error: {
    ring: "border-red-200 dark:border-red-900/60",
    iconColor: "text-red-600 dark:text-red-400",
    icon: <XCircle className="h-4 w-4" />,
  },
  info: {
    ring: "border-amber-200 dark:border-amber-900/60",
    iconColor: "text-amber-700 dark:text-amber-400",
    icon: <Info className="h-4 w-4" />,
  },
};

let nextId = 0;

/**
 * Mount once near the root. Provides the `useToast()` API to the whole tree and
 * renders the stacked viewport in the bottom-right.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((tone: ToastTone, message: string, opts?: ToastOptions) => {
    const id = nextId++;
    const record: ToastRecord = {
      id,
      tone,
      message,
      duration: opts?.duration ?? (tone === "error" ? 7000 : 5000),
      ...(opts?.description !== undefined ? { description: opts.description } : {}),
    };
    setToasts((prev) => [...prev, record]);
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      success: (m, o) => {
        push("success", m, o);
      },
      error: (m, o) => {
        push("error", m, o);
      },
      info: (m, o) => {
        push("info", m, o);
      },
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      <ToastPrimitive.Provider swipeDirection="right" duration={5000}>
        {children}
        {toasts.map((t) => {
          const style = TONE_STYLES[t.tone];
          return (
            <ToastPrimitive.Root
              key={t.id}
              duration={t.duration}
              onOpenChange={(open) => {
                if (!open) dismiss(t.id);
              }}
              className={cn(
                "flex items-start gap-3 rounded-lg border bg-white p-3 pr-9 shadow-md dark:bg-stone-900",
                "data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)] data-[state=open]:animate-fade-in-up",
                "data-[swipe=cancel]:translate-x-0 data-[swipe=cancel]:transition-transform",
                "relative",
                style.ring,
              )}
            >
              <span className={cn("mt-0.5 shrink-0", style.iconColor)}>{style.icon}</span>
              <div className="min-w-0 flex-1">
                <ToastPrimitive.Title className="text-sm font-medium text-stone-900 dark:text-stone-100">
                  {t.message}
                </ToastPrimitive.Title>
                {t.description ? (
                  <ToastPrimitive.Description className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
                    {t.description}
                  </ToastPrimitive.Description>
                ) : null}
              </div>
              <ToastPrimitive.Close
                aria-label="Dismiss"
                className="focus-ring absolute right-2 top-2 rounded p-1 text-stone-400 transition-colors hover:text-stone-700 dark:hover:text-stone-200"
              >
                <X className="h-3.5 w-3.5" />
              </ToastPrimitive.Close>
            </ToastPrimitive.Root>
          );
        })}
        <ToastPrimitive.Viewport className="fixed bottom-0 right-0 z-[100] flex w-full max-w-sm flex-col gap-2 p-4 outline-none" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}

/** Access the toast API. Safe to call anywhere under <ToastProvider>. */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within <ToastProvider>");
  }
  return ctx;
}
