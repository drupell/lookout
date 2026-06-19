import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Spectral } from "next/font/google";
import type { ReactNode } from "react";

import { SessionGuard } from "@/components/SessionGuard";
import { ToastProvider } from "@/components/ui";
import { NO_FLASH_SCRIPT, ThemeProvider } from "@/lib/theme";

import "./globals.css";

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

// Display serif for editorial moments: page/section titles and big numerals
// (stat values, deal scores, prices). Spectral is warm and calm — it reads
// cleanly even on short UI words like "Dashboard". A tight weight range keeps
// the voice consistent: 400 for numerals, 500/600 for titles.
const serif = Spectral({
  subsets: ["latin"],
  variable: "--font-serif",
  display: "swap",
  weight: ["400", "500", "600"],
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Lookout",
  description: "Passive deal monitoring",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${serif.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* No-flash theme guard: stamps `.dark` on <html> before paint so the
            first frame matches the user's stored preference. Must run before
            any styles are applied; placing it inline in <head> guarantees that.
            The body is a tiny string assembled in lib/theme.ts. */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </head>
      <body className="min-h-screen font-sans">
        <ThemeProvider>
          <ToastProvider>
            {/* Listens for global 401s from the API layer → expired-session toast
                + redirect to sign-in, preserving the return URL. */}
            <SessionGuard />
            {children}
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
