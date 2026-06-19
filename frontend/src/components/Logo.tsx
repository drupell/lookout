import { cn } from "@/components/ui";

interface LogomarkProps {
  /** Pixel size of the square glyph. Tuned to read crisp at 20–28px. */
  size?: number;
  className?: string;
}

/**
 * Lookout logomark — a "scope on the horizon".
 *
 * A clean circular lens framing a rising sun over a horizon line: the watchful
 * eye of the agent scanning for deals on the edge of the day. Drawn entirely
 * in `currentColor` so it inherits the honey-amber brand thread (the only
 * place we lean on the accent in the chrome). Pixel-snapped strokes keep it
 * sharp down to ~20px. Decorative — the wordmark carries the accessible name.
 */
export function Logomark({ size = 24, className }: LogomarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={cn("text-brand", className)}
    >
      {/* Lens / scope ring */}
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.75" />
      {/* Horizon line across the lens */}
      <path
        d="M4.6 14.4 H19.4"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
      {/* Rising sun: a half-disc resting on the horizon */}
      <path
        d="M8.4 14.4 a3.6 3.6 0 0 1 7.2 0"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* A single sun ray — a small mark of intent, not a busy starburst */}
      <path
        d="M12 5.4 V7.4"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

interface LogoProps {
  /** Hide the wordmark (e.g. a collapsed rail). Defaults to showing it. */
  wordmark?: boolean;
  className?: string;
}

/**
 * Full lockup: logomark + refined "Lookout" wordmark. The wordmark is set in
 * the display serif (Spectral) with a touch of letter-spacing for an elegant,
 * editorial feel — distinct from a workspace chip.
 */
export function Logo({ wordmark = true, className }: LogoProps) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <Logomark size={24} />
      {wordmark ? (
        <span className="font-serif text-[1.0625rem] font-medium leading-none tracking-[0.01em] text-fg dark:text-stone-100">
          Lookout
        </span>
      ) : null}
    </span>
  );
}
