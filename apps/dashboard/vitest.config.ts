import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/**
 * 最小 vitest 配置 —— 目前只覆盖 server 侧 lib 的纯逻辑单测(如 mastra.ts 的
 * 越权防护 ownsThread)。测试用显式 import(不开 globals),故无需改 tsconfig types。
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
