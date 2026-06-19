# Lookout branding assets

Static logomark exports for places that need a standalone file (Cognito Managed Login, app stores, social cards, etc.). The in-app React component lives at [frontend/src/components/Logo.tsx](../../frontend/src/components/Logo.tsx) and is the source of truth for the glyph — these SVGs mirror it with baked colors.

| File | Use | Color |
|---|---|---|
| `logomark-light.svg` | Logo on warm ivory / white backgrounds (Cognito light mode) | `#B06E1A` honey-amber |
| `logomark-dark.svg` | Logo on warm near-black backgrounds (Cognito dark mode) | `#D68E2B` lighter honey-amber |
| `favicon.svg` | Browser favicon / Cognito favicon slot | amber on ivory tile |

## Cognito Managed Login upload

In the customize-style panel, upload `logomark-light.svg` to the **light-mode logo** slot and `logomark-dark.svg` to the **dark-mode logo** slot. `favicon.svg` goes in the favicon slot if available.

When you export the final settings JSON, drop it at `infrastructure/cognito_branding.json` — `auth_stack.py` wires it into `CfnManagedLoginBranding`.
