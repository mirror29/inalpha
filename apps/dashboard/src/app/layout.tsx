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
 * 防闪烁脚本 —— 首帧绘制前同步设好 data-theme:
 * 读 localStorage(inalpha-theme),无则跟随系统 prefers-color-scheme,兜底 dark。
 * 必须 inline 且在 body 内容之前执行,否则会先暗后亮闪一下。
 */
const THEME_INIT = `(function(){try{var t=localStorage.getItem('inalpha-theme');if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';}var r=document.documentElement;r.setAttribute('data-theme',t);r.style.colorScheme=t;}catch(e){}})();`;

/**
 * Root layout —— 持有 <html>/<body> 与字体变量。locale 相关逻辑在 [locale]/layout.tsx。
 * 控制台是动态应用,根 lang 跟随 middleware 协商的默认值即可。
 * data-theme 默认 dark(终端默认照明),防闪烁脚本会按用户偏好即时覆盖。
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
