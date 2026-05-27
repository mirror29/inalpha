import "./globals.css";

import type { Metadata } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { Fraunces } from "next/font/google";

/**
 * Editorial display serif — used for section indices and titles only.
 * Italic by default per the broadsheet aesthetic (DESIGN.md §4).
 */
const fraunces = Fraunces({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  style: ["normal", "italic"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://inalpha.dev"),
};

/**
 * Root layout — owns <html> and <body> so the locale-less redirect route
 * at `/` can render. Locale-specific concerns live in `[locale]/layout.tsx`.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} ${fraunces.variable}`}
    >
      <body className="min-h-screen bg-bg text-fg antialiased">{children}</body>
    </html>
  );
}
