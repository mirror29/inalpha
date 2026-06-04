import createMiddleware from "next-intl/middleware";
import { routing } from "@/i18n/routing";

/**
 * next-intl locale 协商。动态应用必须有 proxy(Next 16 前称 middleware)来处理
 * `as-needed` 前缀(官网是静态导出,靠 generateStaticParams,不需要)。
 */
export default createMiddleware(routing);

export const config = {
  // 排除 /api(BFF)、Next 内部资源、静态文件。
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
