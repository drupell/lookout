/**
 * Pure helpers for the stale-settings banner — split out so they can be
 * tested without DOM/React. The component itself lives in
 * `StaleSettingsBanner.tsx`.
 */

const STORAGE_KEY = "lookout.dismissedStaleSettings";

/**
 * Returns true when the user's settings were updated *after* their last run,
 * i.e. the deals on screen were scored against older preferences.
 *
 * Returns false when:
 *   - the user has never run (last_run_at is null/missing)
 *   - settings haven't changed since the last run
 *   - either timestamp is unparseable (safer to not nag)
 */
export function isStale(updatedAt?: string | null, lastRunAt?: string | null): boolean {
  if (!updatedAt || !lastRunAt) return false;
  const u = Date.parse(updatedAt);
  const r = Date.parse(lastRunAt);
  if (Number.isNaN(u) || Number.isNaN(r)) return false;
  return u > r;
}

/**
 * Dismissals are keyed by (user_id, updated_at) so a fresh prefs change
 * re-arms the banner — once you change settings again, you'll see it again.
 */
function dismissalKey(userId: string, updatedAt: string): string {
  return `${userId}|${updatedAt}`;
}

export function isDismissed(userId: string, updatedAt: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return false;
    const set = new Set<string>(JSON.parse(raw) as string[]);
    return set.has(dismissalKey(userId, updatedAt));
  } catch {
    return false;
  }
}

export function dismiss(userId: string, updatedAt: string): void {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const arr = raw ? (JSON.parse(raw) as string[]) : [];
    const set = new Set(arr);
    set.add(dismissalKey(userId, updatedAt));
    // Cap at 50 entries so this doesn't grow forever; oldest first.
    const next = Array.from(set).slice(-50);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // localStorage might be disabled (private mode); fail silently.
  }
}
