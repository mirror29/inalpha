import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/**
 * 操作者控制台是**动态** Next 应用(对比官网 apps/web 的 `output: "export"`)。
 * 需要 Route Handler 当 BFF:server 侧持 dev JWT 转发到 python service,浏览器只调同源
 * `/api/*`,从而绕开 python service 未配 CORS + 不把 token 暴露到浏览器。
 * 因此这里**不能**设 `output: "export"`。
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default withNextIntl(nextConfig);
