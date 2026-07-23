import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  output: "export",
  reactStrictMode: true,
  trailingSlash: true,
  experimental: {
    globalNotFound: true,
  },
  images: {
    unoptimized: true,
  },
  // dev-only：用 127.0.0.1 访问时也信任，否则 HMR 跨源 WebSocket 被拦
  // （ws://127.0.0.1:3200/_next/...-hmr failed）。不影响 output:"export" 构建。
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

export default withNextIntl(nextConfig);
