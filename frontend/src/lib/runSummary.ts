/**
 * Display helpers for an agent run — the humane sentence that replaces a
 * truncated UUID in the recent-runs list.
 *
 * The `/me/runs` API forwards every DynamoDB field via `RunSummarySchema`'s
 * `.passthrough()`, so these helpers can lean on counts and triggers that
 * exist on the wire today.
 */
import type { RunSummary } from "./schemas";

// Long-form labels for the `source_errors` codes the agent emits — shared by
// the top-of-page `RunErrorNotice` card and the per-row disclosure body.
const SOURCE_ERROR_LABEL: Record<string, string> = {
  "marketcheck:pagination_exceeded":
    "MarketCheck free-tier pagination ceiling hit (500 rows total). Drop " +
    "max_pages to 1 in Settings, or upgrade your MarketCheck plan if you " +
    "need broader pulls.",
  "marketcheck:quota_exhausted":
    "MarketCheck monthly quota reached on the shared default-tier key. " +
    "Runs resume next month, or consider switching to BYOK for higher quota.",
  "marketcheck:rate_limited":
    "MarketCheck rate-limited the request (429). The agent had nothing to score. " +
    "Try again shortly or check your account's quota in the MarketCheck portal.",
  "marketcheck:auth_failed":
    "MarketCheck rejected our API key. Re-verify the key — for BYOK users this " +
    "means the key on file is invalid; for default tier this is a config issue.",
  "marketcheck:unavailable":
    "MarketCheck couldn't be reached. Network blip or service outage; try again.",
};

/** Long, explanatory label for a source_errors code. */
export function sourceErrorLabel(code: string): string {
  return SOURCE_ERROR_LABEL[code] ?? `Source error: ${code}`;
}

// Short forms used inline in row blurbs — terse, sentence-fragment style.
const SOURCE_ERROR_SHORT: Record<string, string> = {
  "marketcheck:pagination_exceeded": "MarketCheck free-tier pagination ceiling",
  "marketcheck:quota_exhausted": "MarketCheck monthly quota reached",
  "marketcheck:rate_limited": "MarketCheck rate-limited",
  "marketcheck:auth_failed": "MarketCheck auth failed",
  "marketcheck:unavailable": "MarketCheck unavailable",
};

/** Short label for a source_errors code, suitable for inline appending. */
export function sourceErrorShort(code: string): string {
  return SOURCE_ERROR_SHORT[code] ?? `source error: ${code}`;
}

/** "Stopped at" friendly form: lowercase + underscores → spaces. */
function humanizeTrigger(name: string): string {
  return name.toLowerCase().replaceAll("_", " ");
}

function plural(n: number, singular: string, plural?: string): string {
  return `${String(n)} ${n === 1 ? singular : (plural ?? `${singular}s`)}`;
}

/**
 * The one-line sentence shown on each recent-run row, after the status badge
 * and the relative time. Returns `null` when the status is unknown — caller
 * renders no blurb in that case (just badge + time).
 */
export function outcomeBlurb(run: RunSummary): string | null {
  const status = (run.status ?? "").toUpperCase();
  const found = run.deals_found;
  const above = run.deals_above_threshold;
  const drafts = run.drafts_produced;
  const triggers = run.guardrail_triggers ?? [];
  const sourceErrs = run.source_errors ?? [];

  // The inline source-error suffix is appended at the end for SUCCESS / PARTIAL
  // / unknown — for ERROR it's already woven into the base sentence below.
  const sourceSuffix =
    sourceErrs.length > 0 ? ` · ${sourceErrorShort(sourceErrs[0] ?? "")}` : "";

  if (status === "SUCCESS" || status === "PARTIAL") {
    let base: string;
    if (found === undefined || found === 0) {
      base = "Nothing came back from MarketCheck";
    } else if (above === undefined || above === 0) {
      base = `${plural(found, "listing")} scored, nothing matched`;
    } else {
      const parts = [`${plural(found, "listing")} scored`, `${String(above)} in view`];
      if (drafts !== undefined && drafts > 0) {
        parts.push(`${plural(drafts, "email")} drafted`);
      }
      base = parts.join(", ");
    }
    return (status === "PARTIAL" ? `Partial — ${base}` : base) + sourceSuffix;
  }

  if (status === "GUARDRAIL_BLOCKED") {
    if (triggers.length === 0) return `Stopped by a safety check${sourceSuffix}`;
    if (triggers.length === 1) {
      return `Stopped at ${humanizeTrigger(triggers[0] ?? "")}${sourceSuffix}`;
    }
    if (triggers.length <= 3) {
      return `Stopped at ${triggers.map(humanizeTrigger).join(", ")}${sourceSuffix}`;
    }
    return `Stopped at ${humanizeTrigger(triggers[0] ?? "")} and ${String(triggers.length - 1)} more${sourceSuffix}`;
  }

  if (status === "RUNNING" || status === "IN_PROGRESS") {
    return "Running…";
  }

  if (status === "ERROR" || status === "FAILED") {
    const detail = sourceErrs.length > 0 ? ` — ${sourceErrorShort(sourceErrs[0] ?? "")}` : "";
    return `Something went wrong${detail}`;
  }

  return null;
}

/**
 * Human duration between `started_at` and `finished_at`. Returns `null` when
 * either is missing or the diff is non-positive. Examples: `"45s"`,
 * `"2m 11s"`, `"1h 3m"`.
 */
export function runDurationLabel(run: RunSummary): string | null {
  if (!run.started_at || !run.finished_at) return null;
  const start = new Date(run.started_at).getTime();
  const end = new Date(run.finished_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  const totalSeconds = Math.max(0, Math.round((end - start) / 1000));
  if (totalSeconds <= 0) return null;

  if (totalSeconds < 60) return `${String(totalSeconds)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return seconds === 0 ? `${String(minutes)}m` : `${String(minutes)}m ${String(seconds)}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin === 0 ? `${String(hours)}h` : `${String(hours)}h ${String(remMin)}m`;
}
