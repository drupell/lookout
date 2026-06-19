/**
 * Tiny class-name composition helper. Just `clsx` re-exported under a more
 * memorable name; using a wrapper means we can swap to `tailwind-merge` later
 * (resolves conflicting Tailwind classes) without touching every component.
 */
import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
