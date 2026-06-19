import { cn } from "@/components/ui";

/**
 * Lookout bespoke illustrations — the "scope on the horizon" vocabulary from
 * Logo.tsx, scaled up to editorial empty-state size.
 *
 * Drawing rules these share:
 *   • Drawn in `currentColor` so the parent can tint them with the same
 *     `text-stone-300 dark:text-stone-600` treatment as the lucide icons they
 *     replace. A single honey-amber accent (`text-brand`) is overlaid for the
 *     one mark of intent — never more than one accent per scene.
 *   • Pixel-snapped 1.5px strokes on a 120×96 viewBox. With our typical render
 *     size (~h-24) that lands around 1px on screen — crisp, never wiry.
 *   • Round line caps + joins, no fills except where a shape needs to read as
 *     a closed form. Restrained, editorial.
 *   • `aria-hidden` because the surrounding empty-state copy carries the
 *     accessible name. Decorative only.
 */

interface IllustrationProps {
  /** Tailwind sizing/positioning classes; default fits the empty-state slot. */
  className?: string;
}

/**
 * ScopeHorizon — for the "In view" empty states.
 *
 * A scope (the Logomark's circular lens) hovering over a long horizon line,
 * with three faint dots in the sky reading as listings "out there" waiting to
 * be found. The sun inside the lens picks up the honey-amber accent — the same
 * single mark of intent the logomark uses.
 */
export function ScopeHorizon({ className }: IllustrationProps) {
  return (
    <svg
      viewBox="0 0 120 96"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={cn("h-24 w-auto", className)}
    >
      {/* Faint "constellation" of listings out there — three dots, descending,
          to suggest results scattered across the sky. */}
      <g className="text-stone-300 dark:text-stone-700" stroke="currentColor">
        <circle cx="24" cy="22" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="38" cy="14" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="96" cy="20" r="1.1" fill="currentColor" stroke="none" />
      </g>

      {/* Horizon line — the long calm rule the scope is trained on. */}
      <path
        d="M6 70 H114"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />

      {/* Subtle ground notches — a touch of texture, not a busy landscape. */}
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.55">
        <path d="M16 76 L19 76" />
        <path d="M28 78 L33 78" />
        <path d="M92 76 L97 76" />
        <path d="M104 78 L108 78" />
      </g>

      {/* Scope / lens — the watchful eye. Sits on the horizon, just like the
          logomark, so the family resemblance is unmistakable. */}
      <circle
        cx="60"
        cy="52"
        r="22"
        stroke="currentColor"
        strokeWidth="1.5"
      />

      {/* Horizon passing through the lens — same idea as the Logomark. */}
      <path
        d="M40 60 H80"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />

      {/* Rising sun inside the lens — the one accent. Honey-amber, the brand
          thread the logomark also reaches for. */}
      <g className="text-brand">
        <path
          d="M48.5 60 a11.5 11.5 0 0 1 23 0"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* A single sun ray — a mark of intent, not a starburst. */}
        <path
          d="M60 33 V38"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </g>
    </svg>
  );
}

/**
 * SavedHorizon — for the /favorites empty state.
 *
 * A horizon with a small bookmark planted in the ground at center: the "kept"
 * gesture, distinct from ScopeHorizon's scanning posture. The bookmark's
 * notched tail is the editorial cue (a real bookmark, not a heart) and its
 * inner edge picks up the honey-amber accent. Reads as "saved for later"
 * without resorting to a literal heart icon, which the page already uses on
 * each card.
 */
export function SavedHorizon({ className }: IllustrationProps) {
  return (
    <svg
      viewBox="0 0 120 96"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={cn("h-24 w-auto", className)}
    >
      {/* A few faint dots in the sky — same family vocabulary as ScopeHorizon,
          but sparser: this scene is about what's already kept, not what's out
          there to find. */}
      <g className="text-stone-300 dark:text-stone-700">
        <circle cx="22" cy="20" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="100" cy="26" r="1.1" fill="currentColor" stroke="none" />
      </g>

      {/* Horizon line. */}
      <path
        d="M6 70 H114"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />

      {/* Ground notches, mirroring ScopeHorizon. */}
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.55">
        <path d="M16 76 L20 76" />
        <path d="M30 78 L36 78" />
        <path d="M86 78 L92 78" />
        <path d="M100 76 L106 76" />
      </g>

      {/* Bookmark planted at center. A tall rectangle with a notched bottom —
          the universal "saved" gesture, drawn with the same 1.5px stroke as the
          horizon so it feels native to the scene. The bookmark's body is the
          muted stroke; its inner left edge is the brand accent (the one mark
          of intent, like the sun in ScopeHorizon). */}
      <g stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round">
        {/* Body */}
        <path d="M52 32 H68 V66 L60 60 L52 66 Z" />
        {/* A single tick across the bookmark — a "tagged" mark, restrained. */}
        <path d="M56 44 H64" opacity="0.55" />
      </g>

      {/* Brand accent: the bookmark's inner-left edge, lifted in honey-amber.
          One quiet thread, just like the rising sun in ScopeHorizon. */}
      <path
        d="M52 32 V64"
        className="text-brand"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * BrokenScope — optional error illustration for /favorites' ErrorState.
 *
 * The same scope as ScopeHorizon, but the lens ring is broken at the top and a
 * small gap interrupts the horizon — a calm visual cue that something failed
 * upstream. No red, no urgency: this is the same warm muted treatment as the
 * other illustrations; the surrounding copy carries the actual error meaning.
 */
export function BrokenScope({ className }: IllustrationProps) {
  return (
    <svg
      viewBox="0 0 120 96"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={cn("h-24 w-auto", className)}
    >
      {/* Horizon, broken near the lens to echo the "connection interrupted"
          feeling without resorting to a red glyph. */}
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <path d="M6 70 H44" />
        <path d="M76 70 H114" />
      </g>

      {/* Lens — broken ring. Two arcs with a small gap at the top, like a
          fractured scope. */}
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none">
        <path d="M56 30 A22 22 0 0 0 38 52 A22 22 0 0 0 60 74" />
        <path d="M64 30 A22 22 0 0 1 82 52 A22 22 0 0 1 60 74" />
      </g>

      {/* A short interrupted horizon inside the lens — the scope's own view
          is unsettled. */}
      <path
        d="M44 60 H56"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M64 60 H76"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />

      {/* The one warm accent — a small honey-amber tick where the lens is
          broken, suggesting the agent is still watching. */}
      <path
        d="M59 30 L61 26"
        className="text-brand"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
