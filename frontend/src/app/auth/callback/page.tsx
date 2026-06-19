"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/Logo";
import { handleCallback } from "@/lib/auth";

/**
 * OAuth callback. Cognito redirects here with `?code=...&state=...` after
 * the user authenticates. We exchange the code for tokens via the OIDC
 * client (which also verifies state to prevent CSRF), then forward to /deals.
 */
export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    handleCallback()
      .then(() => {
        router.replace("/deals");
      })
      .catch((err: unknown) => {
        const message =
          err instanceof Error ? err.message : "Sign-in failed. Please try again.";
        setError(message);
      });
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="animate-fade-in-up w-full max-w-sm">
        <div className="flex justify-center">
          <Logo />
        </div>

        {error ? (
          <>
            <div className="mt-8 space-y-2 text-center">
              <h1 className="font-serif text-2xl font-medium leading-tight tracking-tight text-stone-900 dark:text-stone-100">
                We couldn&apos;t complete sign-in
              </h1>
              <p className="mx-auto max-w-xs text-sm leading-relaxed text-stone-500 dark:text-stone-400">
                The link may have expired or already been used. Heading back to sign in
                usually clears it up.
              </p>
            </div>
            <div className="mt-8 rounded-xl border border-border bg-surface p-6 shadow-sm dark:border-stone-800 dark:bg-stone-900">
              <div
                role="alert"
                className="mb-4 flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 px-3.5 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
              >
                <AlertCircle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  strokeWidth={1.75}
                  aria-hidden
                />
                <span className="leading-relaxed">{error}</span>
              </div>
              {/* A plain anchor (not router push) guarantees a clean reload of the
                  sign-in route even if client state is wedged. */}
              <a
                href="/sign-in/"
                className="focus-ring inline-flex h-9 w-full items-center justify-center rounded-md bg-ink px-3.5 text-sm font-semibold text-ink-fg shadow-sm transition-[transform,box-shadow,background-color] duration-fast ease-editorial hover:-translate-y-px hover:bg-ink-hover hover:shadow-md active:translate-y-0 active:scale-[0.98] dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white"
              >
                Back to sign in
              </a>
            </div>
          </>
        ) : (
          <div className="mt-8 flex flex-col items-center gap-4 text-center">
            <Loader2
              className="h-7 w-7 animate-spin text-brand"
              strokeWidth={1.75}
              aria-hidden
            />
            <div className="space-y-1.5" role="status" aria-live="polite">
              <h1 className="font-serif text-2xl font-medium leading-tight tracking-tight text-stone-900 dark:text-stone-100">
                Completing sign-in…
              </h1>
              <p className="mx-auto max-w-xs text-sm leading-relaxed text-stone-500 dark:text-stone-400">
                Verifying your session and getting your deals ready. This only takes a
                moment.
              </p>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
