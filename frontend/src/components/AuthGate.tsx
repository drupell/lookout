"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { getUser, type CurrentUser } from "@/lib/auth";

/**
 * Wrap any authenticated page in <AuthGate>. Redirects unauthenticated users to
 * /sign-in and otherwise passes the resolved user to the children render-prop.
 */
export function AuthGate({ children }: { children: (user: CurrentUser) => ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null | "loading">("loading");

  useEffect(() => {
    void getUser().then((u) => {
      if (!u) {
        router.replace("/sign-in");
        return;
      }
      setUser(u);
    });
  }, [router]);

  if (user === "loading" || user === null) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-neutral-500">Loading…</p>
      </main>
    );
  }

  return <>{children(user)}</>;
}
