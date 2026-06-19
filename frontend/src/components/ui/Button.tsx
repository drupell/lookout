import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  /** Shows a spinner and disables the button while an action is in flight. */
  loading?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  // Primary is warm ink on paper — espresso fill, cream text — so the main
  // action reads confident without competing with the warm canvas. In dark
  // mode it inverts to paper-on-ink. Amber stays a jewel accent, not a slab.
  primary:
    "bg-ink text-ink-fg shadow-sm hover:bg-ink-hover hover:shadow-md dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white",
  secondary:
    "bg-white text-stone-900 border border-stone-200 hover:bg-stone-50 hover:shadow-sm dark:bg-stone-900 dark:text-stone-100 dark:border-stone-800 dark:hover:bg-stone-800",
  ghost: "text-stone-700 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800",
  danger:
    "bg-red-600 text-white shadow-sm hover:bg-red-700 hover:shadow-md dark:bg-red-500 dark:hover:bg-red-600",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
};

export function Button({
  variant = "primary",
  size = "md",
  leadingIcon,
  trailingIcon,
  loading = false,
  className,
  children,
  type = "button",
  disabled,
  ...props
}: ButtonProps) {
  const isDisabled = disabled ?? loading;
  return (
    <button
      type={type}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className={cn(
        "focus-ring inline-flex items-center justify-center rounded-md font-semibold",
        // Motion: a gentle lift on hover, a tactile press on active.
        "transition-[transform,box-shadow,background-color,color] duration-fast ease-editorial",
        "hover:-translate-y-px active:translate-y-0 active:scale-[0.98]",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 disabled:hover:shadow-none disabled:active:scale-100",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
      ) : leadingIcon ? (
        <span className="shrink-0">{leadingIcon}</span>
      ) : null}
      {children}
      {!loading && trailingIcon ? <span className="shrink-0">{trailingIcon}</span> : null}
    </button>
  );
}
