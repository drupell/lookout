import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

/**
 * Lookout design system — "sunlit paper".
 *
 * Surfaces are warm ivory/cream, not cool gray; the brand thread is a rich
 * honey-amber used deliberately (primary CTAs, focus ring, key links, the
 * logomark), with a deeper terracotta reserved for emphasis. Status colors
 * (success, danger, warning) follow Tailwind's standard scales for
 * predictability. Contrast is tuned to hold WCAG AA in both light and dark.
 *
 * All other components consume the semantic aliases (`surface`, `border`,
 * `fg`, `brand`, `accent`, `success`, ...) so warming the palette stays a
 * one-file change.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  // Class-based dark mode so the user can pin a theme (auto / light / dark)
  // via the sidebar toggle. The ThemeProvider stamps `.dark` on `<html>` based
  // on the persisted preference (+ system pref when "auto").
  darkMode: ["class"],
  theme: {
    extend: {
      colors: {
        // Warm ivory/cream surface scale — sunlit paper, not cool gray.
        surface: {
          DEFAULT: "rgb(251 247 239)", // #FBF7EF warm ivory
          subtle: "rgb(245 239 228)", // #F5EFE4 cream
          muted: "rgb(235 227 213)", // #EBE3D5 deeper cream
          elevated: "rgb(255 253 250)", // #FFFDFA a hair warm vs pure white
          dim: "rgb(28 23 20)", // #1C1714 warm near-black (dark surface)
          deep: "rgb(19 16 13)", // #13100D warm deepest
        },
        border: {
          DEFAULT: "rgb(232 224 210)", // #E8E0D2 warm hairline
          strong: "rgb(217 207 189)", // #D9CFBD warm strong
          dim: "rgb(51 41 31)", // #33291F warm dark border
        },
        fg: {
          DEFAULT: "rgb(28 23 20)", // #1C1714 warm near-black
          muted: "rgb(107 96 82)", // #6B6052 warm muted
          subtle: "rgb(138 126 110)", // #8A7E6E warm subtle
          inverse: "rgb(251 247 239)", // ivory text on dark
        },
        brand: {
          DEFAULT: "rgb(176 110 26)", // #B06E1A rich honey-amber (AA on cream/white)
          fg: "rgb(255 252 246)", // text on brand bg
          subtle: "rgb(251 239 214)", // #FBEFD6 honey wash
          ring: "rgb(214 142 43)", // #D68E2B focus ring
        },
        // Deeper terracotta — reserved for emphasis, not everyday actions.
        accent: {
          DEFAULT: "rgb(194 89 46)", // #C2592E terracotta
          subtle: "rgb(248 226 213)", // #F8E2D5 terracotta wash
          fg: "rgb(120 45 22)", // deep terracotta text
        },
        // Warm espresso — the primary CTA surface. Amber stays a jewel accent;
        // the main action is warm ink on paper (inverts to paper-on-ink in dark).
        ink: {
          DEFAULT: "rgb(43 32 24)", // #2B2018 warm espresso
          fg: "rgb(251 247 239)", // ivory text on ink
          hover: "rgb(58 44 32)", // #3A2C20 lifted espresso
        },
        success: {
          DEFAULT: "rgb(5 150 105)", // emerald-600
          subtle: "rgb(209 250 229)", // emerald-100
          fg: "rgb(6 78 59)", // emerald-900
        },
        warning: {
          DEFAULT: "rgb(194 110 18)", // honey-amber-aligned warning
          subtle: "rgb(251 239 214)", // honey wash
          fg: "rgb(120 60 15)", // deep amber text
        },
        danger: {
          DEFAULT: "rgb(220 38 38)", // red-600
          subtle: "rgb(254 226 226)", // red-100
          fg: "rgb(127 29 29)", // red-900
        },
        // Components reach for `stone-*` and `amber-*` directly in places. We
        // re-tint both scales toward the sunlit-paper palette so warmth is
        // cohesive everywhere without touching every className. Numbers track
        // Tailwind's stops; only the hue/temperature shifts (cooler grays →
        // warm ivory; orange amber → honey-amber).
        stone: {
          50: "rgb(251 247 239)", // warm ivory
          100: "rgb(245 239 228)", // cream
          200: "rgb(232 224 210)", // warm hairline
          300: "rgb(217 207 189)",
          400: "rgb(160 147 130)",
          500: "rgb(138 126 110)",
          600: "rgb(107 96 82)",
          700: "rgb(74 65 54)",
          800: "rgb(51 41 31)",
          900: "rgb(28 23 20)", // warm near-black
          950: "rgb(19 16 13)", // warm deepest
        },
        amber: {
          50: "rgb(252 245 232)",
          100: "rgb(251 239 214)", // honey wash
          200: "rgb(245 222 178)",
          300: "rgb(232 196 130)",
          400: "rgb(214 156 70)", // lighter honey for dark-mode text
          500: "rgb(214 142 43)", // honey (focus ring)
          600: "rgb(194 110 18)",
          700: "rgb(176 110 26)", // rich honey-amber (brand)
          800: "rgb(146 84 20)",
          900: "rgb(120 60 15)",
          950: "rgb(74 38 10)",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        // Spectral — a warm, calm serif for display moments: page/section
        // titles and big numerals (stat values, scores, prices). Never body.
        serif: ["var(--font-serif)", "ui-serif", "Georgia", "serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        // Tightened scale with consistent line-height ratios.
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
        xs: ["0.75rem", { lineHeight: "1rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.9375rem", { lineHeight: "1.5rem" }],
        lg: ["1.0625rem", { lineHeight: "1.625rem" }],
        xl: ["1.25rem", { lineHeight: "1.75rem" }],
        "2xl": ["1.5rem", { lineHeight: "2rem" }],
        "3xl": ["1.875rem", { lineHeight: "2.25rem" }],
        "4xl": ["2.25rem", { lineHeight: "2.5rem" }],
      },
      borderRadius: {
        // Stripe-like medium-radius defaults; nothing super-rounded.
        sm: "0.25rem",
        DEFAULT: "0.375rem",
        md: "0.5rem",
        lg: "0.625rem",
        xl: "0.75rem",
        "2xl": "1rem",
      },
      boxShadow: {
        // Softer, layered "paper depth" — warm-tinted shadows (a touch of the
        // ivory's umber, not flat black) stacked in two/three layers so cards
        // sit on the page rather than float. Blurs stay restrained to read
        // crisp on cream.
        xs: "0 1px 2px 0 rgb(40 28 16 / 0.05)",
        sm: "0 1px 2px -1px rgb(40 28 16 / 0.06), 0 2px 6px -2px rgb(40 28 16 / 0.05)",
        DEFAULT:
          "0 1px 2px -1px rgb(40 28 16 / 0.06), 0 4px 10px -3px rgb(40 28 16 / 0.07), 0 8px 20px -8px rgb(40 28 16 / 0.05)",
        md: "0 2px 4px -2px rgb(40 28 16 / 0.07), 0 8px 16px -6px rgb(40 28 16 / 0.08), 0 16px 32px -12px rgb(40 28 16 / 0.06)",
        lg: "0 4px 8px -4px rgb(40 28 16 / 0.08), 0 16px 28px -10px rgb(40 28 16 / 0.10), 0 28px 48px -16px rgb(40 28 16 / 0.07)",
      },
      // --- Motion system ---
      // A small, deliberate vocabulary: three speeds and one expressive ease.
      // Everything in the UI should reach for one of these so motion feels
      // like one hand drew it.
      transitionDuration: {
        DEFAULT: "150ms",
        fast: "150ms",
        base: "250ms",
        slow: "400ms",
      },
      transitionTimingFunction: {
        // A gentle overshoot-free "settle" — calm, editorial, never springy.
        editorial: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      keyframes: {
        // Page/content entrance: rise a few px while fading in.
        "fade-in-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        // The favorite heart's little moment of joy.
        "heart-pop": {
          "0%": { transform: "scale(1)" },
          "40%": { transform: "scale(1.3)" },
          "70%": { transform: "scale(0.92)" },
          "100%": { transform: "scale(1)" },
        },
        // Loading shimmer for skeletons.
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-in-up": "fade-in-up 400ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "heart-pop": "heart-pop 350ms cubic-bezier(0.22, 1, 0.36, 1)",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [animate],
};

export default config;
