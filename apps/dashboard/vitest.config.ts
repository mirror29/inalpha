import { defineConfig } from "vitest/config";

/**
 * 最小 vitest 配置 —— 目前只覆盖 server 侧 lib 的纯逻辑单测(如 mastra.ts 的
 * 越权防护 ownsThread)。测试用显式 import(不开 globals),故无需改 tsconfig types。
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
