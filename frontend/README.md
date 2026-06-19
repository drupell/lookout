# Lookout Dashboard (Next.js)

Multi-tenant dashboard for the Lookout EV deal monitor. Static export → S3 + CloudFront.

## Stack

| Concern   | Tool                                                              | Why                                                |
| --------- | ----------------------------------------------------------------- | -------------------------------------------------- |
| Framework | Next.js 15 (App Router) with `output: 'export'`                   | Static SPA → S3 + CloudFront                       |
| Language  | TypeScript with `strict` + `noUncheckedIndexedAccess`             | Type-safe, mirrors mypy strict on backend          |
| Linter    | ESLint with `next/core-web-vitals` + `@typescript-eslint/strict`  | Opinionated, catches real bugs                     |
| Formatter | Prettier + `prettier-plugin-tailwindcss`                          | Single-source formatting                           |
| Auth      | `aws-amplify` v6 (modular) + Cognito User Pool                    | Production-grade JWT flow                          |
| HTTP      | Native `fetch` + small typed wrapper with zod response validation | No axios bloat; backend regressions surface loudly |
| Forms     | `react-hook-form` + `zod` schemas                                 | Type-safe validation that mirrors Pydantic         |
| Styling   | Tailwind CSS                                                      | Industry standard, no custom CSS                   |
| Tests     | Vitest + Testing Library + jsdom                                  | Fast Jest replacement                              |

## Layout

```
src/
  app/
    layout.tsx          root html shell + globals.css
    page.tsx            redirects → /sign-in or /deals based on auth
    sign-in/page.tsx    Cognito email/password form
    deals/page.tsx      authenticated deals list
    settings/page.tsx   prefs view + manual run trigger
  components/
    AuthGate.tsx        client wrapper that redirects unauthenticated users
    NavBar.tsx          top nav + sign-out
  lib/
    env.ts              zod-validated NEXT_PUBLIC_* env vars
    auth.ts             Amplify configure + signIn/signOut/getIdToken wrapper
    api.ts              typed API client (fetch + JWT + zod-validated responses)
    schemas.ts          zod schemas mirroring src/config/loader.py + API responses
    schemas.test.ts     smoke tests for the schema mirror
```

## Local dev

Copy `.env.example` → `.env.local` and fill from CDK stack outputs:

```bash
aws cloudformation describe-stacks --stack-name LookoutDevAuth \
  --query "Stacks[0].Outputs"
aws cloudformation describe-stacks --stack-name LookoutDevApi \
  --query "Stacks[0].Outputs"
```

Then:

```bash
cd frontend
npm install
npm run dev          # local dev server (http://localhost:3000)
npm run lint         # ESLint
npm run format       # Prettier write
npm run typecheck    # tsc --noEmit
npm run test         # Vitest
npm run check        # lint + typecheck + test
npm run build        # static export → frontend/out/
```

The root `Makefile` exposes the same as `make frontend-*` targets.

## Deploy (Phase 1E)

`out/` is uploaded to the dashboard S3 bucket (created by a future `LookoutDevDashboard`
stack) and served via CloudFront. Until that stack lands, run `npm run dev` locally.
