# ADR 008: Latest-Run Deal Snapshot + Generic User-Feedback Store

## Status

Accepted

## Context

Two coupled problems prompted this decision.

1. **Stale deals accumulate across runs.** The deals table is keyed
   `(listing_id, first_seen)` where `first_seen` is the *write* timestamp.
   `persist_results` writes the full scored set on every run, so the same
   listing lands under a new sort key each run. `list_deals` queried the
   `user-id-index` with no run filter, returning an ever-growing pile from
   every run a user has ever had. The only cleanup was
   `REFRESH_DEALS_ON_RUN` — a destructive full/per-user wipe, off by default.
   A run also *skipped persistence entirely* when no deals were "new"
   (`filter_new_deals → persist_run_only`), so the displayed set could go
   stale even though the run produced a perfectly good scored set.

2. **Users want to keep deals they like.** A favorites feature, with a clear
   path to a future dislike → scoring-feedback → auto-ignore-list loop.

These interact: any mechanism that "clears old deals" must not delete a deal
the user has saved. The non-negotiable rules (`RemovalPolicy.RETAIN`, *never
auto-delete data*, decisions defensible to a risk committee) rule out a
destructive-wipe-by-default fix.

## Decision

**Non-destructive latest-run snapshot.**

- Every run persists its full scored-deal set tagged with `run_id`
  (`persist_results`, unchanged behavior). Graph routing changed so
  `persist_results` runs on *every* terminal path — the no-deals path no
  longer skips it. `persist_run_only` is deleted; the run audit record is
  written by the handler independently, so nothing is lost. A new
  `validate_persist` guardrail wrapper runs the registered
  `PersistResultsOutput` check before the graph ends, preserving the
  "guardrail between every node" invariant.
- After a successful run the worker stamps `last_run_id` (alongside the
  existing `last_run_at`) on the Users row. It is only written on the
  success path, so it always points at a run that actually persisted.
- `list_deals` resolves `last_run_id` from the Users row and returns only
  that run's rows (queried via `user-id-index`, filtered by `run_id`). No
  `last_run_id` ⇒ empty result — never a fallback to "show everything".
- Old runs' rows are **retained** (audit, queryable via `run-id-index`) and
  expire only via the pre-existing 90-day TTL. No new destructive path.
- The redundant handler-side `write_deals(new_deals)` is removed;
  `persist_results` solely owns persistence.

**Cross-user dedup fix.** `get_known_listing_ids` (used by
`filter_new_deals` to decide what's "new", which gates email drafting) was a
full-table scan across *all* users. It is now scoped per user via
`user-id-index`. Legacy single-tenant (`user_id=None`) keeps the full scan.

**Generic, dislike-ready feedback store.** A new DynamoDB table
`lookout-{env}-favorites` (PK `user_id`, SK `listing_id`), in `AuthStack`
beside the Users table (identity-scoped, written only by the API Lambda).
Each row carries `signal` (=`"FAVORITE"` today), `created_at`, an optional
`note`, and a **full snapshot of the deal at favorite-time**. No TTL. New
endpoints: `GET /me/favorites`, `POST|DELETE /me/deals/{id}/favorite`. The
deals list stamps `is_favorite` per row from one cheap projection Query.
DeleteItem is granted in a dedicated IAM statement scoped *only* to this
table's ARN, so deals/users/runs do not silently gain delete.

**`REFRESH_DEALS_ON_RUN` retained but dormant.** The destructive wipe is no
longer needed for correctness. It stays in the code, off by default, never
wired into any automatic path — an explicit ops-only escape hatch for
storage reclamation ahead of TTL.

## Consequences

### Positive

- The Deals tab reflects exactly the latest completed run, with zero data
  destruction. Full run history remains for audit and TTL-bounded retention
  — defensible to a risk committee.
- Favorites survive run rotation *and* the 90-day deal TTL because the
  favorite stores its own snapshot, decoupled from the deal lifecycle.
- The `signal` field makes adding `DISLIKE` (and later a scoring-feedback
  loop / auto-exclude setting) an additive change, not a migration.
- Cross-user notification suppression is fixed; one user's history no longer
  hides another user's new deals.
- No schema/GSI change to the deals table; no index backfill on deploy.

### Negative

- A favorite is a point-in-time snapshot: price/score can be stale relative
  to a newer run. This is by design (a saved record, not a live deal); the
  Favorites page shows it as "kept even after they age out" and could
  surface `created_at` to make staleness explicit.
- `list_deals` now does two extra reads (Users `get_item`, favorites Query).
  Both are single-key/projection reads at personal-project scale — rounding
  error.
- During an in-flight run, `last_run_id` still points at the previous
  completed run, so the Deals tab shows the last *complete* snapshot until
  the new run finishes. Acceptable — the in-flight progress bar already
  communicates that a run is underway, and showing a half-written run would
  be worse.

## Alternatives considered

- **Destructive wipe-and-refresh per run (excluding favorites).** Simpler
  reads (no run filter). Rejected: conflicts with the *never auto-delete
  data* / `RETAIN` non-negotiables, destroys the audit trail, and makes the
  favorites/feedback design fight the wipe instead of complementing it.
- **`favorited` flag on the deal row.** Avoids a second table. Rejected: the
  flag (and the deal) would still TTL-expire at 90 days and rotate out of
  the latest-run view, so a "saved" deal would silently vanish — the exact
  failure this feature exists to prevent.
- **Separate `favorites` and future `dislikes` tables.** Rejected as
  premature duplication; one feedback table with a `signal` discriminator is
  the smaller surface and makes the future loop a one-enum change.
- **Filter the Deals view by `run-id-index` instead of `user-id-index` +
  client filter.** Rejected: it would require post-filtering by `user_id`
  anyway and breaks the existing "legacy rows invisible to user-scoped
  queries" invariant (the `user-id-index` is sparse). Reusing
  `user-id-index` keeps blast radius minimal and needs no infra change.
