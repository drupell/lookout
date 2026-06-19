"use client";

import { Heart, LayoutDashboard, ListChecks, LogOut, Settings as SettingsIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { signOut, type CurrentUser } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: <LayoutDashboard className="h-4 w-4" /> },
  { href: "/deals", label: "In view", icon: <ListChecks className="h-4 w-4" /> },
  { href: "/favorites", label: "Favorites", icon: <Heart className="h-4 w-4" /> },
  { href: "/settings", label: "Settings", icon: <SettingsIcon className="h-4 w-4" /> },
];

interface AppShellProps {
  user: CurrentUser;
  children: ReactNode;
}

/**
 * Authenticated layout: sticky left sidebar with nav, top bar with user info,
 * main content area for the page. Fully responsive — sidebar collapses to a
 * top bar on small screens.
 */
export function AppShell({ user, children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await signOut();
    } catch {
      router.replace("/sign-in");
    }
  };

  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  const initial = (user.email.trim()[0] ?? "?").toUpperCase();

  return (
    <div className="min-h-screen md:grid md:grid-cols-[14rem_1fr]">
      {/* Sidebar */}
      <aside className="border-b border-border bg-surface-elevated px-4 py-3 md:sticky md:top-0 md:h-screen md:border-b-0 md:border-r md:px-4 md:py-6 dark:border-stone-800 dark:bg-stone-950">
        <div className="flex items-center justify-between md:block">
          <Link
            href="/dashboard"
            className="focus-ring -mx-1 flex items-center rounded-md px-1 py-1 md:py-1.5"
            aria-label="Lookout — go to dashboard"
          >
            <Logo />
          </Link>
          <nav className="flex items-center gap-0.5 md:mt-7 md:flex-col md:items-stretch md:gap-1">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`focus-ring group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors duration-fast ease-editorial ${
                    active
                      ? "bg-brand-subtle font-medium text-stone-900 dark:bg-amber-950/40 dark:text-stone-100"
                      : "text-stone-600 hover:bg-stone-100 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-800/60 dark:hover:text-stone-100"
                  }`}
                >
                  {/* Active rail: a slim honey-amber marker that reads as the
                      brand thread, only on the desktop vertical nav. */}
                  <span
                    aria-hidden
                    className={`absolute -left-4 top-1.5 bottom-1.5 hidden w-0.5 rounded-full bg-brand transition-opacity duration-fast md:block ${
                      active ? "opacity-100" : "opacity-0"
                    }`}
                  />
                  <span className={active ? "text-brand" : "text-stone-400 dark:text-stone-500"}>
                    {item.icon}
                  </span>
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Account row, pinned to the bottom on desktop. A hairline rule (not a
            boxed card) keeps it part of the page; a warm initial + quiet
            icon-only sign-out sit calmly in the editorial theme. The theme
            toggle lives above the rule so it reads as a utility, not a flair. */}
        <div className="hidden md:absolute md:bottom-5 md:left-4 md:right-4 md:block">
          <div className="mb-3 flex justify-end">
            <ThemeToggle />
          </div>
          <div className="mb-3 border-t border-border dark:border-stone-800" />
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-subtle font-serif text-sm font-medium text-brand dark:bg-amber-950/40 dark:text-amber-300"
            >
              {initial}
            </span>
            <span
              className="min-w-0 flex-1 truncate text-xs text-fg-muted dark:text-stone-400"
              title={user.email}
            >
              {user.email}
            </span>
            <button
              type="button"
              onClick={handleSignOut}
              disabled={signingOut}
              aria-label={signingOut ? "Signing out…" : "Sign out"}
              title="Sign out"
              className="focus-ring shrink-0 rounded-md p-1.5 text-stone-400 transition-colors duration-fast hover:text-fg disabled:opacity-50 dark:hover:text-stone-100"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: ReactNode;
}

/**
 * Standard page header — title + optional description + right-aligned action
 * area. Sits at the top of every authenticated page for consistent rhythm.
 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="border-b border-stone-200 px-6 py-6 dark:border-stone-800">
      <div className="mx-auto flex max-w-5xl items-start justify-between gap-4">
        <div className="space-y-1.5">
          {/* Display serif H1 — the editorial voice of the page. */}
          <h1 className="font-serif text-3xl font-medium leading-tight tracking-tight text-stone-900 sm:text-4xl dark:text-stone-100">
            {title}
          </h1>
          {description ? (
            <p className="text-sm text-stone-500 dark:text-stone-400">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
    </header>
  );
}

export function PageBody({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-5xl px-6 py-6">{children}</div>;
}
