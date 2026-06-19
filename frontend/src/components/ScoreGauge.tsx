import { cn } from "@/components/ui";
import { scoreTone } from "@/lib/dealFormat";

interface ScoreGaugeProps {
  /** Overall score on [0, 1]. Undefined renders a graceful empty dial. */
  score: number | undefined;
  /** Diameter in px. Defaults to a compact 44px. */
  size?: number;
  className?: string;
}

/**
 * Compact circular score gauge — an SVG donut whose warm-accent arc is
 * proportional to a 0–1 score, with the value centered in the display serif.
 *
 * - Accessible: the SVG carries role="img" and an aria-label with the score.
 * - Degrades gracefully: an undefined score draws an empty track + "—".
 * - Reduced-motion friendly: the arc is a static stroke (no spin), the only
 *   transition is a brief stroke length tween that the global reduced-motion
 *   rule collapses to near-zero.
 *
 * The arc color tracks `scoreTone` so a strong deal reads emerald, a good one
 * honey-amber, and a weak one neutral — the same language as the rest of the
 * UI, just rendered as a dial.
 */
export function ScoreGauge({ score, size = 44, className }: ScoreGaugeProps) {
  const stroke = Math.max(3, Math.round(size * 0.09));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = score === undefined ? 0 : Math.min(1, Math.max(0, score));
  const dash = circumference * clamped;

  const tone = scoreTone(score);
  const arcClass =
    tone === "success"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "brand"
        ? "text-brand dark:text-amber-400"
        : "text-stone-400 dark:text-stone-500";

  const label = score === undefined ? "Score unavailable" : `Score ${score.toFixed(2)} out of 1`;

  return (
    <div
      role="img"
      aria-label={label}
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${String(size)} ${String(size)}`} aria-hidden>
        {/* Track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="text-stone-200 dark:text-stone-800"
          stroke="currentColor"
        />
        {/* Value arc — rotated so it starts at 12 o'clock and sweeps clockwise. */}
        {score !== undefined ? (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            strokeLinecap="round"
            stroke="currentColor"
            strokeDasharray={`${String(dash)} ${String(circumference)}`}
            transform={`rotate(-90 ${String(size / 2)} ${String(size / 2)})`}
            className={cn(arcClass, "transition-[stroke-dasharray] duration-slow ease-editorial")}
          />
        ) : null}
      </svg>
      <span
        className={cn(
          "absolute font-serif font-medium tabular-nums leading-none text-stone-900 dark:text-stone-100",
          size >= 44 ? "text-sm" : "text-xs",
        )}
      >
        {score === undefined ? "—" : score.toFixed(2)}
      </span>
    </div>
  );
}
