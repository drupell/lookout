"use client";

import { AlertCircle } from "lucide-react";
import { useState } from "react";

import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui";
import { signIn } from "@/lib/auth";

export default function SignInPage() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSignIn = async () => {
    setError(null);
    setLoading(true);
    try {
      await signIn();
      // signIn() redirects the browser; control rarely returns here. We keep
      // `loading` true so the button stays in its redirecting state until the
      // navigation takes over.
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Sign-in failed";
      setError(message);
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="animate-fade-in-up w-full max-w-sm">
        {/* Brand lockup — the warm thread carried by the logomark. */}
        <div className="flex justify-center">
          <Logo />
        </div>

        {/* The front door: a Spectral headline + warm welcome. */}
        <div className="mt-8 space-y-2 text-center">
          <h1 className="font-serif text-3xl font-medium leading-tight tracking-tight text-stone-900 dark:text-stone-100">
            Welcome back
          </h1>
          <p className="mx-auto max-w-xs text-sm leading-relaxed text-stone-500 dark:text-stone-400">
            Sign in to pick up where you left off. New accounts and password resets are
            handled by the team.
          </p>
        </div>

        {/* The card itself reads as a single warm sheet, not a glassy modal. */}
        <div className="mt-8 rounded-xl border border-border bg-surface p-6 shadow-sm dark:border-stone-800 dark:bg-stone-900">
          {error ? (
            <div
              role="alert"
              className="mb-4 flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 px-3.5 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
              <span className="leading-relaxed">{error}</span>
            </div>
          ) : null}

          <Button
            variant="primary"
            onClick={handleSignIn}
            loading={loading}
            className="w-full"
          >
            {loading ? "Redirecting…" : "Sign in"}
          </Button>
        </div>

        <p className="mt-6 text-center text-xs text-stone-400 dark:text-stone-500">
          Lookout — passive deal monitoring
        </p>
      </div>
    </main>
  );
}
