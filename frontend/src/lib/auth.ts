/**
 * Cognito Hosted UI auth via OIDC Authorization Code + PKCE.
 *
 * Flow:
 *   signIn()  — redirects browser to Cognito's hosted login page
 *   Cognito   — authenticates the user, redirects back to /auth/callback?code=XXX
 *   callback  — handleCallback() exchanges code for ID + access tokens
 *   signOut() — clears local tokens + redirects to Cognito's /logout
 *
 * We rely on Cognito's OIDC discovery doc (at the issuer URL) for the
 * `authorize` and `token` endpoints. Cognito does *not* publish
 * `end_session_endpoint` in discovery, so logout is constructed manually.
 *
 * The OIDC library is intentionally the only auth dependency — replaces a 750-pkg
 * `aws-amplify` install with a single ~30 KB module and zero transitive vulns.
 */
import { UserManager, WebStorageStateStore } from "oidc-client-ts";

import { env } from "./env";
import { isPreview, PREVIEW_USER } from "./preview";

let manager: UserManager | null = null;

function userManager(): UserManager {
  if (manager) return manager;
  if (typeof window === "undefined") {
    throw new Error("Auth requires a browser environment");
  }

  const origin = window.location.origin;
  manager = new UserManager({
    authority: `https://cognito-idp.${env.NEXT_PUBLIC_COGNITO_REGION}.amazonaws.com/${env.NEXT_PUBLIC_COGNITO_USER_POOL_ID}`,
    client_id: env.NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID,
    redirect_uri: `${origin}/auth/callback/`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code",
    scope: "openid email profile",
    // Persist across reloads. Cognito's refresh tokens last 30 days.
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    // Try silent renew before expiry so the API doesn't 401 mid-session.
    automaticSilentRenew: true,
  });
  return manager;
}

export interface CurrentUser {
  userId: string;
  email: string;
}

export async function signIn(): Promise<void> {
  await userManager().signinRedirect();
}

export async function signOut(): Promise<void> {
  // Preview mode has no real session — just bounce home.
  if (isPreview) {
    window.location.assign("/");
    return;
  }
  // Clear local session first so a flaky network doesn't leave us in a half-state.
  await userManager().removeUser();
  // Cognito's /logout takes client_id + logout_uri (must match a registered URL).
  const url = new URL(`https://${env.NEXT_PUBLIC_COGNITO_DOMAIN}/logout`);
  url.searchParams.set("client_id", env.NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID);
  url.searchParams.set("logout_uri", `${window.location.origin}/`);
  window.location.assign(url.toString());
}

export async function getUser(): Promise<CurrentUser | null> {
  if (isPreview) return PREVIEW_USER;
  const u = await userManager().getUser();
  if (!u || u.expired) return null;
  const sub = u.profile.sub;
  const email = u.profile.email;
  return {
    userId: typeof sub === "string" ? sub : "",
    email: typeof email === "string" ? email : "",
  };
}

export async function getIdToken(): Promise<string | null> {
  if (isPreview) return "preview-token";
  const u = await userManager().getUser();
  if (!u || u.expired) return null;
  return u.id_token ?? null;
}

/**
 * Called from the /auth/callback route: completes the code → token exchange.
 * Throws on bad/expired/missing code; the route should catch and redirect.
 */
export async function handleCallback(): Promise<void> {
  await userManager().signinCallback();
}
