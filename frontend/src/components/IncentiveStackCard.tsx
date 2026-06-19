import { ChevronRight } from "lucide-react";

import { Card, CardBody, SectionHeader } from "@/components/ui";
import type { IncentiveItem, IncentiveStackResponse } from "@/lib/schemas";

interface IncentiveStackCardProps {
  /**
   * The qualified stack and any expiration / funding warnings the backend
   * surfaced. `null` while the API call is in flight (or after a soft
   * failure — the dashboard passes null on a rejected promise and we render
   * a quiet placeholder instead of an angry red banner). If `hidden_reason`
   * is set the card removes itself from layout entirely.
   */
  data: IncentiveStackResponse | null;
}

const JURISDICTION_LABEL: Record<IncentiveItem["jurisdiction"], string> = {
  federal: "Federal",
  state: "state",
  utility: "utility",
};

/**
 * Per-tier warning windows mirror `_EXPIRATION_WARNING_WINDOW_BY_TIER` in
 * `src/api/incentives.py`. We re-evaluate the window client-side so the
 * per-row "Expires Mon DD" pill stays in sync with the warning block at
 * the bottom of the card: a row only earns the pill if its expiration
 * falls inside its tier's window. Keeping both ends keyed off the same
 * table means there's never a row with a pill but no matching warning
 * (or vice versa) when the user reloads on a date boundary.
 */
const EXPIRATION_WINDOW_DAYS: Record<IncentiveItem["jurisdiction"], number> = {
  federal: 90,
  state: 42,
  utility: 21,
};

const SHORT_MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/**
 * Format an ISO expiration date string ("2026-06-30" or
 * "2026-06-30T00:00:00+00:00") as "Expires Jun 30".
 *
 * Returns `null` when:
 *   - the input is null / undefined / unparseable
 *   - the resulting date is outside the row's tier-specific warning window
 *
 * The card uses `null` to skip rendering the pill — only time-sensitive
 * rows get the visual treatment.
 */
function expirationPillLabel(
  rawDate: string | null | undefined,
  jurisdiction: IncentiveItem["jurisdiction"],
): string | null {
  if (!rawDate) return null;
  // Date constructor handles ISO date + ISO datetime; bail on NaN.
  const expiresAt = new Date(rawDate);
  if (Number.isNaN(expiresAt.getTime())) return null;

  const now = new Date();
  const msPerDay = 24 * 60 * 60 * 1000;
  const daysRemaining = Math.round(
    (expiresAt.getTime() - now.getTime()) / msPerDay,
  );
  if (daysRemaining < 0) return null;
  if (daysRemaining > EXPIRATION_WINDOW_DAYS[jurisdiction]) return null;

  const month = SHORT_MONTHS[expiresAt.getUTCMonth()] ?? "";
  const day = expiresAt.getUTCDate();
  return `Expires ${month} ${String(day)}`;
}

/**
 * Above three items the stack starts to crowd the eye — collapse into a
 * <details> so the card stays tidy by default while still letting the user
 * see everything in one click. At/under three rows we leave the body
 * always-expanded; the disclosure chrome would be more noise than help.
 */
const COLLAPSE_THRESHOLD = 3;

/**
 * "across Federal + state + utility" — derives the jurisdictional summary
 * from the actual items in the stack so we never claim a category the user
 * doesn't qualify for. Maintains a fixed display order (federal first, then
 * state, then utility) so the line reads the same way every render
 * regardless of source ordering.
 */
function jurisdictionsLine(items: IncentiveItem[]): string {
  const order: IncentiveItem["jurisdiction"][] = ["federal", "state", "utility"];
  const present = new Set(items.map((i) => i.jurisdiction));
  const labels = order
    .filter((j) => present.has(j))
    .map((j) => JURISDICTION_LABEL[j]);
  if (labels.length === 0) return "";
  if (labels.length === 1) return `across ${labels[0] ?? ""}`;
  if (labels.length === 2) return `across ${labels[0] ?? ""} + ${labels[1] ?? ""}`;
  return `across ${labels[0] ?? ""} + ${labels[1] ?? ""} + ${labels[2] ?? ""}`;
}

/**
 * The parenthetical suffix for an item row. Federal 30D gets fixed
 * "you qualify by income" copy until we have an income field on prefs.
 * Otherwise we lean on the backend's `stacks` flag: `true` → "stacks",
 * `false` → "replaces other state programs" (heuristic — state-program
 * exclusivity is the typical non-stacking case for now).
 */
function stackSuffix(item: IncentiveItem): string {
  if (item.id === "federal_30d") return "you qualify by income";
  if (item.stacks) return "stacks";
  return "replaces other state programs";
}

/** "Federal" / "state rebate" / "utility" — the parenthetical jurisdiction
 *  word used inside the per-row suffix. State items read more naturally as
 *  "state rebate" than the bare "state" we use in the summary line. */
function rowJurisdictionLabel(j: IncentiveItem["jurisdiction"]): string {
  switch (j) {
    case "federal":
      return "Federal";
    case "state":
      return "state rebate";
    case "utility":
      return "utility";
  }
}

/** "$11,500" — tabular-nums-friendly USD formatter. No cents; incentives
 *  are always whole dollars in the source data. */
function formatUsd(value: number): string {
  return `$${Math.round(value).toLocaleString()}`;
}

/** One bullet in the stack list. Includes a right-aligned amber pill
 *  ("Expires Jun 30") when the row's expiration falls inside its tier's
 *  warning window — gives the user an at-a-glance "this is the time-
 *  sensitive line item" cue without making them parse the warning text
 *  below the list. */
