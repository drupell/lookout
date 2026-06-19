# ADR 010: Calibration Log Scaffolding

## Status

Accepted

## Context

The market-signal feature (ADR 009) tells a user "Now / Quiet / Not yet" at
the top of their dashboard. The signal is framed as **descriptive of the
present, not predictive of the future** — but users will inevitably read it
as predictive ("the signal said Now, did the next month actually get
cheaper?"). A wrong headline that the next 30 days disagree with doesn't
just look broken; it spends user trust we can't easily get back.

The plan ("market signal + incentive stack") promises a 90-day calibration
audit: at the 90-day mark per user we look back and ask whether the
"Now / Not yet" labels lined up with what the matching-listings price did
in the following ~30 days. That audit produces three outputs:

1. **Internal:** do we need to re-weight the five factors?
2. **User-facing (v2):** a meta-chart showing "how often our signal aligned
   with the next 30 days" — radical transparency about our own accuracy.
3. **Portfolio:** "I built a calibration loop that audited the system's own
   predictions" reads strongly for finance/fintech roles where decisions
   without audit trails are unshippable.

The audit can only be meaningful if it has data. We cannot backfill — a
snapshot has to be written at the moment a user saw a given signal,
against the criteria they had at that time. Every day we delay scaffolding
the persistence + read path is a day of audit data we can never recover.

A second concern is criteria parity. If a user widens their radius from
50 → 100 miles between two snapshots, the median matching-listing price
will move regardless of what the market does. The auditor must be able to
exclude (or annotate) cross-criteria comparisons rather than mistake them
for signal failures.

## Decision

We scaffold the calibration data infrastructure now, ahead of the v2 audit
implementation. Three changes:

### 1. Snapshot row carries the criteria it was scored against

`persist_market_snapshot` now stamps a `prefs_snapshot` attribute on every
row it writes — a tight projection of the search criteria that determined
the matching-listings population for that run:

- `location_zip`, `radius_miles`, `max_vehicle_age_years`
- `body_styles`, `fuel_types`
- `min_price_usd`, `max_price_usd`, `max_mileage_miles`
- `included_brands`, `excluded_brands`, `excluded_models`

Each row already carried `composite_index`, factor contributions,
`flower_position`, `label`, `started_at` (the SK), `median_discount_pct`,
`median_eff_price_usd`, `listing_count`, and the `macro_index` /
`personal_index` decomposition. Adding `prefs_snapshot` closes the
criteria-parity gap without touching the existing schema or guardrail.

The attribute is omitted when the prefs lookup fails (the snapshot is
still more valuable than the side-channel context), so legacy rows
without it are treated as "criteria unknown for this snapshot" by the
auditor — they simply aren't eligible for comparison.

### 2. New read endpoint: `GET /me/calibration`

A purpose-built audit endpoint at `src/api/calibration.py`. Distinct from
`GET /me/signal` because the use cases diverge:

- `/me/signal` is a render endpoint (chart + flower) and projects to the
  frontend wire shape (`factor_contribs` short-form, latest point, etc.).
- `/me/calibration` is an audit endpoint and projects to a richer shape
  (`factor_contributions` long-form, `median_eff_price_usd` and
  `median_discount_pct` as optional floats, full `macro_index` /
  `personal_index` split, `prefs_snapshot` pass-through, label-bucket
  summary).

The endpoint never raises: empty store and store-exception alike degrade
to `points=[]` + zeroed summary. Per-row tolerance follows the same
`safeParse + drop` pattern the frontend uses — a single malformed row
doesn't blank the audit log.

Window support: 30d, 90d, 6m, 12m, 24m. Default 90d (the audit cycle).
Unknown windows fall through to 90d at the store layer; the endpoint
echoes the caller's requested string so a stale dashboard tab doesn't 400.

Partials (guardrail-blocked runs) are excluded at the store boundary —
the audit's contract with the chart layer is different. The chart shows
them as greyed dots; the audit must not see them, because the trailing
median math is poisoned by incomplete data.

### 3. The 90-day audit metric (computational sketch)

Run offline against the calibration endpoint's output:

```
for snapshot s in user's history where now - s.timestamp >= 90 days:
    if s.label == "Now":
        # Find the next-30d-after-s window of snapshots with prefs_snapshot
        # equivalent to s.prefs_snapshot. Compute the median of their
        # median_eff_price_usd. Compare to s.median_eff_price_usd.
        # A "hit" = the next-30d median was lower (the call to act paid off).
    if s.label == "Not yet":
        # Symmetric: a "hit" = the next-30d median rose or held flat
        # (the call to wait was vindicated, or at least not punished).
    # "Quiet" snapshots aren't scored — the signal is explicitly
    # editorial-neutral in that band.
```

The output is a per-user (and aggregated) hit rate per label, plus a
distribution of magnitudes (was the move $50 or $5,000?). This drives the
v2 meta-chart and feeds the internal "are the factor weights right"
review.

## Consequences

### Positive

- The 90-day audit can run as soon as 90 days of users exist. No
  backfill, no rebuild, no migration.
- Criteria parity is provable, not assumed. The auditor can drop or flag
  cross-criteria comparisons rather than silently inflate (or deflate)
  the hit rate.
- The audit endpoint is a clean separation of concerns from the render
  endpoint. Changing one (e.g. richer factor breakdown) doesn't risk the
  other (e.g. silent breakage of the chart). Both read from the same
  store column, projected to different wire shapes.
- "Never raises" + per-row tolerance means a single bad row from any
  source (math regression, partial write, schema drift) never blanks the
  audit log. Same fault-tolerance idiom as `/me/signal`.
- The 24-month retention promise on snapshots (no TTL) gives the v2
  meta-chart enough history to be meaningful when it ships.

### Negative

- `prefs_snapshot` adds modest write-time overhead and one more attribute
  per snapshot row. Tiny in absolute terms (a few hundred bytes); we
  decided that's acceptable for the audit value.
- Legacy rows written before this ADR don't have `prefs_snapshot`, so
  they're not eligible for criteria-parity comparisons. They still
  contribute to the label-bucket summary and survive in the chart.
- The audit metric is offline-only for now — the endpoint serves raw
  audit material; the actual hit-rate computation is the v2 step. We
  considered computing the hit rate inside the endpoint and rejected it
  (see Alternatives).

### Risks / non-risks

- **Risk: prefs churn between snapshots is the norm, not the exception**
  — many users will tune their search criteria over a 90-day window. We
  accept that the eligible-comparison subset is smaller than the total
  snapshot count; the audit publishes both numbers.
- **Non-risk: criteria-parity is too strict.** The matching-listings
  population doesn't have to be byte-identical for the comparison to be
  meaningful — exact-equality on `prefs_snapshot` is the v1 rule, and v2
  can soften to "within tolerance" (e.g. radius differs by <10mi) without
  changing the persisted data.

## Alternatives considered

- **Defer scaffolding until the audit is ready to ship.** Rejected: data
  has to accumulate to be auditable; we'd be starting from 0 again at
  audit time. The whole point of "scaffold day-one" is that the 90-day
  clock starts the day the first snapshot lands.
- **Capture the full Preferences object on every snapshot.** Rejected:
  most fields (scoring thresholds, schedule, BYOK metadata) have nothing
  to do with the matching-listings population. Persisting them inflates
  storage and risks leaking secrets-adjacent fields into the audit log.
  The tight projection in `prefs_snapshot` is the minimum that supports
  criteria-parity comparisons.
- **Compute the hit rate inside `GET /me/calibration`.** Rejected for v1:
  it requires reading 90 days of snapshots *plus* the next 30 days of
  prefs-matched snapshots per row — a quadratic read pattern that gets
  expensive fast on a per-request endpoint. v2 either pre-computes the
  hit rate in a nightly cron and exposes the result, or moves the audit
  to an offline notebook. The v1 endpoint serves raw audit material so
  both v2 paths remain open.
- **Reuse `/me/signal` for the audit.** Rejected: the chart shape is
  optimized for rendering (short field names, latest-point caching,
  no `prefs_snapshot` exposure). Bolting audit fields onto the render
  endpoint either bloats the render payload or forces conditional
  projection logic — both worse than one purpose-built endpoint.
