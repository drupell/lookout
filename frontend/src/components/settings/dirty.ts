/**
 * Convert react-hook-form's `dirtyFields` tree into a partial-overrides patch.
 *
 * RHF tracks dirtiness per-leaf. For arrays of primitives (e.g. string[]) it
 * marks each element, so we treat any dirty element as "send the full new
 * array" — the backend deep-merges, so partial array updates don't make sense
 * anyway. Empty branches are pruned so we PUT a minimal payload.
 */

type Dirty = boolean | DirtyObject | DirtyArray;
interface DirtyObject {
  [k: string]: Dirty | undefined;
}
type DirtyArray = readonly (Dirty | undefined)[];

function isDirtyAny(v: Dirty | undefined): boolean {
  if (v === true) return true;
  if (v === undefined || v === false) return false;
  if (Array.isArray(v)) return v.some(isDirtyAny);
  return Object.values(v).some(isDirtyAny);
}

/**
 * Walk `dirty` in lockstep with `values` and return a structural subset of
 * `values` containing only the dirty leaves. Returns `undefined` if no leaf
 * was dirty (caller can short-circuit "nothing to save").
 */
export function pickDirty<T>(values: T, dirty: Dirty | undefined): Partial<T> | undefined {
  if (dirty === undefined || dirty === false) return undefined;
  if (dirty === true) return values;

  if (Array.isArray(dirty)) {
    return dirty.some(isDirtyAny) ? values : undefined;
  }

  const dirtyObj = dirty as DirtyObject;
  const out: Record<string, unknown> = {};
  const valuesObj = (values ?? {}) as Record<string, unknown>;
  for (const key of Object.keys(dirtyObj)) {
    const sub = pickDirty(valuesObj[key], dirtyObj[key]);
    if (sub !== undefined) out[key] = sub;
  }
  return Object.keys(out).length > 0 ? (out as Partial<T>) : undefined;
}

/** Parse a comma-separated string into a trimmed, blank-stripped array. */
export function parseList(input: string): string[] {
  return input
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}
