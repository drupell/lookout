"use client";

import { useEffect, useMemo, useState } from "react";

import { AppShell, PageBody, PageHeader } from "@/components/AppShell";
import { AuthGate } from "@/components/AuthGate";
import { DealCard } from "@/components/DealCard";
import { BrokenScope, ScopeHorizon } from "@/components/illustrations/Illustration";
import {
  DealFilterBar,
  EMPTY_FILTERS,
  applyDealFilters,
  type DealFilters,
} from "@/components/DealFilterBar";
import { RunErrorNotice } from "@/components/RunErrorNotice";
import { StaleSettingsBanner } from "@/components/StaleSettingsBanner";
import { Button, Card, CardBody, SectionHeader, Skeleton, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatNextRunDate } from "@/lib/dealFormat";
import type { Deal, MeResponse, RunSummary } from "@/lib/schemas";

export default function DealsPage() {
  return <AuthGate>{(user) => <AppShell user={user}>{<Body />}</AppShell>}</AuthGate>;
}

type DealsState =
  | { kind: "loading" }
  | {
      kind: "ok";
      deals: Deal[];
      me: MeResponse | undefined;
      latestRun: RunSummary | undefined;
    }
  | { kind: "error"; message: string };

function Body() {
  const toast = useToast();
  const [state, setState] = useState<DealsState>({ kind: "loading" });
  const [filters, setFilters] = useState<DealFilters>(EMPTY_FILTERS);

  useEffect(() => {
    let cancelled = false;

    // allSettled, not all: a single failed call (e.g. deals rate-limited) must
    // not blank the whole page. We render whatever succeeded and surface the
    // rest. We pull the latest run too so we can distinguish an honest
    // "0 deals matched" from an upstream failure.
    void Promise.allSettled([api.listDeals(), api.getMe(), api.listRuns()]).then(
      ([dealsRes, meRes, runsRes]) => {
        if (cancelled) return;

        const deals = dealsRes.status === "fulfilled" ? dealsRes.value : undefined;
        const me = meRes.status === "fulfilled" ? meRes.value : undefined;
        const runs = runsRes.status === "fulfilled" ? runsRes.value : undefined;

        // Deals are the heart of this page. If that one call failed, treat the
        // page as errored; if only the ancillary calls failed, render anyway.
        if (deals === undefined) {
          setState({ kind: "error", message: messageFor(dealsRes) });
          return;
        }

        // Ancillary failures degrade gracefully — note them, don't block.
        if (me === undefined) {
          toast.error("Couldn't load your account details — some context may be missing.");
        }
        if (runs === undefined) {
          toast.error("Couldn't load run history — recent-run context is unavailable.");
        }

        setState({
          kind: "ok",
          deals,
          me,
          latestRun: runs?.[0],
        });
      },
    );

    return () => {
      cancelled = true;
    };
  }, [toast]);

  const visibleDeals = useMemo(
    () => (state.kind === "ok" ? applyDealFilters(state.deals, filters) : []),
    [state, filters],
  );

  return (
    <>
      <PageHeader
        title="In view"
        description="What the agent surfaced for you, scored and ranked"
      />
      <PageBody>
        {state.kind === "loading" ? <DealsSkeleton /> : null}

        {state.kind === "error" ? (
          <ErrorState
            message={state.message}
            onRetry={() => {
              // Re-mount the effect by resetting to loading; simplest reliable
              // retry for a static-export page.
              setState({ kind: "loading" });
              void Promise.allSettled([api.listDeals(), api.getMe(), api.listRuns()]).then(
                ([dealsRes, meRes, runsRes]) => {
                  const deals = dealsRes.status === "fulfilled" ? dealsRes.value : undefined;
                  if (deals === undefined) {
                    setState({ kind: "error", message: messageFor(dealsRes) });
                    return;
                  }
                  setState({
                    kind: "ok",
                    deals,
                    me: meRes.status === "fulfilled" ? meRes.value : undefined,
                    latestRun: runsRes.status === "fulfilled" ? runsRes.value[0] : undefined,
                  });
                },
              );
            }}
          />
        ) : null}

        {state.kind === "ok" ? (
          <div className="animate-fade-in-up space-y-4">
            {state.me ? <StaleSettingsBanner me={state.me} /> : null}
            <RunErrorNotice run={state.latestRun} />

            <Card>
              <SectionHeader title="In view" description="Sorted by overall score" />
              {state.deals.length === 0 ? (
                <EmptyState
                  hasRun={state.latestRun !== undefined}
                  latestRun={state.latestRun}
                  nextRunAt={state.me?.next_run_at}
                />
              ) : (
                <>
                  <DealFilterBar
                    deals={state.deals}
                    filters={filters}
                    filteredCount={visibleDeals.length}
                    onChange={setFilters}
                  />
                  <CardBody className="p-0">
                    {visibleDeals.length === 0 ? (
                      <NoFilterMatch
                        onClear={() => {
                          setFilters(EMPTY_FILTERS);
                        }}
                      />
                    ) : (
                      <ul className="divide-y divide-stone-200 dark:divide-stone-800">
                        {visibleDeals.map((deal, i) => (
                          <li
                            key={`${deal.listing_id}|${deal.first_seen}`}
                            className="stagger-item animate-fade-in-up"
                            style={{ "--stagger-index": Math.min(i, 12) } as React.CSSProperties}
                          >
                            <DealCard deal={deal} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardBody>
                </>
              )}
            </Card>
          </div>
        ) : null}
      </PageBody>
    </>
  );
}

/** Pull a friendly message out of a settled allSettled result. */
function messageFor(result: PromiseSettledResult<unknown>): string {
  if (result.status === "rejected") {
    const err: unknown = result.reason;
    if (err instanceof ApiError) return err.toUserMessage();
  }
  return "Failed to load what's in view";
}

/** Editorial loading state — skeleton listing rows under a skeleton header. */
function DealsSkeleton() {
  return (
    <Card aria-busy aria-label="Loading what's in view">
      <div className="border-b border-stone-200 px-5 py-4 dark:border-stone-800">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="mt-2 h-3 w-40" />
      </div>
      <ul className="divide-y divide-stone-200 dark:divide-stone-800">
        {Array.from({ length: 5 }).map((_, i) => (
          <li key={i} className="flex items-start justify-between gap-4 px-5 py-4">
            <div className="min-w-0 flex-1 space-y-2.5">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-2/5" />
              <Skeleton className="h-3 w-3/4" />
            </div>
            <div className="flex shrink-0 flex-col items-end gap-2">
              <Skeleton className="h-5 w-5 rounded-full" />
              <Skeleton className="h-11 w-11 rounded-full" />
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Graceful error — no raw string dump in the layout; offers a retry. */
function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card>
      <CardBody className="flex flex-col items-center gap-4 px-6 py-14 text-center">
        <span className="text-stone-300 dark:text-stone-600">
          <BrokenScope />
        </span>
        <div className="space-y-1.5">
          <h2 className="font-serif text-2xl font-medium tracking-tight text-stone-900 dark:text-stone-100">
            We couldn&apos;t load what&apos;s in view
          </h2>
          <p className="mx-auto max-w-sm text-sm text-stone-500 dark:text-stone-400">
            Something interrupted the request. This is usually temporary — give it another try.
          </p>
          <p className="pt-1 text-xs text-stone-400 dark:text-stone-500">{message}</p>
        </div>
        <Button variant="primary" onClick={onRetry}>
          Try again
        </Button>
      </CardBody>
    </Card>
  );
}

/**
 * Editorial empty state. Distinguishes "no run yet" from "the last run found
 * nothing matching your criteria" — the existing latestRun logic, preserved.
 * References the next scheduled run DATE when we know it.
 */
function EmptyState({
  hasRun,
  latestRun,
  nextRunAt,
}: {
  hasRun: boolean;
  latestRun: RunSummary | undefined;
  nextRunAt: string | null | undefined;
}) {
  const finishedAt = latestRun?.finished_at
    ? new Date(latestRun.finished_at).toLocaleString()
    : null;
  const nextRunDate = formatNextRunDate(nextRunAt);

  const noRunCopy = nextRunDate
    ? `Nothing in view yet — your next run is ${nextRunDate}. You can also trigger one from the dashboard.`
    : "Nothing in view yet. Trigger a run from the dashboard, or wait for the next scheduled cycle — what we find will land here.";

  return (
    <CardBody className="flex flex-col items-center gap-4 px-6 py-16 text-center">
      <span className="text-stone-300 dark:text-stone-600">
        <ScopeHorizon />
      </span>
      <div className="space-y-1.5">
        <h2 className="font-serif text-2xl font-medium tracking-tight text-stone-900 dark:text-stone-100">
          {hasRun ? "Nothing matched this time" : "Nothing in view yet"}
        </h2>
        <p className="mx-auto max-w-sm text-sm text-stone-500 dark:text-stone-400">
          {hasRun
            ? finishedAt
              ? `Your last run finished ${finishedAt} and nothing cleared your criteria. Loosening filters or widening your search radius can help.`
              : "Your last run finished and nothing cleared your criteria. Loosening filters or widening your search radius can help."
            : noRunCopy}
        </p>
      </div>
    </CardBody>
  );
}

/** Filters hid everything. Offer a one-tap clear. */
function NoFilterMatch({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      <span className="text-stone-300 dark:text-stone-600">
        <ScopeHorizon className="h-16" />
      </span>
      <p className="text-sm text-stone-500 dark:text-stone-400">
        Nothing matches the active filters.
      </p>
      <Button variant="secondary" size="sm" onClick={onClear}>
        Clear filters
      </Button>
    </div>
  );
}
