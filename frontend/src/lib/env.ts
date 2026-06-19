/**
 * Build-time env vars exposed to the browser.
 *
 * Next.js inlines `NEXT_PUBLIC_*` at build time. We validate them once here so a
 * misconfigured deployment fails fast instead of throwing inside the OIDC client
 * or the API. All values are required; an empty string is treated as missing.
 */
import { z } from "zod";

const schema = z.object({
  NEXT_PUBLIC_COGNITO_USER_POOL_ID: z.string().min(1, "Cognito User Pool ID is required"),
  NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID: z.string().min(1, "Cognito Client ID is required"),
  NEXT_PUBLIC_COGNITO_REGION: z.string().min(1, "Cognito region is required"),
  // Hosted UI domain, e.g. lookout-dev-auth.auth.us-east-1.amazoncognito.com
  NEXT_PUBLIC_COGNITO_DOMAIN: z.string().min(1, "Cognito Hosted UI domain is required"),
  NEXT_PUBLIC_API_BASE_URL: z.string().url("API base URL must be a valid URL"),
});

const raw = {
  NEXT_PUBLIC_COGNITO_USER_POOL_ID: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID,
  NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID,
  NEXT_PUBLIC_COGNITO_REGION: process.env.NEXT_PUBLIC_COGNITO_REGION,
  NEXT_PUBLIC_COGNITO_DOMAIN: process.env.NEXT_PUBLIC_COGNITO_DOMAIN,
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
};

const parsed = schema.safeParse(raw);

if (!parsed.success) {
  // Surface the first missing var clearly. We don't throw on the server-side
  // build pass when the placeholders are absent — only in the browser, where
  // the user actually needs them.
  if (typeof window !== "undefined") {
    const issues = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
    throw new Error(`Invalid environment configuration — ${issues}`);
  }
}

export const env = (parsed.success ? parsed.data : raw) as z.infer<typeof schema>;
