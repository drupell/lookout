"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { InFlightRun } from "@/lib/schemas";

const ACTIVE_POLL_MS = 4000;
// Optimistic state caps at this long. The worker typically starts within a
// couple of seconds; if it never does (SQS issue, lambda cold-start gone bad)
// we still want to re-enable the button eventually.
const OPTIMISTIC_HOLD_MS = 60_000;

interface Options {
  onCompleted?: () => void;
}

/**
 * Polls /me/runs/in_flight and keeps the latest result in state. Two
 * additional features beyond a vanilla poll:
 *
 *   1. `markOptimisticallyRunning()` — call right after triggering a run so
 *      the UI flips to "running" immediately, before the worker has had time
 *      to write the RUNNING row. Avoids the race where the user can click
 *      "Run now" twice while waiting for the next poll. Cleared as soon as a
 *      real poll observes in_flight=true (or after OPTIMISTIC_HOLD_MS).
 *
 *   2. `onCompleted` fires once when state transitions from in_flight=true
 *      → in_flight=false. Use it to refresh dependent data (deals, /me).
 *
 * Polling cadence is constant (4s); cheap enough that we don't bother
 * varying it by tab visibility.
 */
// After this many consecutive failed polls we surface a "reconnecting" hint
// rather than letting a stale "running" bar sit there silently.
const FAILURES_BEFORE_RECONNECTING = 2;

export function useInFlightRun(options: Options = {}): {
  inFlight: InFlightRun | null;
  refetch: () => Promise<void>;
  markOptimisticallyRunning: () => void;
  /** True once polling has failed repeatedly — show a "reconnecting" hint. */
  connectionLost: boolean;
} {
  const [real, setReal] = useState<InFlightRun | null>(null);
  const [optimisticUntil, setOptimisticUntil] = useState<number | null>(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const failures = useRef(0);
  const wasInFlight = useRef(false);
  const onCompletedRef = useRef(options.onCompleted);
  onCompletedRef.current = options.onCompleted;

  const refetch = useCallback(async () => {
    try {
      const next = await api.getInFlightRun();
      // A good poll clears any prior failure streak.
      failures.current = 0;
      setConnectionLost(false);
      setReal(next);
      if (wasInFlight.current && !next.in_flight) {
        onCompletedRef.current?.();
      }
      wasInFlight.current = next.in_flight;
    } catch {
      // Don't blank the UI on a blip — keep the last good state and retry next
      // tick. After a few consecutive misses, flag the connection as lost so
      // the progress bar can say "reconnecting" instead of looking frozen.
      failures.current += 1;
      if (failures.current >= FAILURES_BEFORE_RECONNECTING) setConnectionLost(true);
    }
  }, []);

  const markOptimisticallyRunning = useCallback(() => {
    setOptimisticUntil(Date.now() + OPTIMISTIC_HOLD_MS);
    // Kick a refetch immediately so the real state catches up sooner.
    void refetch();
  }, [refetch]);

  // Clear optimistic as soon as we have a real in_flight=true (the worker
  // got the message and wrote the RUNNING row). Also clear if optimistic
  // expired but the user hasn't seen a real run start.
  useEffect(() => {
    if (real?.in_flight) setOptimisticUntil(null);
  }, [real]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (cancelled) return;
      await refetch();
      timer = setTimeout(() => void tick(), ACTIVE_POLL_MS);
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refetch]);

  // Compose the effective state: real wins when present and active; else
  // an optimistic stub (still within the hold window).
  const optimisticActive =
    optimisticUntil !== null && Date.now() < optimisticUntil && !real?.in_flight;

  const inFlight: InFlightRun | null = real?.in_flight
    ? real
    : optimisticActive
      ? {
          in_flight: true,
          current_node: "starting",
          progress: { completed: 0, total: 12 },
        }
      : real;

  return { inFlight, refetch, markOptimisticallyRunning, connectionLost };
}