function IncentiveRow({ item }: { item: IncentiveItem }) {
  const pillLabel = expirationPillLabel(item.expiration_date, item.jurisdiction);
  return (
    <li className="flex items-baseline gap-2 text-sm text-stone-800 dark:text-stone-200">
      <span aria-hidden="true" className="text-stone-400 dark:text-stone-500">
        •
      </span>
      <span className="min-w-0 flex-1">
        <span className="font-medium">{item.title}</span>
        <span className="text-stone-600 dark:text-stone-400">
          {" — "}
          <span className="tabular-nums">{formatUsd(item.amount_usd)}</span>
          {" "}
          <em className="not-italic text-stone-500 dark:text-stone-500">
            ({rowJurisdictionLabel(item.jurisdiction)}, {stackSuffix(item)})
          </em>
        </span>
      </span>
      {pillLabel ? (
        <span
          data-testid="incentive-expiration-pill"
          className="ml-2 shrink-0 text-amber-700 dark:text-amber-400 text-xs font-mono tabular-nums"
        >
          {pillLabel}
        </span>
      ) : null}
    </li>
  );
}

/** One amber warning row, prefixed with the editorial chevron. */
function WarningRow({ text }: { text: string }) {
  return (
    <li
      data-testid="incentive-warning"
      className="flex items-baseline gap-2 text-xs text-amber-700 dark:text-amber-400"
    >
      <ChevronRight
        aria-hidden="true"
        className="h-3 w-3 shrink-0 translate-y-0.5"
      />
      <span>{text}</span>
    </li>
  );
}

/** The body of the card — items + warnings. Shared between the
 *  always-expanded and <details>-wrapped variants so they render identically. */
function StackBody({
  items,
  warnings,
}: {
  items: IncentiveItem[];
  warnings: string[];
}) {
  return (
    <>
      <ul className="space-y-1.5">
        {items.map((item) => (
          <IncentiveRow key={item.id} item={item} />
        ))}
      </ul>
      {warnings.length > 0 ? (
        <ul className="space-y-1">
          {warnings.map((w, i) => (
            <WarningRow key={`${String(i)}|${w}`} text={w} />
          ))}
        </ul>
      ) : null}
    </>
  );
}

/**
 * The "Your incentive stack" surface — federal + state + utility programs
 * the user qualifies for, with explicit stackability copy and expiration
 * warnings. Editorial voice (serif headlines, "you qualify by income"
 * instead of "free money") per the brand guide.
 *
 * Visibility rules:
 * - `data?.hidden_reason === "non-ev-prefs"` → renders nothing (smart
 *   default for non-EV shoppers; not a dismiss button).
 * - `data === null` → renders nothing. The dashboard fires this surface in
 *   parallel with the rest of the page; a Loading placeholder would flash a
 *   skeleton that's noisier than just-rendering-nothing for the ~ms before
 *   the promise resolves. On a hard API failure the dashboard also passes
 *   null and we stay quiet — never a red error here.
 * - empty items + no hidden_reason → "No incentives found" placeholder.
 * - >COLLAPSE_THRESHOLD items → wraps in <details> so users can fold the
 *   list when the card grows.
 */
export function IncentiveStackCard({ data }: IncentiveStackCardProps) {
  if (data?.hidden_reason === "non-ev-prefs") return null;

  if (data === null) return null;

  const items = data.stack.items;
  const warnings = data.warnings;

  if (items.length === 0) {
    return (
      <Card>
        <SectionHeader title="Your incentive stack" />
        <CardBody>
          <p className="text-sm text-stone-500 dark:text-stone-500">
            No incentives found for your area yet — we&apos;ll surface them as
            soon as a matching program lands.
          </p>
        </CardBody>
      </Card>
    );
  }

  const total = data.stack.total_usd;
  const summary = jurisdictionsLine(items);
  const collapsible = items.length > COLLAPSE_THRESHOLD;

  // Editorial headline — serif "Your incentive stack" anchors the surface
  // alongside the same serif used by MarketSignal's endpoints. The big
  // total numeral sits below in matched serif weight.
  const heading = (
    <div className="space-y-0.5">
      <h2 className="font-serif text-lg text-stone-900 dark:text-stone-100">
        Your incentive stack
      </h2>
      <p
        className="font-serif text-2xl font-medium tabular-nums text-stone-900 dark:text-stone-100"
        data-testid="incentive-total"
      >
        {formatUsd(total)}
      </p>
      {summary ? (
        <p className="text-xs text-stone-500 dark:text-stone-500">{summary}</p>
      ) : null}
    </div>
  );

  return (
    <Card>
      <CardBody className="space-y-4">
        {collapsible ? (
          <details className="group" data-testid="incentive-details" open>
            <summary
              className="focus-ring flex cursor-pointer list-none items-start justify-between gap-3 marker:hidden [&::-webkit-details-marker]:hidden"
            >
              {heading}
              <ChevronRight
                aria-hidden="true"
                className="mt-1 h-3.5 w-3.5 shrink-0 text-stone-400 transition-transform duration-fast ease-editorial group-open:rotate-90"
              />
            </summary>
            <div className="mt-4 space-y-3">
              <StackBody items={items} warnings={warnings} />
            </div>
          </details>
        ) : (
          <>
            {heading}
            <StackBody items={items} warnings={warnings} />
          </>
        )}
      </CardBody>
    </Card>
  );
}
