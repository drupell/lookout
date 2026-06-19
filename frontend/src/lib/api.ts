/**
 * Typed API client for the Lookout backend.
 *
 * Every call:
 *   1. fetches the user's Cognito JWT and adds it as a Bearer token,
 *   2. throws ApiError on non-2xx (with status + parsed body when JSON),
 *   3. validates the response with the matching zod schema before returning.
 *
 * Schema validation is deliberate: a backend regression that drops a field
 * surfaces as a parse error in the UI, not as a silently-empty render.
 */
import { ZodError, type z } from "zod";

import { getIdToken } from "./auth";
import { env } from "./env";
import { isPreview, previewFixture } from "./preview";
import {
  ActDealResponseSchema,
  AddFavoriteResponseSchema,
  ArbitrageResponseSchema,
  ByokKeyResponseSchema,
  DealsResponseSchema,
  FavoritesResponseSchema,
  InFlightRunSchema,
  IncentiveStackResponseSchema,
  InventoryAnomalyResponseSchema,
  MacroSnapshotSchema,
  MarketSignalResponseSchema,
  MeResponseSchema,
  PrefsResponseSchema,
  RemoveFavoriteResponseSchema,
  RunsResponseSchema,
  TriggerRunResponseSchema,
  UsageSchema,
  type ArbitrageResponse,
  type ByokKeyResponse,
  type Deal,
  type Favorite,
  type InFlightRun,
  type IncentiveStackResponse,
  type InventoryAnomalyResponse,
  type MacroSnapshot,
  type MarketSignalResponse,
  type MeResponse,
  type Preferences,
  type PrefsResponse,
  type RunSummary,
  type TriggerRunResponse,
  type UsageResponse,
} from "./schemas";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /**
   * Human-readable single-line error including the backend's own `detail` /
   * `error` field when present. Use this in UI surfaces — the bare `.message`
   * is just the HTTP status line (e.g. "500 Internal Server Error") which is
   * usually less useful than what the API Lambda actually said.
   */
  toUserMessage(): string {
    const body = this.body;
    if (body && typeof body === "object") {
      const obj = body as { detail?: unknown; error?: unknown };
      if (typeof obj.detail === "string" && obj.detail.length > 0) {
        return `${String(this.status)}: ${obj.detail}`;
      }
      if (typeof obj.error === "string" && obj.error.length > 0) {
        return `${String(this.status)}: ${obj.error}`;
      }
    }
    return `${String(this.status)}: ${this.message}`;
  }
}

/**
 * Global 401 handling, decoupled from any router.
 *
 * The api layer must not import Next's router (it's used in non-React contexts
 * and would couple the data layer to the view). Instead a top-level client
 * component registers a handler here; on any 401 we invoke it so the app can
 * show an "expired session" toast and redirect to sign-in. We guard against
 * re-entrancy so a burst of parallel 401s fires the handler once.
 */
type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;
let handlingUnauthorized = false;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function notifyUnauthorized(): void {
  if (handlingUnauthorized || !unauthorizedHandler) return;
  handlingUnauthorized = true;
  try {
    unauthorizedHandler();
  } finally {
    // Re-arm shortly after so a later genuine expiry can fire again, but a
    // single page's parallel calls collapse to one notification.
    setTimeout(() => {
      handlingUnauthorized = false;
    }, 1000);
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | undefined>;
}

async function request<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  opts: RequestOptions = {},
): Promise<z.output<S>> {
  // Preview mode: serve fixtures (validated against the same schema) instead of
  // hitting a backend. Dev-only; the flag is never set in a real build.
  if (isPreview) {
    return schema.parse(previewFixture(path, opts.method ?? "GET")) as z.output<S>;
  }

  const token = await getIdToken();
  if (!token) {
    notifyUnauthorized();
    throw new ApiError(401, "Not signed in");
  }

  const url = new URL(path.replace(/^\//, ""), `${env.NEXT_PUBLIC_API_BASE_URL}/`);
  if (opts.query) {
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }

  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    ...(opts.body !== undefined ? { body: JSON.stringify(opts.body) } : {}),
  };

  const res = await fetch(url.toString(), init);
  const text = await res.text();
  let parsed: unknown;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }

  if (!res.ok) {
    // A 401 means the token is missing/expired/rejected — route it through the
    // global handler for a clean re-auth path. A 403 is different: the user IS
    // authenticated but simply not allowed (tier limit, or another user's
    // resource) — let the caller surface targeted guidance instead of bouncing
    // them to sign-in.
    if (res.status === 401) notifyUnauthorized();
    throw new ApiError(res.status, `${String(res.status)} ${res.statusText}`, parsed);
  }

  // Wrap zod failures so callers see "the API returned a shape we don't
  // understand" instead of the generic "Failed to ..." fallback. This is
  // almost always a backend/frontend schema drift bug worth surfacing.
  try {
    return schema.parse(parsed) as z.output<S>;
  } catch (err) {
    if (err instanceof ZodError) {
      const issue = err.issues[0];
      const where = issue?.path.join(".") ?? "(root)";
      const what = issue?.message ?? "schema mismatch";
      throw new ApiError(res.status, `Response shape mismatch at ${where}: ${what}`, parsed);
    }
    throw err;
  }
}

