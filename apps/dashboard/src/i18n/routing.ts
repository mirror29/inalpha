import { defineRouting } from "next-intl/routing";

/**
 * 与官网 apps/web 保持一致:en 默认无前缀,zh 走 /zh。
 * 控制台是动态应用,locale 协商靠 middleware(见 src/middleware.ts)。
 */
export const routing = defineRouting({
  locales: ["en", "zh"],
  defaultLocale: "en",
  localePrefix: "as-needed",
});

export type Locale = (typeof routing.locales)[number];
