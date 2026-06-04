import "./globals.css";

import type { Metadata } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { Fraunces } from "next/font/google";

/**
 * 编辑体 display serif —— 与官网一致,仅用于 section 序号/标题。
 */
const fraunces = Fraunces({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  style: ["normal", "italic"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Inalpha · Operator Console",
  robots: { index: false, follow: false }, // 控制台不进搜索引擎
};

/**
 * Root layout —— 持有 <html>/<body> 与字体变量。locale 相关逻辑在 [locale]/layout.tsx。
 * 控制台是动态应用,根 lang 跟随 middleware 协商的默认值即可。
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
