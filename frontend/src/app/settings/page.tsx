"use client";

import { Telescope } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AppShell, PageBody, PageHeader } from "@/components/AppShell";
import { AuthGate } from "@/components/AuthGate";
import { RunProgress } from "@/components/RunProgress";
import { ByokSection } from "@/components/settings/ByokSection";
import { PrefsForm } from "@/components/settings/PrefsForm";
import { useInFlightRun } from "@/components/useInFlightRun";
import { UsageQuotaPill } from "@/components/UsageQuotaPill";
import { Badge, Button, Card, CardBody, SectionHeader, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { MeResponse, PrefsResponse } from "@/lib/schemas";

export default function SettingsPage() {
  return <AuthGate>{(user) => <AppShell user={user}>{<Body />}</AppShell>}</AuthGate>;
}

interface Loaded {
  me: MeResponse;
  prefs: PrefsResponse;
}

function Body() {
  const toast = useToast();
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ok"; data: Loaded } | { kind: "error"; message: string }
  >({ kind: "loading" });
  const [triggering, setTriggering] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [me, prefs] = await Promise.all([api.getMe(), api.getPrefs()]);
      setState({ kind: "ok", data: { me, prefs } });
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.toUserMessage() : "Failed to load settings";
      setState({ kind: "error", message });
    }
  }, []);

  // Refresh /me + prefs when an in-flight run completes (last_run_at, etc.).
  const { inFlight, markOptimisticallyRunning, connectionLost } = useInFlightRun({
    onCompleted: () => {
      void loadAll();
    },
  });

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const isRunning = inFlight?.in_flight ?? false;

  const handleTrigger = async () => {
    setTriggering(true);
    markOptimisticallyRunning();
    try {
      await api.triggerRun();
      toast.success("Run queued", { description: "Fresh deals will appear on your dashboard." });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.toUserMessage() : "Trigger failed");
    } finally {
      setTriggering(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Settings"
        description="Search, vehicle, deal criteria, scoring, schedule, and your API key"
        actions={
          <div className="flex items-center gap-3">
            {state.kind === "ok" ? (
              <Badge tone={state.data.me.tier === "byok" ? "brand" : "neutral"}>
                {state.data.me.tier}
              </Badge>
            ) : null}
            <Button
              leadingIcon={<Telescope className="h-4 w-4" />}
              onClick={handleTrigger}
              disabled={isRunning}
              loading={triggering}
            >
              {isRunning ? "Already running…" : triggering ? "Queueing…" : "Run now"}
            </Button>
          </div>
        }
      />
      <PageBody>
        <RunProgress inFlight={inFlight} reconnecting={connectionLost} />

        {state.kind === "loading" ? (
          <p className="text-sm text-fg-muted">Loading preferences…</p>
        ) : null}

        {state.kind === "error" ? <ErrorState message={state.message} onRetry={loadAll} /> : null}

        {state.kind === "ok" ? (
          <div className="animate-fade-in-up space-y-4">
            {/* MarketCheck call-usage pill — quiet, ambient, so users know
                where they sit on the shared default-tier quota before a
                run gets blocked. BYOK users see their own counter (no
                amber/red tint since their key has its own ceiling). */}
            <div className="flex">
              <UsageQuotaPill />
            </div>
            {state.data.me.tier === "default" ? (
              <Card>
                <CardBody>
                  <p className="text-xs text-fg-muted dark:text-stone-400">
                    You&apos;re on the default tier. Schedule, listings-per-run, and scoring
                    thresholds are locked. Add your MarketCheck API key below to upgrade to BYOK
                    and unlock them.
                  </p>
                </CardBody>
              </Card>
            ) : null}

            {/* Preferences first — the everyday controls — in a cohesive flow:
                Search → Vehicle & trade-in → Deal criteria → Scoring → Schedule. */}
            <Card>
              <SectionHeader
                title="Preferences"
                description="What the agent looks for and how it scores deals"
              />
              <CardBody>
                <PrefsForm
                  prefs={state.data.prefs}
                  nextRunAt={state.data.me.next_run_at}
                  onSaved={(next) => {
                    setState({ kind: "ok", data: { ...state.data, prefs: next } });
                  }}
                  onTierViolation={() => {
                    // Resync caps + writable paths so the BYOK-only fields the
                    // user just tried to edit re-lock to match their tier.
                    void loadAll();
                  }}
                />
              </CardBody>
            </Card>

            {/* MarketCheck API key (BYOK) last — an integration / advanced
                setting, the gateway to the locked controls above. */}
            <Card>
              <SectionHeader
                title="MarketCheck API key"
                description="Bring your own key to unlock schedule, listings-per-run, and scoring controls"
                badge="BYOK"
              />
              <CardBody>
                <ByokSection
                  tier={state.data.me.tier}
                  configured={state.data.me.marketcheck_secret_configured}
                  onChanged={() => {
                    void loadAll();
                  }}
                />
              </CardBody>
            </Card>
          </div>
        ) : null}
      </PageBody>
    </>
  );
}

/** Graceful error state, matching the editorial language used across screens. */
function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card>
      <CardBody className="flex flex-col items-center gap-4 px-6 py-14 text-center">
        <div className="space-y-1.5">
          <h2 className="font-serif text-2xl font-medium tracking-tight text-stone-900 dark:text-stone-100">
            We couldn&apos;t load your settings
          </h2>
          <p className="mx-auto max-w-sm text-sm text-fg-muted dark:text-stone-400">
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
