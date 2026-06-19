"use client";

import { Calendar, CalendarClock, KeyRound, Sparkles, Telescope } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AppShell, PageBody, PageHeader } from "@/components/AppShell";
import { AprSnapshotPill } from "@/components/AprSnapshotPill";
import { AuthGate } from "@/components/AuthGate";
import { IncentiveStackCard } from "@/components/IncentiveStackCard";
import { InventoryAnomalyPill } from "@/components/InventoryAnomalyPill";
import { MarketSignal } from "@/components/MarketSignal";
import { MarketTrendChart } from "@/components/MarketTrendChart";
import { QuotaBanner } from "@/components/QuotaBanner";
import { RunErrorNotice } from "@/components/RunErrorNotice";
import { RunProgress } from "@/components/RunProgress";
import { RunRow } from "@/components/RunRow";
import { ScoreGauge } from "@/components/ScoreGauge";
import { StaleSettingsBanner } from "@/components/StaleSettingsBanner";
import { UsedVsNewArbitragePill } from "@/components/UsedVsNewArbitragePill";
import { useInFlightRun } from "@/components/useInFlightRun";
import { Button, Card, CardBody, SectionHeader, Stat } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  dealTitle,
  discountPct,
  formatNextRun,
  relativeFuture,
  relativeTime,
} from "@/lib/dealFormat";
import type {
  ArbitrageResponse,
  Deal,
  IncentiveStackResponse,
  InventoryAnomalyResponse,
  MacroSnapshot,
  MarketSignalResponse,
  MeResponse,
  RunSummary,
  UsageResponse,
} from "@/lib/schemas";

type MarketSignalWindow = "30d" | "90d" | "6m" | "12m" | "24m";
type MarketSignalView = "personalized" | "market";

export default function DashboardPage() {
  return <AuthGate>{(user) => <AppShell user={user}>{<Body />}</AppShell>}</AuthGate>;
}

interface DashboardData {
  me: MeResponse;
  deals: Deal[];
  runs: RunSummary[];
  signal: MarketSignalResponse | null;
  usage: UsageResponse | null;
  incentives: IncentiveStackResponse | null;
  macro: MacroSnapshot | null;
  arbitrage: ArbitrageResponse | null;
  inventoryAnomaly: InventoryAnomalyResponse | null;
}

