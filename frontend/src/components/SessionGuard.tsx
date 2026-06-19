"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useToast } from "@/components/ui";
import { setUnauthorizedHandler } from "@/lib/api";

/**
 * Bridges the router-free api layer to the app's session UX. When any API call
 * hits a 401/403 (token missing, expired, or rejected), the api layer invokes
 * our registered handler; we surface a single "session expired" toast and send
 * the user to sign-in, preserving where they were so they can land back here.
 *
 * Render once, high in the tree (root layout). It draws nothing.
 */
export function SessionGuard() {
  const router = useRouter();
  const pathname = usePathname();
  const toast = useToast();
  // Keep the latest path without re-registering the handler on every nav.
  const pathRef = useRef(pathname);
  pathRef.current = pathname;

  useEffect(() => {
    setUnauthorizedHandler(() => {
      // Never bounce people who are already on an auth screen.
      const current = pathRef.current;
      if (current.startsWith("/sign-in") || current.startsWith("/auth")) return;

      toast.info("Your session expired — please sign in again.");

      const returnTo = encodeURIComponent(current);
      router.replace(`/sign-in?returnTo=${returnTo}`);
    });

    return () => {
      setUnauthorizedHandler(null);
    };
  }, [router, toast]);

  return null;
}
