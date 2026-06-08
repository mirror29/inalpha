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
 * 防闪烁脚本 —— 首帧绘制前同步设好 data-theme：
 * 读 localStorage(inalpha-theme)，无则跟随系统 prefers-color-scheme，兜底 dark。
 * 必须 inline 且在 body 内容之前执行，否则会先暗后亮闪一下。
 * 与 operator console 共用同一个 STORAGE_KEY，本地两端主题一致。
 */
const THEME_INIT = `(function(){try{var t=localStorage.getItem('inalpha-theme');if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';}var r=document.documentElement;r.setAttribute('data-theme',t);r.style.colorScheme=t;}catch(e){}})();`;

/**
 * Root layout — owns <html> and <body> so the locale-less redirect route
 * at `/` can render. Locale-specific concerns live in `[locale]/layout.tsx`.
 *
 * NOTE: `lang` is hard-coded to "en" here because Next.js SSG (`output: "export"`)
 * cannot vary root `<html>` attributes per locale. Search engines rely on
 * `hreflang` tags (set in `[locale]/layout.tsx` via `alternates.languages`)
 * for language detection, so this has negligible SEO impact.
 *
 * data-theme 默认 dark；防闪烁脚本会按用户偏好（localStorage / 系统）即时覆盖。
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable} ${fraunces.variable}`}
    >
      <body className="min-h-screen bg-bg text-fg antialiased">
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        {children}
      </body>
    </html>
  );
}
