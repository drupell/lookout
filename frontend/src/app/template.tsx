import type { ReactNode } from "react";

/**
 * Per-route page transition.
 *
 * Templates re-mount on every navigation (unlike layouts, which persist), so
 * wrapping `{children}` here gives every route a subtle entrance without any
 * client-side router plumbing or third-party motion library.
 *
 * Motion vocabulary: reuses the existing `fade-in-up` keyframe (8px rise +
 * opacity) from tailwind.config.ts — same shape language as the per-page
 * entrance — and shortens its duration to 250ms so it reads as a route-change
 * "whisper" rather than the 400ms entrance flourish. The keyframe's editorial
 * easing curve (`cubic-bezier(0.22, 1, 0.36, 1)`) is baked into the animation
 * shorthand, so we only need to override the duration via inline style.
 *
 * This composes cleanly with everything beneath it:
 *   • Per-page `animate-fade-in-up` wrappers fire *later*, when their data
 *     resolves and the skeleton swaps out — a different moment, not a
 *     double-animation of the same element.
 *   • Per-list `stagger-item` cascades layer naturally on top of either.
 *
 * Reduced motion is honoured automatically: the global `prefers-reduced-motion`
 * rule in globals.css clamps `animation-duration` to ~0ms, so users who opt
 * out see an instant route change with no transform.
 */
export default function Template({ children }: { children: ReactNode }) {
  return (
    <div className="animate-fade-in-up" style={{ animationDuration: "250ms" }}>
      {children}
    </div>
  );
}