// --- typed endpoint wrappers ---

export const api = {
  getMe: (): Promise<MeResponse> => request("/me", MeResponseSchema),

  getPrefs: (): Promise<PrefsResponse> => request("/me/prefs", PrefsResponseSchema),

  putPrefs: (overrides: Partial<Preferences>): Promise<PrefsResponse> =>
    request("/me/prefs", PrefsResponseSchema, { method: "PUT", body: overrides }),

  listRuns: async (): Promise<RunSummary[]> => {
    const res = await request("/me/runs", RunsResponseSchema);
    return res.runs;
  },

  triggerRun: (): Promise<TriggerRunResponse> =>
    request("/me/runs", TriggerRunResponseSchema, { method: "POST" }),

  getInFlightRun: (): Promise<InFlightRun> => request("/me/runs/in_flight", InFlightRunSchema),

  listDeals: async (
    query: { min_score?: number; status?: string; limit?: number } = {},
  ): Promise<Deal[]> => {
    const res = await request("/me/deals", DealsResponseSchema, { query });
    return res.deals;
  },

  actOnDeal: (listingId: string) =>
    request(`/me/deals/${encodeURIComponent(listingId)}/act`, ActDealResponseSchema, {
      method: "POST",
    }),

  listFavorites: async (): Promise<Favorite[]> => {
    const res = await request("/me/favorites", FavoritesResponseSchema);
    return res.favorites;
  },

  addFavorite: (listingId: string) =>
    request(`/me/deals/${encodeURIComponent(listingId)}/favorite`, AddFavoriteResponseSchema, {
      method: "POST",
    }),

  removeFavorite: (listingId: string) =>
    request(`/me/deals/${encodeURIComponent(listingId)}/favorite`, RemoveFavoriteResponseSchema, {
      method: "DELETE",
    }),

  putByokKey: (apiKey: string): Promise<ByokKeyResponse> =>
    request("/me/byok-key", ByokKeyResponseSchema, {
      method: "PUT",
      body: { api_key: apiKey },
    }),

  deleteByokKey: (): Promise<ByokKeyResponse> =>
    request("/me/byok-key", ByokKeyResponseSchema, { method: "DELETE" }),

  getMarketSignal: (
    {
      window = "90d",
      view = "personalized",
    }: { window?: string; view?: "personalized" | "market" } = {},
  ): Promise<MarketSignalResponse> =>
    request("/me/signal", MarketSignalResponseSchema, { query: { window, view } }),

  getUsage: (): Promise<UsageResponse> => request("/me/usage", UsageSchema),

  getMacroSnapshot: (): Promise<MacroSnapshot> =>
    request("/me/macro", MacroSnapshotSchema),

  getIncentiveStack: (): Promise<IncentiveStackResponse> =>
    request("/me/incentives", IncentiveStackResponseSchema),

  getUsedVsNewArbitrage: (): Promise<ArbitrageResponse> =>
    request("/me/used-vs-new-arbitrage", ArbitrageResponseSchema),

  getInventoryAnomaly: (): Promise<InventoryAnomalyResponse> =>
    request("/me/inventory-anomaly", InventoryAnomalyResponseSchema),
};
