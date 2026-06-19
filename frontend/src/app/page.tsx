"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { getUser } from "@/lib/auth";

/**
 * Root page — bounces signed-in users to /dashboard, others to /sign-in.
 * Static export, so the redirect happens client-side.
 */
export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    void getUser().then((user) => {
      router.replace(user ? "/dashboard" : "/sign-in");
    });
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <p className="text-sm text-stone-500">Loading…</p>
    </main>
  );
}
