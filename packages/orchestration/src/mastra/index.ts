/**
 * Mastra 实例 —— ``mastra dev`` 自动发现的入口。
 *
 * 启动：
 *
 *   pnpm dev           # http://localhost:4111 playground
 *
 * 需要：
 *
 * - services/data + services/paper 都在跑（D-3 / D-6）
 * - DEEPSEEK_API_KEY 在 .env
 * - JWT_SECRET 跟 services 一致
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

// dev 启动时显式加载 .env（mastra CLI 的 cwd 是 package root）
const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { Mastra } from "@mastra/core/mastra";
import { PinoLogger } from "@mastra/loggers";

import { orchestrator } from "./agents/orchestrator.js";
import { risk } from "./agents/risk.js";
import { trader } from "./agents/trader.js";

export const mastra = new Mastra({
  // D-8a：3 个 agent（orchestrator supervisor + trader + risk）
  // 所有 agent 都在 Mastra 顶层注册，方便 playground 单独调测
  agents: { orchestrator, trader, risk },
  logger: new PinoLogger({ name: "inalpha", level: "info" }),
});
