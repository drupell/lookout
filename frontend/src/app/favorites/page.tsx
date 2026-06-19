"use client";

import { useEffect, useState } from "react";

import { AppShell, PageBody, PageHeader } from "@/components/AppShell";
import { AuthGate } from "@/components/AuthGate";
import { DealCard } from "@/components/DealCard";
import { BrokenScope, SavedHorizon } from "@/components/illustrations/Illustration";
import { Button, Card, CardBody, SectionHeader, Skeleton } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { Favorite } from "@/lib/schemas";

export default function FavoritesPage() {
  return <AuthGate>{(user) => <AppShell user={user}>{<Body />}</AppShell>}</AuthGate>;
}

type FavoritesState =
  | { kind: "loading" }
  | { kind: "ok"; favorites: Favorite[] }
  | { kind: "error"; message: string };

function Body() {
  const [state, setState] = useState<FavoritesState>({ kind: "loading" });

  function load() {
    setState({ kind: "loading" });
    api
      .listFavorites()
      .then((favorites) => {
        setState({ kind: "ok", favorites });
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError ? err.toUserMessage() : "Failed to load favorites";
        setState({ kind: "error", message });
      });
  }

  useEffect(() => {
    load();
    // load is stable for the page's lifetime; intentionally run once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Loss-safe removal. DealCard owns the heart: it calls api.removeFavorite()
  // first and only invokes this callback *after* the request succeeds (on
  // failure it reverts its own state and surfaces a toast). So by the time we
  // run, the removal is durable — we simply drop the row from the rendered
  // list. We never optimistically remove here, so a failed request can never
  // leave a row missing.
  const handleUnfavorite = (listingId: string) => {
    setState((prev) =>
      prev.kind === "ok"
        ? { kind: "ok", favorites: prev.favorites.filter((f) => f.listing_id !== listingId) }
        : prev,
    );
  };

  return (
    <>
      <PageHeader
        title="Favorites"
        description="Deals you saved — kept even after they age out of a run"
      />
      <PageBody>
        {state.kind === "loading" ? <FavoritesSkeleton /> : null}

        {state.kind === "error" ? (
          <ErrorState message={state.message} onRetry={load} />
        ) : null}

        {state.kind === "ok" ? (
          <div className="animate-fade-in-up">
            <Card>
              <SectionHeader title="Saved deals" description="Newest first" />
              {state.favorites.length === 0 ? (
                <EmptyState />
              ) : (
                <CardBody className="p-0">
                  <ul className="divide-y divide-stone-200 dark:divide-stone-800">
                    {state.favorites.map((fav, i) =>
                      fav.deal ? (
                        <li
                          key={fav.listing_id}
                          className="stagger-item animate-fade-in-up"
                          style={{ "--stagger-index": Math.min(i, 12) } as React.CSSProperties}
                        >
                          <DealCard deal={fav.deal} onUnfavorite={handleUnfavorite} />
                        </li>
                      ) : null,
                    )}
                  </ul>
                </CardBody>
              )}
            </Card>
          </div>
        ) : null}
      </PageBody>
    </>
  );
}

/** Editorial loading state — skeleton deal rows under a skeleton header. */
function FavoritesSkeleton() {
  return (
    <Card aria-busy aria-label="Loading favorites">
      <div className="border-b border-stone-200 px-5 py-4 dark:border-stone-800">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="mt-2 h-3 w-24" />
      </div>
      <ul className="divide-y divide-stone-200 dark:divide-stone-800">
        {Array.from({ length: 4 }).map((_, i) => (
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

/** Graceful error — offers a retry, no raw dump in the layout. */
function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card>
      <CardBody className="flex flex-col items-center gap-4 px-6 py-14 text-center">
        <span className="text-stone-300 dark:text-stone-600">
          <BrokenScope />
        </span>
        <div className="space-y-1.5">
          <h2 className="font-serif text-2xl font-medium tracking-tight text-stone-900 dark:text-stone-100">
            We couldn&apos;t load your favorites
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

/** Inviting editorial empty state — a saved-deals shelf waiting to be filled. */
function EmptyState() {
  return (
    <CardBody className="flex flex-col items-center gap-4 px-6 py-16 text-center">
      <span className="text-stone-300 dark:text-stone-600">
        <SavedHorizon />
      </span>
      <div className="space-y-1.5">
        <h2 className="font-serif text-2xl font-medium tracking-tight text-stone-900 dark:text-stone-100">
          Nothing saved yet
        </h2>
        <p className="mx-auto max-w-sm text-sm text-stone-500 dark:text-stone-400">
          Tap the heart on any deal to keep it here. Favorites stick around even after a
          listing ages out of a run — your own quiet shortlist.
        </p>
      </div>
    </CardBody>
  );
}