function Body() {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; data: DashboardData; partialErrors: string[] }
    | { kind: "error"; message: string }
  >({ kind: "loading" });
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  // Market signal window/view live in dashboard state so the window pills
  // re-fetch a new range without re-mounting the chart. View is fixed to
  // "personalized" for PR1 (Macro/Personal split lands later).
  const [marketSignalWindow, setMarketSignalWindow] = useState<MarketSignalWindow>("90d");
  const [marketSignalView] = useState<MarketSignalView>("personalized");

  const load = useCallback(async () => {
    // Partial-data resilience: one failed call (e.g. deals rate-limited)
    // shouldn't blank the whole dashboard. `me` is essential; everything else
    // degrades gracefully with a small notice.
    const [
      meR,
      dealsR,
      runsR,
      signalR,
      usageR,
      incentivesR,
      macroR,
      arbitrageR,
      inventoryAnomalyR,
    ] = await Promise.allSettled([
      api.getMe(),
      api.listDeals({ limit: 5 }),
      api.listRuns(),
      api.getMarketSignal({ window: marketSignalWindow, view: marketSignalView }),
      api.getUsage(),
      api.getIncentiveStack(),
      api.getMacroSnapshot(),
      api.getUsedVsNewArbitrage(),
      api.getInventoryAnomaly(),
    ]);
    if (meR.status === "rejected") {
      const message =
        meR.reason instanceof ApiError ? meR.reason.toUserMessage() : "Failed to load dashboard";
      setState({ kind: "error", message });
      return;
    }
    const partialErrors: string[] = [];
    if (dealsR.status === "rejected") partialErrors.push("listings");
    if (runsR.status === "rejected") partialErrors.push("recent runs");
    // signal/usage/incentives are quieter surfaces — they degrade silently
    // by rendering their empty/cold-start/placeholder states. No banner copy
    // required; the card itself owns its loading visual.
    setState({
      kind: "ok",
      data: {
        me: meR.value,
        deals: dealsR.status === "fulfilled" ? dealsR.value : [],
        runs: runsR.status === "fulfilled" ? runsR.value : [],
        signal: signalR.status === "fulfilled" ? signalR.value : null,
        usage: usageR.status === "fulfilled" ? usageR.value : null,
        incentives: incentivesR.status === "fulfilled" ? incentivesR.value : null,
        macro: macroR.status === "fulfilled" ? macroR.value : null,
        arbitrage: arbitrageR.status === "fulfilled" ? arbitrageR.value : null,
        inventoryAnomaly:
          inventoryAnomalyR.status === "fulfilled" ? inventoryAnomalyR.value : null,
      },
      partialErrors,
    });
  }, [marketSignalWindow, marketSignalView]);

  // When the in-flight run finishes, pull fresh dashboard data so the new
  // deals/runs appear without the user reloading.
  const { inFlight, markOptimisticallyRunning, connectionLost } = useInFlightRun({
    onCompleted: () => {
      void load();
    },
  });

  useEffect(() => {
    void load();
  }, [load]);

  const isRunning = inFlight?.in_flight ?? false;

  const handleTrigger = async () => {
    setTriggering(true);
    setTriggerError(null);
    // Optimistically flip the UI into "running" before the API roundtrip
    // returns — closes the race where the user could double-click while
    // waiting for the next in_flight poll.
    markOptimisticallyRunning();
    try {
      await api.triggerRun();
    } catch (err) {
      setTriggerError(err instanceof ApiError ? err.toUserMessage() : "Trigger failed");
    } finally {
      setTriggering(false);
    }
  };

  if (state.kind === "loading") {
    return (
      <>
        <PageHeader title="Dashboard" description="Overview of your runs and listings" />
        <PageBody>
          <p className="text-sm text-stone-500">Loading…</p>
        </PageBody>
      </>
    );
  }

  if (state.kind === "error") {
    return (
      <>
        <PageHeader title="Dashboard" />
        <PageBody>
          <p className="text-sm text-red-600">{state.message}</p>
        </PageBody>
      </>
    );
  }

  const { me, deals, runs, signal, usage, incentives, macro, arbitrage, inventoryAnomaly } =
    state.data;
  const { partialErrors } = state;
  const lastRun = runs[0];

  // Heuristic per the PR1 spec: keep it simple, derive available windows
  // from how many points we actually have. Empty signal = no pills shown
  // (component infers cold-start). Refined later when we have real history.
  const pointCount = signal?.points.length ?? 0;
  const availableWindows: MarketSignalWindow[] = pointCount === 0 ? [] : ["30d", "90d"];
  const isColdStart = pointCount === 0;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Overview of your account, recent runs, and what's in view"
        actions={
          <Button
            leadingIcon={<Telescope className="h-4 w-4" />}
            onClick={handleTrigger}
            disabled={triggering || isRunning}
          >
            {isRunning ? "Already running…" : triggering ? "Queueing…" : "Run now"}
          </Button>
        }
      />
      <PageBody>
        <RunProgress inFlight={inFlight} reconnecting={connectionLost} />
        <StaleSettingsBanner me={me} />
        <RunErrorNotice run={lastRun} />
        <QuotaBanner usage={usage} />
        {partialErrors.length > 0 ? (
          <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
            Couldn&apos;t load your {partialErrors.join(" and ")} just now — showing everything
            else. This is usually temporary.
          </div>
        ) : null}
        {triggerError ? (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
            {triggerError}
          </div>
        ) : null}

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Tier"
            variant="text"
            value={<span className="uppercase">{me.tier}</span>}
            tone={me.tier === "byok" ? "brand" : "neutral"}
            hint={
              me.tier === "byok"
                ? "Using your MarketCheck key"
                : "Using shared MarketCheck quota"
            }
            icon={<KeyRound className="h-5 w-5" />}
          />
          <Stat
            label="In view"
            value={deals.length}
            hint={deals.length > 0 ? "Top of your list shown below" : "Nothing in view yet"}
            icon={<Sparkles className="h-5 w-5" />}
          />
          <Stat
            label="Last run"
            variant="text"
            value={
              lastRun?.started_at
                ? new Date(lastRun.started_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })
                : "—"
            }
            secondary={
              lastRun?.started_at
                ? relativeTime(lastRun.started_at)
                : undefined
            }
            hint={lastRun?.status ?? "No runs yet"}
            icon={<Calendar className="h-5 w-5" />}
          />
          <Stat
            label="Next run"
            variant="text"
            value={formatNextRun(me.next_run_at)}
            secondary={relativeFuture(me.next_run_at) ?? undefined}
            hint={me.next_run_at ? "Scheduled · all times UTC" : "Not scheduled"}
            icon={<CalendarClock className="h-5 w-5" />}
          />
        </section>

        <section className="mt-8">
          <Card>
            <CardBody className="space-y-6">
              <MarketSignal
                latest={signal?.latest ?? null}
                isColdStart={isColdStart}
                {...(signal?.latest?.timestamp
                  ? { asOfTimestamp: signal.latest.timestamp }
                  : {})}
              />
              {/* The "as of" caption lives on MarketSignal above; the trend
                  chart sits in the same Card, so we intentionally don't pass
                  asOfTimestamp here to avoid a duplicate line. */}
              <MarketTrendChart
                points={signal?.points ?? []}
                currentWindow={marketSignalWindow}
                availableWindows={availableWindows}
                onWindowChange={setMarketSignalWindow}
              />
              {/* Supporting metric pills sit in a quiet row under the trend
                  chart. They are intentionally smaller than the chart's own
                  factor chips so they read as ambient context rather than
                  another headline; each pill is responsible for hiding itself
                  when its underlying data hasn't been cached yet. */}
              <div className="flex flex-wrap items-center gap-2">
                <AprSnapshotPill data={macro} />
                <UsedVsNewArbitragePill data={arbitrage} />
                <InventoryAnomalyPill data={inventoryAnomaly} />
              </div>
            </CardBody>
          </Card>
        </section>

        {/* Skip the section wrapper when the card has nothing to render —
            keeps the dashboard's vertical rhythm clean during the loading
            window and for the non-EV "hidden_reason" case. */}
        {incentives !== null && incentives.hidden_reason !== "non-ev-prefs" ? (
          <section className="mt-8">
            <IncentiveStackCard data={incentives} />
          </section>
        ) : null}

        <section className="mt-8 grid gap-4 lg:grid-cols-2">
          <Card>
            <SectionHeader
              title="Top of your list"
              description="Highest scored from your most recent run"
            />
            <CardBody className="p-0">
              {deals.length === 0 ? (
                <p className="px-5 py-6 text-sm text-stone-500">
                  Nothing in view yet. Trigger a run to populate this list.
                </p>
              ) : (
                <ul className="divide-y divide-stone-200 dark:divide-stone-800">
                  {deals.slice(0, 5).map((deal) => (
                    <li
                      key={`${deal.listing_id}|${deal.first_seen}`}
                      className="flex items-center justify-between gap-4 px-5 py-3"
                    >
                      <div className="min-w-0 space-y-0.5">
                        <p className="truncate text-sm font-medium text-stone-900 dark:text-stone-100">
                          {dealTitle(deal)}
                        </p>
                        {deal.selling_price !== undefined ? (
                          <p className="text-xs tabular-nums text-stone-500">
                            ${deal.selling_price.toLocaleString()}
                            {discountPct(deal) !== null
                              ? ` · ${String(discountPct(deal))}% off MSRP`
                              : ""}
                          </p>
                        ) : null}
                      </div>
                      <ScoreGauge score={deal.overall_score} size={38} className="shrink-0" />
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <SectionHeader title="Recent runs" description="Most recent agent executions" />
            <CardBody className="p-0">
              {runs.length === 0 ? (
                <p className="px-5 py-6 text-sm text-stone-500">No runs yet.</p>
              ) : (
                <ul className="divide-y divide-stone-200 dark:divide-stone-800">
                  {runs.slice(0, 5).map((run) => (
                    <RunRow key={run.run_id} run={run} />
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </section>
      </PageBody>
    </>
  );
}
